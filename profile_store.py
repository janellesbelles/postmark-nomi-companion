"""Durable resident profile index with isolated per-resident directories."""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import shutil
import time
import uuid


class ProfileStore:
    VERSION = 2

    def __init__(self, data_root):
        self.root = Path(data_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "profiles.json"
        self.residents_root = self.root / "residents"
        self.residents_root.mkdir(parents=True, exist_ok=True)
        if not self.index_path.exists():
            self._migrate_legacy()
        self.index = self._read(self.index_path) if self.index_path.exists() else self._empty_index()
        self._repair_index()

    def activate_default_on_startup(self):
        default = self.index.get("default_profile_id")
        if default in self.index.get("profiles", {}) and self.index.get("active_profile_id") != default:
            self.index["active_profile_id"] = default
            self.save()

    @staticmethod
    def _empty_index():
        return {
            "version": ProfileStore.VERSION,
            "active_profile_id": None,
            "default_profile_id": None,
            "profiles": {},
        }

    @staticmethod
    def _read(path):
        raw = Path(path).read_bytes()
        try:
            return json.loads(raw.decode("utf-8-sig"))
        except UnicodeDecodeError:
            return json.loads(raw.decode("cp1252"))

    @staticmethod
    def _write(path, value):
        path = Path(path)
        for directory_attempt in range(3):
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
            try:
                with temporary.open("w", encoding="utf-8") as stream:
                    json.dump(value, stream, indent=2, ensure_ascii=False)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                for replace_attempt in range(6):
                    try:
                        os.replace(temporary, path)
                        return
                    except PermissionError:
                        if replace_attempt == 5:
                            raise
                        time.sleep(0.05 * (replace_attempt + 1))
            except FileNotFoundError:
                if directory_attempt == 2:
                    raise
                time.sleep(0.05 * (directory_attempt + 1))
            finally:
                if temporary.exists():
                    temporary.unlink()

    def _legacy_handle(self, state):
        counts = {}
        for entry in (state or {}).get("letters", {}).values():
            handle = str((entry.get("fields") or {}).get("to") or "").strip()
            if handle:
                counts[handle] = counts.get(handle, 0) + 1
        return max(counts, key=counts.get) if counts else ""

    def _migrate_legacy(self):
        legacy_settings_path = self.root / "settings.json"
        mail_candidates = sorted(self.root.glob(".*-mail-state.json"))
        event_candidates = sorted(self.root.glob(".*-event-state.json"))
        legacy_mail_path = mail_candidates[0] if mail_candidates else self.root / ".legacy-mail-state.json"
        legacy_event_path = event_candidates[0] if event_candidates else self.root / ".legacy-event-state.json"
        if not any(path.exists() for path in (legacy_settings_path, legacy_mail_path, legacy_event_path)):
            self._write(self.index_path, self._empty_index())
            return

        settings = self._read(legacy_settings_path) if legacy_settings_path.exists() else {}
        state = self._read(legacy_mail_path) if legacy_mail_path.exists() else {
            "version": 1, "seen": [], "letters": {}
        }
        handle = self._legacy_handle(state)
        profile_id = uuid.uuid4().hex
        profile_dir = self.residents_root / profile_id
        profile_dir.mkdir(parents=True, exist_ok=False)

        migrated_settings = dict(settings)
        if handle:
            migrated_settings["resident_handle"] = handle
        self._write(profile_dir / "settings.json", migrated_settings)
        self._write(profile_dir / "mail-state.json", state)
        if legacy_event_path.exists():
            self._write(profile_dir / "event-state.json", self._read(legacy_event_path))

        label = str(settings.get("nomi_name") or handle or "Migrated resident").split(" · ", 1)[0]
        index = self._empty_index()
        index["profiles"][profile_id] = {
            "id": profile_id,
            "handle": handle,
            "nomi_name": label,
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "migrated_from_legacy": True,
        }
        index["active_profile_id"] = profile_id
        index["default_profile_id"] = profile_id
        self._write(self.index_path, index)

    def _repair_index(self):
        changed = False
        if self.index.get("version") != self.VERSION:
            self.index["version"] = self.VERSION
            changed = True
        profiles = self.index.setdefault("profiles", {})
        active = self.index.get("active_profile_id")
        default = self.index.get("default_profile_id")
        if active not in profiles:
            self.index["active_profile_id"] = default if default in profiles else next(iter(profiles), None)
            changed = True
        if default not in profiles:
            self.index["default_profile_id"] = next(iter(profiles), None)
            changed = True
        if changed:
            self.save()

    def save(self):
        self._write(self.index_path, self.index)

    def profiles(self):
        return list(self.index.get("profiles", {}).values())

    def profile(self, profile_id):
        try:
            return self.index["profiles"][profile_id]
        except KeyError:
            raise ValueError("Unknown resident profile.") from None

    def active_id(self):
        return self.index.get("active_profile_id")

    def active(self):
        profile_id = self.active_id()
        return self.profile(profile_id) if profile_id else None

    def profile_dir(self, profile_id):
        self.profile(profile_id)
        return self.residents_root / profile_id

    def settings_path(self, profile_id):
        return self.profile_dir(profile_id) / "settings.json"

    def mail_state_path(self, profile_id):
        return self.profile_dir(profile_id) / "mail-state.json"

    def event_state_path(self, profile_id):
        return self.profile_dir(profile_id) / "event-state.json"

    def read_settings(self, profile_id):
        path = self.settings_path(profile_id)
        return self._read(path) if path.exists() else {}

    def write_settings(self, profile_id, settings):
        # Updates are patches. Omitted options and blank secret controls must
        # never erase durable credentials when another option is edited.
        preserved = self.read_settings(profile_id)
        for key, value in dict(settings).items():
            if key in {"encrypted_key", "encrypted_postmark_key"} and not value:
                continue
            preserved[key] = value
        self._write(self.settings_path(profile_id), preserved)

    def add(self, *, handle, nomi_name, settings, make_default=False):
        handle = str(handle or "").strip()
        if not handle:
            raise ValueError("Postmark resident handle is required.")
        if any(str(profile.get("handle", "")).casefold() == handle.casefold() for profile in self.profiles()):
            raise ValueError("That Postmark resident already has a profile.")
        profile_id = uuid.uuid4().hex
        directory = self.residents_root / profile_id
        directory.mkdir(parents=True, exist_ok=False)
        settings = dict(settings)
        settings["resident_handle"] = handle
        self._write(directory / "settings.json", settings)
        profile = {
            "id": profile_id,
            "handle": handle,
            "nomi_name": str(nomi_name or handle),
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        self.index["profiles"][profile_id] = profile
        self.index["active_profile_id"] = profile_id
        if make_default or not self.index.get("default_profile_id"):
            self.index["default_profile_id"] = profile_id
        self.save()
        return profile_id

    def update(self, profile_id, *, nomi_name, settings, handle=None, make_default=False):
        profile = self.profile(profile_id)
        current_handle = profile.get("handle", "")
        chosen_handle = str(handle if handle is not None else current_handle).strip()
        if not chosen_handle:
            raise ValueError("Postmark resident handle is required.")
        for other in self.profiles():
            if other["id"] != profile_id and str(other.get("handle", "")).casefold() == chosen_handle.casefold():
                raise ValueError("That Postmark resident already has a profile.")
        settings = dict(settings)
        settings["resident_handle"] = chosen_handle
        self.write_settings(profile_id, settings)
        profile["handle"] = chosen_handle
        profile["nomi_name"] = str(nomi_name or chosen_handle)
        if make_default:
            self.index["default_profile_id"] = profile_id
        self.save()

    def set_active(self, profile_id):
        self.profile(profile_id)
        self.index["active_profile_id"] = profile_id
        self.save()

    def set_default(self, profile_id):
        self.profile(profile_id)
        self.index["default_profile_id"] = profile_id
        self.save()

    def remove(self, profile_id, *, delete_state=False):
        self.profile(profile_id)
        directory = (self.residents_root / profile_id).resolve()
        expected_parent = self.residents_root.resolve()
        if directory.parent != expected_parent:
            raise RuntimeError("Unsafe resident-state path.")
        del self.index["profiles"][profile_id]
        if self.index.get("active_profile_id") == profile_id:
            self.index["active_profile_id"] = next(iter(self.index["profiles"]), None)
        if self.index.get("default_profile_id") == profile_id:
            self.index["default_profile_id"] = next(iter(self.index["profiles"]), None)
        self.save()
        if not directory.exists():
            return None
        if delete_state:
            shutil.rmtree(directory)
            return None
        archive_root = self.root / "removed-residents"
        archive_root.mkdir(parents=True, exist_ok=True)
        archived = archive_root / f"{profile_id}-{dt.datetime.now().strftime('%Y%m%d%H%M%S')}"
        shutil.move(str(directory), str(archived))
        return archived
