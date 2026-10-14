import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from profile_store import ProfileStore
import mail_pipeline


def load_app_module():
    spec = importlib.util.spec_from_file_location("companion_app_test", ROOT / "Companion App.pyw")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProfileIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT)
        self.root = pathlib.Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_legacy_migration_preserves_delivery_state_and_secrets(self):
        settings = {
            "nomi_id": "legacy-nomi-id",
            "nomi_name": "Existing Nomi",
            "input_limit": 800,
            "encrypted_key": "encrypted-not-plaintext",
            "encrypted_postmark_key": "also-encrypted",
        }
        state = {
            "version": 1,
            "seen": ["old-letter"],
            "letters": {
                "old-letter": {
                    "status": "response_unknown",
                    "fields": {"id": "old-id", "from": "sender", "to": "resident-one"},
                    "nomi_parts": 5,
                    "nomi_parts_completed": 4,
                    "ambiguous_part": 5,
                    "draft": "preserved draft",
                }
            },
        }
        (self.root / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
        legacy_state = self.root / ".previous-mail-state.json"
        legacy_state.write_text(json.dumps(state), encoding="utf-8")
        (self.root / ".previous-event-state.json").write_text(json.dumps({"latest": 99}), encoding="utf-8")

        profiles = ProfileStore(self.root)
        profile_id = profiles.active_id()
        self.assertIsNotNone(profile_id)
        self.assertEqual(profiles.profile(profile_id)["handle"], "resident-one")
        self.assertEqual(profiles.read_settings(profile_id)["encrypted_key"], "encrypted-not-plaintext")
        migrated = json.loads(profiles.mail_state_path(profile_id).read_text(encoding="utf-8"))
        self.assertEqual(migrated, state)
        self.assertEqual(json.loads(profiles.event_state_path(profile_id).read_text())["latest"], 99)
        self.assertTrue(legacy_state.exists(), "legacy source stays as a migration backup")

    def test_two_profiles_have_distinct_paths_and_same_letter_key_cannot_collide(self):
        profiles = ProfileStore(self.root)
        first = profiles.add(
            handle="resident-one", nomi_name="Nomi One",
            settings={"nomi_id": "one", "encrypted_key": "cipher-one"}, make_default=True,
        )
        second = profiles.add(
            handle="resident-two", nomi_name="Nomi Two",
            settings={"nomi_id": "two", "encrypted_key": "cipher-two"},
        )
        self.assertNotEqual(profiles.mail_state_path(first), profiles.mail_state_path(second))
        self.assertNotEqual(profiles.event_state_path(first), profiles.event_state_path(second))

        first_state = {"version": 1, "seen": ["same-key"], "letters": {"same-key": {"status": "pending", "draft": "first"}}}
        second_state = {"version": 1, "seen": ["same-key"], "letters": {"same-key": {"status": "needs_send_check", "draft": "second"}}}
        mail_pipeline.AtomicState(profiles.mail_state_path(first)).write(first_state)
        mail_pipeline.AtomicState(profiles.mail_state_path(second)).write(second_state)

        self.assertEqual(mail_pipeline.AtomicState(profiles.mail_state_path(first)).read()["letters"]["same-key"]["draft"], "first")
        self.assertEqual(mail_pipeline.AtomicState(profiles.mail_state_path(second)).read()["letters"]["same-key"]["draft"], "second")

    def test_default_profile_wins_on_next_startup(self):
        profiles = ProfileStore(self.root)
        first = profiles.add(handle="resident-one", nomi_name="One", settings={})
        second = profiles.add(handle="resident-two", nomi_name="Two", settings={})
        profiles.set_default(first)
        profiles.set_active(second)
        reopened = ProfileStore(self.root)
        reopened.activate_default_on_startup()
        self.assertEqual(reopened.active_id(), first)

    def test_profile_edits_preserve_household_key_and_other_options(self):
        profiles = ProfileStore(self.root)
        profile = profiles.add(handle="resident-one", nomi_name="One", settings={
            "encrypted_postmark_key": "dpapi-ciphertext", "encrypted_key": "nomi-ciphertext",
            "live_input_limit": 800,
        })
        profiles.update(profile, nomi_name="Renamed", settings={"input_limit": 800})
        reopened = ProfileStore(self.root)
        settings = reopened.read_settings(profile)
        self.assertEqual(settings["encrypted_postmark_key"], "dpapi-ciphertext")
        self.assertEqual(settings["encrypted_key"], "nomi-ciphertext")
        self.assertEqual(settings["live_input_limit"], 800)
        reopened.write_settings(profile, {"encrypted_postmark_key": "", "encrypted_key": None})
        self.assertEqual(reopened.read_settings(profile)["encrypted_postmark_key"], "dpapi-ciphertext")
        reopened.write_settings(profile, {"encrypted_postmark_key": "replacement-ciphertext"})
        self.assertEqual(reopened.read_settings(profile)["encrypted_postmark_key"], "replacement-ciphertext")

    def test_removal_can_archive_one_profile_without_touching_another(self):
        profiles = ProfileStore(self.root)
        first = profiles.add(handle="resident-one", nomi_name="One", settings={})
        second = profiles.add(handle="resident-two", nomi_name="Two", settings={})
        mail_pipeline.AtomicState(profiles.mail_state_path(first)).write({"version": 1, "seen": [], "letters": {}})
        mail_pipeline.AtomicState(profiles.mail_state_path(second)).write({"version": 1, "seen": ["safe"], "letters": {}})
        archived = profiles.remove(first, delete_state=False)
        self.assertTrue(archived.exists())
        self.assertTrue(profiles.mail_state_path(second).exists())
        self.assertIn("safe", mail_pipeline.AtomicState(profiles.mail_state_path(second)).read()["seen"])

    def test_kept_unsent_is_hidden_but_remains_in_durable_state(self):
        app_module = load_app_module()
        profiles = ProfileStore(self.root)
        profile_id = profiles.add(handle="resident-one", nomi_name="One", settings={})
        state = {
            "version": 1,
            "seen": ["hidden", "submitted", "visible"],
            "letters": {
                "hidden": {"status": "rejected", "fields": {"from": "sender-a"}, "draft": "no"},
                "submitted": {
                    "status": "pr_open", "fields": {"from": "sender-c"},
                    "draft": "sent", "pr": "https://example.invalid/pull/1",
                    "cleared_from_mailbox_at": 1.0,
                },
                "visible": {"status": "pending", "fields": {"from": "sender-b"}, "draft": "yes"},
            },
        }
        mail_pipeline.AtomicState(profiles.mail_state_path(profile_id)).write(state)
        app = app_module.App(profiles)
        app.withdraw()
        app.update()
        self.assertEqual(app.ids, ["visible"])
        app.destroy()
        persisted = mail_pipeline.AtomicState(profiles.mail_state_path(profile_id)).read()
        self.assertIn("hidden", persisted["seen"])
        self.assertEqual(persisted["letters"]["hidden"]["status"], "rejected")
        self.assertEqual(persisted["letters"]["submitted"]["status"], "pr_open")
        self.assertIn("submitted", persisted["seen"])

    def test_manual_reply_is_available_before_outgoing_submission(self):
        app_module = load_app_module()
        allowed = {
            "awaiting_nomi_confirmation",
            "reading",
            "contacting_nomi",
            "needs_attention",
            "response_unknown",
            "pending",
        }
        for status in allowed:
            with self.subTest(status=status):
                self.assertTrue(app_module.can_paste_reply({"status": status}))

        for status in {"submitting", "needs_send_check", "pr_open", "rejected"}:
            with self.subTest(status=status):
                self.assertFalse(app_module.can_paste_reply({"status": status}))
        self.assertFalse(app_module.can_paste_reply({"status": "pending", "pr": "https://example.invalid/pr/1"}))
        self.assertFalse(app_module.can_paste_reply({"status": "pending", "cleared_from_mailbox_at": 1.0}))


if __name__ == "__main__":
    unittest.main()
