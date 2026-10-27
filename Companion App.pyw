"""Postmark ↔ Nomi Companion: multi-resident local desktop application."""
from __future__ import annotations

import base64
import ctypes
import datetime as dt
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
import uuid
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
import webbrowser

import event_bridge
import github_mailer
import mail_pipeline
import nomi_client
import desk_themes
import lamplit_ui
from profile_store import ProfileStore


APPDATA_ROOT = Path(os.environ.get("APPDATA", str(Path.home() / ".local/share")))
DATA = APPDATA_ROOT / "PostmarkNomiCompanion"
BG = "#171526"
CARD = "#242139"
INK = "#f2edff"
MUTED = "#bab0d1"
CYAN = "#99ffff"
LILAC = "#d9b8ff"
ACTIVE = "#62377d"

# A reply copied from the normal Nomi chat can safely replace any inbound
# delivery workflow that has not begun outgoing submission. This includes a
# letter still waiting at the human confirmation gate: the human may already
# have delivered it outside the app and only need to recover Nomi's reply.
MANUAL_REPLY_STATUSES = {
    "awaiting_nomi_confirmation",
    "reading",
    "contacting_nomi",
    "needs_attention",
    "response_unknown",
    "pending",
}


def can_paste_reply(entry):
    """Return whether manual reply recovery is safe for this mailbox entry."""
    return bool(
        entry
        and entry.get("status") in MANUAL_REPLY_STATUSES
        and not entry.get("pr")
        and not entry.get("cleared_from_mailbox_at")
        and not entry.get("archived_at")
    )


class Blob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def crypt_key(value, decrypt=False):
    """Windows DPAPI: ciphertext can only be opened by this Windows user."""
    if os.name != "nt":
        raise RuntimeError("Remembering keys requires Windows DPAPI.")
    raw = base64.b64decode(value) if decrypt else value.encode("utf-8")
    buffer = ctypes.create_string_buffer(raw)
    incoming = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    outgoing = Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    function.argtypes = [
        ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(Blob),
    ]
    function.restype = ctypes.c_int
    if not function(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        result = ctypes.string_at(outgoing.data, outgoing.size)
        return result.decode("utf-8") if decrypt else base64.b64encode(result).decode("ascii")
    finally:
        kernel32.LocalFree(ctypes.cast(outgoing.data, ctypes.c_void_p))


def read_secret(config, field):
    encrypted = config.get(field)
    if not encrypted:
        return ""
    try:
        return crypt_key(encrypted, decrypt=True)
    except Exception:
        return ""


class ResidentService:
    """One immutable resident namespace used for the life of an operation."""
    def __init__(self, profiles, profile_id):
        self.profiles = profiles
        self.profile_id = profile_id
        self.profile = dict(profiles.profile(profile_id))
        self.directory = profiles.profile_dir(profile_id)
        self.config_path = profiles.settings_path(profile_id)
        self.state_path = profiles.mail_state_path(profile_id)
        self.event_state_path = profiles.event_state_path(profile_id)
        self.config = profiles.read_settings(profile_id)
        self.handle = str(self.profile.get("handle") or self.config.get("resident_handle") or "").strip()

    @property
    def name(self):
        return str(self.profile.get("nomi_name") or self.handle or "Unconfigured resident")

    @property
    def input_limit(self):
        return int(self.config.get("input_limit", 800))

    @property
    def live_input_limit(self):
        return int(self.config.get("live_input_limit", 800))

    def nomi_key(self):
        return read_secret(self.config, "encrypted_key")

    def postmark_key(self):
        return read_secret(self.config, "encrypted_postmark_key")

    def connected(self):
        return bool(self.handle and self.config.get("nomi_id") and self.nomi_key())

    def _send_nomi(self, message):
        return nomi_client.send_chat(
            self.nomi_key(), self.config.get("nomi_id", ""), message,
            timeout=int(self.config.get("nomi_reply_timeout", 180)),
        )

    def pipeline(self):
        return mail_pipeline.MailPipeline(
            resident_handle=self.handle,
            state_path=self.state_path,
            nomi_send=self._send_nomi,
            message_limit=self.input_limit,
            part_pause=float(self.config.get("part_pause", 4)),
        )

    def initialized(self):
        return self.state_path.exists()

    def initialize(self):
        if not self.connected():
            raise RuntimeError("Complete this resident's Postmark handle and Nomi connection first.")
        return self.pipeline().initialize(
            import_existing=self.config.get("initial_mail_mode") == "import"
        )

    def poll(self):
        if not self.connected():
            raise RuntimeError("Complete this resident's Nomi connection first.")
        return self.pipeline().poll()

    def confirm_delivery(self, key):
        return self.pipeline().confirm_delivery(key)

    def entries(self):
        if not self.state_path.exists():
            return {}
        pipeline = self.pipeline()
        state = pipeline.state.read()
        changed = False
        for entry in state.get("letters", {}).values():
            if entry.get("status") != "needs_attention":
                continue
            error_text = " ".join(str(entry.get(key, "")) for key in ("error", "timeout_detail")).lower()
            if mail_pipeline.is_ambiguous_nomi_error(RuntimeError(error_text)):
                entry["status"] = "response_unknown"
                entry.setdefault(
                    "ambiguous_part",
                    int(entry.get("nomi_parts_completed", 0) or 0) + 1,
                )
                entry["error"] = (
                    "The Nomi request may already have arrived. Check the normal Nomi chat, "
                    "then paste the reply or explicitly resend after checking."
                )
                changed = True
        if changed:
            pipeline.state.write(state)
        return state.get("letters", {})

    def save_draft(self, key, text):
        return self.pipeline().save_draft(key, text)

    def reject(self, key):
        return self.pipeline().reject(key)

    def clear_submitted(self, key):
        return self.pipeline().clear_submitted(key)

    def retry_letter(self, key, *, next_part=None, human_checked_chat=False):
        return self.pipeline().continue_after_definite_failure(
            key, next_part=next_part, human_checked_chat=human_checked_chat
        )

    def resend_after_timeout(self, key, *, next_part=None):
        return self.pipeline().resend_after_human_check(
            key, human_checked_chat=True, next_part=next_part
        )

    def recover_reply(self, key, text):
        return self.pipeline().recover_reply(key, text)

    def send_approved(self, key, exact_text):
        return github_mailer.submit_approved(
            self.pipeline(), key, exact_text,
            expected_github_account=self.config.get("github_account") or None,
        )

    def event_cursor(self):
        if not self.event_state_path.exists():
            return None
        try:
            value = json.loads(self.event_state_path.read_text(encoding="utf-8")).get("latest")
            return value if isinstance(value, (int, float)) else None
        except Exception:
            return None

    def save_event_cursor(self, latest):
        if not isinstance(latest, (int, float)):
            return
        state = self.live_state()
        old = state.get("latest")
        state["latest"] = max(old, latest) if isinstance(old, (int, float)) else latest
        mail_pipeline.AtomicState(self.event_state_path).write(state)

    def accept_live_chatter(self, result, *, baseline=False):
        state = self.live_state()
        fresh, seen = event_bridge.remember_voices(result, list(state.get("heard_ids") or []), baseline=baseline)
        old_cursor = state.get("latest")
        if isinstance(old_cursor, (int, float)):
            fresh["voices"] = [voice for voice in fresh.get("voices", [])
                               if not isinstance(voice.get("at_ms"), (int, float)) or voice["at_ms"] > old_cursor]
        state["heard_ids"] = seen
        latest = result.get("latest")
        if isinstance(latest, (int, float)):
            old = state.get("latest")
            state["latest"] = max(old, latest) if isinstance(old, (int, float)) else latest
        mail_pipeline.AtomicState(self.event_state_path).write(state)
        return fresh

    def live_state(self):
        if self.event_state_path.exists():
            return mail_pipeline.AtomicState(self.event_state_path).read()
        return {}

    def save_live_speech(self, speech):
        state = self.live_state()
        state["speech"] = speech
        mail_pipeline.AtomicState(self.event_state_path).write(state)

    def save_live_nomi(self, request):
        state = self.live_state()
        state["nomi"] = request
        mail_pipeline.AtomicState(self.event_state_path).write(state)

    def end_live_continuation(self):
        """An explicit idle pause/close ends catch-up; unfinished work stays safe."""
        state = self.live_state()
        request = state.get('nomi') or {}
        if request.get('status') == 'resolved' and not request.get('packets'):
            request.pop('continue_from_cursor', None)
            state['nomi'] = request
            mail_pipeline.AtomicState(self.event_state_path).write(state)

    def record_private_live_reply(self, request, reply):
        state = self.live_state()
        request_id = request.get('request_id')
        packets = request.get('packets') or []
        packet = packets[0] if packets else None
        history = state.setdefault('private_reply_history', [])
        # Recording before consumption makes restart safe and retains the reply.
        if not any(item.get('request_id') == request_id and item.get('packet') == packet and item.get('reply') == reply for item in history):
            history.append({'request_id':request_id, 'packet':packet, 'reply':reply,
                'disposition':'kept_private', 'recorded_at':dt.datetime.now(dt.timezone.utc).isoformat()})
        mail_pipeline.AtomicState(self.event_state_path).write(state)

    def keep_live_private_and_reset(self, reply=''):
        """Archive rather than reconcile an interrupted private Nomi exchange.

        One atomic write preserves uncertainty and unsent packets in history,
        removes them from active work, and never makes a public API request.
        """
        state = self.live_state()
        speech = state.get('speech') or {}
        if speech.get('status') == 'unknown':
            raise ValueError('Public delivery is uncertain. This action cannot unlock a possible public post.')
        request = state.get('nomi') or {}
        if not request.get('packets') and request.get('status') not in {'awaiting_reply','response_unknown','reply_ready','request_rejected'}:
            raise ValueError('There is no unfinished live exchange to keep private.')
        archive = {'nomi':request, 'speech':dict(speech),
                   'disposition':'abandoned_private_no_resend',
                   'recorded_at':dt.datetime.now(dt.timezone.utc).isoformat()}
        if reply:
            archive['manually_preserved_reply'] = reply
        state.setdefault('abandoned_live_history', []).append(archive)
        state['nomi'] = {'status':'abandoned_private','packets':[]}
        if speech.get('status') == 'proposed':
            state['speech'] = dict(speech, status='kept_private')
            state.setdefault('speech_history', []).append(dict(state['speech']))
        mail_pipeline.AtomicState(self.event_state_path).write(state)

    def recover_live_reply(self, text):
        """Recover a private Nomi result without sending or clearing public locks."""
        state = self.live_state()
        if (state.get("speech") or {}).get("status") == "unknown":
            raise ValueError("Public speech delivery is uncertain. Check the World; pasting cannot unlock or resend it.")
        request = state.get("nomi") or {}
        if request.get("status") not in {"awaiting_reply", "response_unknown", "reply_ready"}:
            raise ValueError("There is no interrupted Nomi response to recover for this resident.")
        text = (text or "").strip()
        event_bridge.speech_from_nomi(text)
        if not text:
            raise ValueError("Paste SAY: followed by the public words, or PASS to stay quiet.")
        request.update(status="reply_ready", reply=text, recovered_manually=True, continue_from_cursor=True)
        request.pop("error", None)
        state["nomi"] = request
        mail_pipeline.AtomicState(self.event_state_path).write(state)
        return request


class LiveEventsWindow(tk.Toplevel):
    def __init__(self, app, service):
        super().__init__(app)
        self.app = app
        self.service = service
        self.profile_id = service.profile_id
        self.running = False
        self.closing = False
        self.recovering_live = False
        self.stop_event = threading.Event()
        self.tasks = queue.Queue()
        self.calendar_events = []
        self.mode = tk.StringVar(value="Map hangout")
        self.speech_requests = queue.Queue()
        self.session_context = "Map hangout"
        self.actual_place = ""
        self.title(f"Live Map & Events — {service.name}")
        self.geometry("930x690")
        self.minsize(760, 570)
        self.configure(bg=BG)
        self.protocol("WM_DELETE_WINDOW", self.close)

        lamplit_ui.build_live(self, service)

        self.drain_after = self.after(100, self.drain)
        self.refresh_calendar()
        previous = service.live_state().get("speech") or {}
        if previous.get("status") in {"proposed", "unknown"}:
            self.log(f"Previous public speech ({previous['status']}): {previous.get('text', '')}")
            self.log("This saved proposal is not posted automatically. If delivery was unknown, check the World before speaking again.")
        request = service.live_state().get("nomi") or {}
        if request.get("status") in {"awaiting_reply", "response_unknown"}:
            self.log("A Nomi response is uncertain. Check normal Nomi chat and use Paste Nomi response. The saved packet will not be resent.")
        elif request.get("status") == "reply_ready":
            self.log("A saved Nomi response awaits review. Start listening to review it before any new chatter.")
        elif request.get("status") == "request_rejected":
            self.log(request.get('error', 'Nomi rejected the saved request.'))
            self.log('No reply is expected for this rejected request. Retry requires your explicit approval.')
        self.update_live_recovery()

    def _button(self, parent, text, command, color):
        return tk.Button(
            parent, text=text, command=command, bg=color,
            fg=BG if color in (CYAN, LILAC) else INK, relief="flat", bd=0,
            padx=12, pady=9, cursor="hand2", font=("Segoe UI", 10, "bold"),
        )

    def log(self, text):
        self.transcript.configure(state="normal")
        self.transcript.insert("end", f"[{dt.datetime.now().strftime('%H:%M')}] {text}\n\n")
        self.transcript.see("end")
        self.transcript.configure(state="disabled")

    def refresh_calendar(self):
        self.status.set("Reading Postmark’s public calendar…")
        self.refresh_btn.configure(state="disabled")
        def run():
            try:
                _raw, rows = event_bridge.current_calendar()
                self.tasks.put(("calendar", rows))
            except Exception as error:
                self.tasks.put(("error", str(error)))
        threading.Thread(target=run, daemon=True).start()

    def show_event(self, _event=None):
        selected = self.event_list.curselection()
        if not selected:
            return
        event = self.calendar_events[selected[0]]
        place = event.get("place") or {}
        invitation = str(event.get("invitation") or "").strip()
        if len(invitation) > 190:
            invitation = invitation[:187].rstrip() + "…"
        self.event_detail.set(
            f"{event.get('phase', 'announced')} · {place.get('name') or place.get('mark') or 'place not listed'}\n"
            f"{event.get('starts') or event.get('doors_open') or 'time not listed'}"
            + (f"\n\n{invitation}" if invitation else "")
            + "\n\nSelecting an event never moves the resident."
        )

    def toggle(self):
        if self.running:
            self.service.end_live_continuation()
            self.stop_event.set()
            self.status.set("Pausing after the current request…")
            self.toggle_btn.configure(state="disabled")
            self.ask_btn.configure(state="disabled")
            return
        state = self.service.live_state()
        if (state.get("speech") or {}).get("status") == "unknown":
            messagebox.showinfo("Check public speech", "A public message may already have posted. Check the World before any further speech. Paste recovery cannot unlock this send.", parent=self)
            return
        if (state.get("nomi") or {}).get("status") in {"awaiting_reply", "response_unknown"}:
            messagebox.showinfo("Recover Nomi response", "Check this resident's normal Nomi chat, then use Paste Nomi response. The saved chatter will not be resent.", parent=self)
            return
        retry_rejected = (state.get('nomi') or {}).get('status') == 'request_rejected'
        if retry_rejected:
            request = state['nomi']
            packet = (request.get('packets') or [''])[0]
            if not messagebox.askyesno(
                'Retry rejected Nomi request?',
                f"{self.service.name} / @{self.service.handle}\n{request.get('error', '')}\n\n"
                f'Retry this exact saved packet? It will not be resized or skipped.\n\n{packet}', parent=self):
                return
        if not self.service.connected() or not self.service.postmark_key():
            messagebox.showinfo(
                "Connection incomplete",
                "Edit this resident and add both the Nomi connection and Postmark household key.",
                parent=self,
            )
            return
        context = "Map hangout"
        if self.mode.get() == "Event":
            selected = self.event_list.curselection()
            if not selected:
                messagebox.showinfo("Choose an event", "Select an event from the calendar first.", parent=self)
                return
            event = self.calendar_events[selected[0]]
            context = f"Event: {event.get('title', 'Untitled')}"
        if not messagebox.askyesno(
            "Let this Nomi mingle?",
            f"{context}\nACTIVE RESIDENT: {self.service.name} / @{self.service.handle}\n\n"
            f"Nearby chatter will be sent to this Nomi. Every public message needs your approval. "
            "Listening follows where the resident actually stands; selecting an event does not move them.\n\nStart?",
            parent=self,
        ):
            return
        self.running = True
        self.paste_live_btn.configure(state="disabled")
        self.speech_requests = queue.Queue()
        self.actual_place = ""
        self.session_context = context
        self.stop_event.clear()
        self.mode_picker.configure(state="disabled")
        self.event_list.configure(state="disabled")
        self.refresh_btn.configure(state="disabled")
        self.ask_btn.configure(state="normal")
        self.toggle_btn.configure(text="Pause", bg=CYAN)
        self.log("Relay on. Listening where this resident currently stands.")
        self.status.set(f"{context}: listening nearby. Every public message needs approval.")
        threading.Thread(target=lambda: self.relay_loop(retry_rejected=retry_rejected), daemon=True).start()

    def update_live_recovery(self):
        state = self.service.live_state()
        request = state.get("nomi") or {}
        recoverable = request.get("status") in {"awaiting_reply", "response_unknown", "reply_ready"}
        locked = (state.get("speech") or {}).get("status") == "unknown"
        unfinished = bool(request.get('packets')) or request.get('status') in {'awaiting_reply','response_unknown','reply_ready','request_rejected'}
        self.keep_private_btn.configure(state='normal' if unfinished and not locked and not self.running else 'disabled')
        self.paste_live_btn.configure(state="normal" if recoverable and not locked and not self.running else "disabled")
        if not self.running and not locked:
            self.toggle_btn.configure(text="Retry rejected request…" if request.get('status') == 'request_rejected' else
                                      "Resume listening" if request.get("status") in {"queued", "reply_ready"} or request.get('continue_from_cursor') else "Start listening")
        lamplit_ui.sync_live(self)

    def keep_private_and_reset(self):
        if self.running:
            return
        dialog = tk.Toplevel(self)
        dialog.title(f'Keep private — {self.service.name}')
        dialog.geometry('650x460')
        dialog.minsize(480,360)
        dialog.configure(bg=CARD)
        dialog.columnconfigure(0,weight=1)
        dialog.rowconfigure(1,weight=1)
        dialog.transient(self)
        dialog.grab_set()
        tk.Label(dialog,text=f'{self.service.name} / @{self.service.handle}\nKeep this unfinished exchange private and skip its remaining queued packets?\nNothing will be posted or resent. Uncertain Nomi delivery stays recorded as uncertain.\nOptional: paste the full reply below to preserve it, even if over 500 characters.\nAfter saving, Start listening begins from now, not from the missed chatter.',
                 bg=CARD,fg=INK,justify='left',wraplength=440).grid(row=0,column=0,columnspan=2,sticky='ew',padx=16,pady=12)
        box = tk.Text(dialog,wrap='word',bg=BG,fg=INK,insertbackground=CYAN,height=1,width=1)
        box.grid(row=1,column=0,sticky='nsew',padx=(16,0))
        scroll = ttk.Scrollbar(dialog,orient='vertical',command=box.yview)
        scroll.grid(row=1,column=1,sticky='ns',padx=(0,16))
        box.configure(yscrollcommand=scroll.set)
        saved = (self.service.live_state().get('nomi') or {}).get('reply','')
        box.insert('1.0',saved)
        notice = tk.StringVar()
        tk.Label(dialog,textvariable=notice,bg=CARD,fg=LILAC,wraplength=440).grid(row=2,column=0,columnspan=2,sticky='ew',padx=16)
        actions = tk.Frame(dialog,bg=CARD)
        actions.grid(row=3,column=0,columnspan=2,sticky='ew',padx=16,pady=12)
        def save():
            if self.running:
                return
            try:
                self.service.keep_live_private_and_reset(box.get('1.0','end-1c'))
            except Exception as error:
                notice.set(str(error))
                return
            dialog.destroy()
            self.log('Unfinished live exchange archived privately. No post or resend. Start listening will skip paused chatter.')
            self.status.set('Kept private. Still paused. Start listening to begin from now.')
            self.update_live_recovery()
        tk.Button(actions,text='Cancel',command=dialog.destroy).pack(side='left')
        tk.Button(actions,text='Keep private — no posting',command=save).pack(side='right')
        dialog.bind('<Escape>',lambda _event:dialog.destroy())

    def paste_live_reply(self):
        if self.running:
            return
        dialog = tk.Toplevel(self)
        dialog.title(f"Paste Nomi response — {self.service.name}")
        dialog.geometry("650x440")
        dialog.minsize(480, 320)
        dialog.configure(bg=CARD)
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(1, weight=1)
        dialog.transient(self)
        dialog.grab_set()
        heading = tk.Label(dialog, text=f"{self.service.name} / @{self.service.handle}\nPaste SAY: with the exact public words, or PASS.\nSave response (Ctrl+Enter) opens public approval; listening stays paused.",
                           bg=CARD, fg=INK, justify="left", wraplength=600)
        heading.grid(row=0, column=0, columnspan=2, sticky="ew", padx=18, pady=12)
        box = tk.Text(dialog, wrap="word", undo=True, height=1, width=1,
                      bg=BG, fg=INK, insertbackground=CYAN)
        box.grid(row=1, column=0, sticky="nsew", padx=(18, 0))
        scroll = ttk.Scrollbar(dialog, orient="vertical", command=box.yview)
        scroll.grid(row=1, column=1, sticky="ns", padx=(0, 18))
        box.configure(yscrollcommand=scroll.set)
        notice = tk.StringVar()
        notice_label = tk.Label(dialog, textvariable=notice, wraplength=600, bg=CARD, fg=LILAC)
        notice_label.grid(row=2, column=0, columnspan=2, sticky="ew", padx=18)
        actions = tk.Frame(dialog, bg=CARD)
        actions.grid(row=3, column=0, columnspan=2, sticky="ew", padx=18, pady=12)
        def resize(event):
            if event.widget == dialog:
                heading.configure(wraplength=max(200, event.width - 36))
                notice_label.configure(wraplength=max(200, event.width - 36))
        dialog.bind("<Configure>", resize)
        def paste():
            try:
                value = dialog.clipboard_get()
                box.delete("1.0", "end")
                box.insert("1.0", value)
            except tk.TclError:
                notice.set("The clipboard does not contain text.")
        def save(_event=None):
            try:
                self.service.recover_live_reply(box.get("1.0", "end-1c"))
            except Exception as error:
                notice.set(str(error))
                return
            dialog.destroy()
            self.running = True
            self.recovering_live = True
            self.stop_event.clear()
            self.paste_live_btn.configure(state="disabled")
            self.toggle_btn.configure(text="Pause", state="normal")
            self.mode_picker.configure(state="disabled")
            self.event_list.configure(state="disabled")
            self.refresh_btn.configure(state="disabled")
            self.status.set("Reviewing the pasted Nomi response. Listening stays paused.")
            threading.Thread(target=lambda: self.relay_loop(recovery_only=True), daemon=True).start()
        tk.Button(actions, text="Paste clipboard", command=paste).pack(side="left")
        tk.Button(actions, text="Save response", command=save).pack(side="right")
        tk.Button(actions, text="Cancel", command=dialog.destroy).pack(side="right", padx=8)
        dialog.bind("<Control-Return>", save)
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        box.focus_set()

    def ask_to_speak(self):
        if self.running and not self.stop_event.is_set():
            self.speech_requests.put(True)
            self.ask_btn.configure(state="disabled")
            self.status.set(f"Asking {self.service.name} for their own public words…")

    def approve_live_speech(self, speech):
        decision = {"ready": threading.Event(), "approved": False, "speech": speech}
        self.tasks.put(("approval", decision))
        while not decision["ready"].wait(0.1):
            if self.stop_event.is_set():
                return False
        return decision["approved"] and not self.stop_event.is_set()

    def confirm_public_speech(self, speech):
        dialog = tk.Toplevel(self)
        dialog.title(f'Approve Nomi response — {self.service.name}')
        dialog.geometry('640x380')
        dialog.minsize(480,280)
        dialog.configure(bg=CARD)
        dialog.transient(self)
        dialog.attributes('-topmost',True)
        dialog.columnconfigure(0,weight=1)
        dialog.rowconfigure(1,weight=1)
        tk.Label(dialog,text=f'POST AS: {self.service.name} / @{self.service.handle}\nPost these exact Nomi words publicly where this resident stands?',
            bg=CARD,fg=INK,wraplength=440,justify='left').grid(row=0,column=0,columnspan=2,sticky='w',padx=18,pady=14)
        preview = tk.Text(dialog,height=1,width=1,wrap='word',bg=BG,fg=INK,padx=12,pady=10)
        preview.insert('1.0',speech)
        preview.configure(state='disabled')
        preview.grid(row=1,column=0,sticky='nsew',padx=(18,0))
        scroll = ttk.Scrollbar(dialog,orient='vertical',command=preview.yview)
        scroll.grid(row=1,column=1,sticky='ns',padx=(0,18))
        preview.configure(yscrollcommand=scroll.set)
        actions = tk.Frame(dialog,bg=CARD)
        actions.grid(row=2,column=0,columnspan=2,sticky='ew',padx=18,pady=14)
        result = {'approved':False}
        def decide(approved=False):
            result['approved'] = approved
            dialog.destroy()
        tk.Button(actions,text='Keep private',command=decide).pack(side='left')
        tk.Button(actions,text='Approve & speak',command=lambda:decide(True)).pack(side='right')
        dialog.protocol('WM_DELETE_WINDOW',decide)
        dialog.bind('<Escape>',lambda _event:decide())
        dialog.grab_set()
        dialog.lift()
        dialog.focus_force()
        self.wait_window(dialog)
        return result['approved']

    def relay_loop(self, recovery_only=False, retry_rejected=False):
        key = self.service.postmark_key()
        handle = self.service.handle
        cursor = self.service.event_cursor()
        pending = []
        last_spoke_at = 0.0
        try:
            initial = self.service.live_state()
            if (initial.get("speech") or {}).get("status") == "unknown":
                raise RuntimeError("Public delivery is unknown. Check the World before continuing.")
            if (initial.get("nomi") or {}).get("status") in {"awaiting_reply", "response_unknown"}:
                raise RuntimeError("Nomi delivery is unknown. Check normal chat and paste the response.")
            if (initial.get('nomi') or {}).get('status') == 'request_rejected':
                if not retry_rejected or recovery_only:
                    raise RuntimeError('Nomi rejected the saved request. Explicit retry approval is required.')
            continuing = bool((initial.get('nomi') or {}).get('continue_from_cursor') or
                              (initial.get('nomi') or {}).get('packets'))
            if not recovery_only and not continuing:
                # A new session starts at the present room boundary. Never feed
                # the server's initial history snapshot back to Nomi.
                baseline = event_bridge.listen(key, handle, since=cursor, wait=1)
                self.service.accept_live_chatter(baseline, baseline=True)
                cursor = self.service.event_cursor()
                self.tasks.put(("status", "Listening from now. Earlier room history was skipped."))
            elif not recovery_only:
                if retry_rejected:
                    # Preserve the explicit-retry reconnect gate, but collect
                    # new chatter rather than discarding it as a baseline.
                    reconnected = event_bridge.listen(key, handle, since=cursor, wait=1)
                    fresh = self.service.accept_live_chatter(reconnected)
                    cursor = self.service.event_cursor()
                    pending.extend(event_bridge.other_voices(fresh, handle))
                self.tasks.put(('status', 'Resuming from the saved listening cursor. New chatter during recovery is not skipped.'))
            while not self.stop_event.is_set():
                saved = self.service.live_state()
                if (saved.get("speech") or {}).get("status") == "unknown":
                    raise RuntimeError("Public delivery is unknown. Check the World; no automatic resend is allowed.")
                request = saved.get("nomi") or {}
                if request.get('status') == 'request_rejected':
                    if not retry_rejected:
                        raise RuntimeError('Nomi rejected the saved request. Explicit retry approval is required.')
                    request['status'] = 'queued'
                    self.service.save_live_nomi(request)
                    retry_rejected = False
                if request.get("status") in {"awaiting_reply", "response_unknown"}:
                    raise RuntimeError("Nomi delivery is unknown. Check normal chat and paste the response; the packet will not be resent.")
                opening = False
                try:
                    self.speech_requests.get_nowait()
                    opening = True
                except queue.Empty:
                    pass
                if not request.get("packets") and not pending and not opening:
                    if recovery_only:
                        break
                    heard = event_bridge.listen(key, handle, since=cursor, wait=5)
                    if self.stop_event.is_set():
                        break
                    where = heard.get("where") or {}
                    place = str(where.get("place") or "")
                    if place and place != self.actual_place:
                        self.actual_place = place
                        self.tasks.put(("place", place))
                    heard = self.service.accept_live_chatter(heard)
                    cursor = self.service.event_cursor()
                    pending.extend(event_bridge.other_voices(heard, handle))
                    if not pending:
                        self.tasks.put(("status", "Listening. The room is quiet."))
                        continue
                if opening:
                    voices = []
                else:
                    voices, pending = pending, []
                self.tasks.put(("heard", voices))
                context = f"{self.session_context}; at {self.actual_place}" if self.actual_place else self.session_context
                packets = request.get("packets") or ([event_bridge.opening_packet(self.service.live_input_limit, context=context)]
                           if opening else event_bridge.chatter_packets(voices, self.service.live_input_limit, context=context))
                if not request.get("packets"):
                    request = {"status": "queued", "packets": packets, "context": context}
                    self.service.save_live_nomi(request)
                for packet in packets:
                    if self.stop_event.is_set():
                        break
                    if request.get("status") == "reply_ready":
                        reply = request["reply"]
                    else:
                        request.update(status="awaiting_reply", request_id=str(uuid.uuid4()))
                        self.service.save_live_nomi(request)
                        try:
                            self.tasks.put(('status', f'Sending saved packet: {len(packet)} characters; current Live limit: {self.service.live_input_limit}. Saved packets keep their original size.'))
                            reply, _reply_id = self.service._send_nomi(packet)
                        except nomi_client.RejectedNomiRequest as error:
                            request.update(status='request_rejected', error=str(error), continue_from_cursor=True)
                            self.service.save_live_nomi(request)
                            raise
                        except Exception as error:
                            request.update(status="response_unknown", error=str(error), continue_from_cursor=True)
                            self.service.save_live_nomi(request)
                            raise
                        request.update(status="reply_ready", reply=reply)
                        self.service.save_live_nomi(request)
                    self.tasks.put(("can_ask", None))
                    if self.stop_event.is_set():
                        break
                    if event_bridge.partial_chatter_packet(packet):
                        self.tasks.put(("quiet", "Chatter continues in the next part; no public reply is proposed yet."))
                        self.finish_live_reply(request)
                        if recovery_only:
                            break
                        continue
                    try:
                        speech = event_bridge.speech_from_nomi(reply)
                    except ValueError as error:
                        if not (reply or '').strip().casefold().startswith('say:'):
                            # Definite unmarked responses stay private; no resend
                            # and no public proposal. Consume only this packet.
                            self.service.record_private_live_reply(request, reply)
                            self.tasks.put(('private', f'{self.service.name} replied privately; listening can continue: {reply}'))
                            self.finish_live_reply(request)
                            if recovery_only:
                                break
                            continue
                        self.tasks.put(("private", f"{error} Draft kept private: {reply}"))
                        raise RuntimeError("Nomi's response needs manual review. Use Paste Nomi response with SAY: or PASS.") from error
                    if speech is None:
                        self.tasks.put(("quiet", f"{self.service.name} stayed quiet."))
                        self.finish_live_reply(request)
                        if recovery_only:
                            break
                        continue
                    previous = (self.service.live_state().get("speech") or {})
                    if previous.get("request_id") == request.get("request_id") and previous.get("status") in {"confirmed", "kept_private"}:
                        self.finish_live_reply(request)
                        if recovery_only:
                            break
                        continue
                    proposal = {"text": speech, "nonce": str(uuid.uuid4()), "status": "proposed", "request_id": request.get("request_id")}
                    self.service.save_live_speech(proposal)
                    if not self.approve_live_speech(speech):
                        if self.stop_event.is_set():
                            break
                        proposal["status"] = "kept_private"
                        self.service.save_live_speech(proposal)
                        self.finish_live_reply(request)
                        if recovery_only:
                            break
                        continue
                    remaining = 15.5 - (time.monotonic() - last_spoke_at)
                    if remaining > 0 and self.stop_event.wait(remaining):
                        break
                    if self.stop_event.is_set():
                        break
                    proposal["status"] = "unknown"
                    self.service.save_live_speech(proposal)
                    result = event_bridge.speak(key, handle, speech, since=cursor, nonce=proposal["nonce"])
                    proposal["status"] = "confirmed"
                    proposal['receipt'] = result
                    self.service.save_live_speech(proposal)
                    last_spoke_at = time.monotonic()
                    result = self.service.accept_live_chatter(result)
                    cursor = self.service.event_cursor()
                    new_voices = event_bridge.other_voices(result, handle)
                    if recovery_only:
                        request["packets"] = request["packets"] + event_bridge.chatter_packets(new_voices, self.service.live_input_limit, context=context)
                    else:
                        pending.extend(new_voices)
                    self.tasks.put(("spoke", speech))
                    self.finish_live_reply(request)
                    if recovery_only:
                        break
                if recovery_only:
                    break
        except Exception as error:
            self.tasks.put(("relay_error", str(error)))
        finally:
            self.tasks.put(("stopped", None))

    def finish_live_reply(self, request):
        request["packets"] = request.get("packets", [])[1:]
        request["status"] = "queued" if request["packets"] else "resolved"
        for field in ("reply", "request_id", "error"):
            request.pop(field, None)
        self.service.save_live_nomi(request)

    def drain(self):
        if not self.winfo_exists():
            return
        try:
            while True:
                kind, value = self.tasks.get_nowait()
                if kind == "calendar":
                    self.refresh_btn.configure(state="disabled" if self.running else "normal")
                    self.calendar_events = value
                    self.event_list.delete(0, "end")
                    for event in value:
                        self.event_list.insert("end", f"{event.get('title', 'Untitled')} · {event.get('phase', 'event')}")
                    if value:
                        self.event_list.selection_set(0)
                        self.show_event()
                        if not self.running:
                            self.status.set("Calendar ready. Choose Map hangout or Event, then Start listening.")
                    else:
                        self.event_detail.set("No current or upcoming events are posted.")
                        if not self.running:
                            self.status.set("No events posted. Map hangout is available anytime.")
                elif kind == "approval":
                    try:
                        if not self.stop_event.is_set():
                            value['approved'] = self.confirm_public_speech(value['speech'])
                    finally:
                        value["ready"].set()
                elif kind == "can_ask":
                    self.ask_btn.configure(state="normal" if self.running and not self.recovering_live and not self.stop_event.is_set() else "disabled")
                elif kind == "error":
                    self.refresh_btn.configure(state="normal")
                    self.update_live_recovery()
                    self.status.set(value)
                elif kind == "status":
                    self.status.set(value)
                elif kind == "heard":
                    for voice in value:
                        self.log(f"{voice['handle']}: {voice['said']}")
                elif kind == "place":
                    self.log(f"Listening at {value}. Public speech uses this resident's actual map location.")
                elif kind == "spoke":
                    self.log(f"{self.service.name}: {value}")
                elif kind in ("quiet", "private"):
                    self.log(value)
                elif kind == "relay_error":
                    self.log(f"Relay paused safely: {value}")
                    self.status.set("Paused after an error. Nothing uncertain will be resent automatically.")
                elif kind == "stopped":
                    self.running = False
                    self.recovering_live = False
                    self.toggle_btn.configure(text="Start listening", bg=LILAC, state="normal")
                    self.ask_btn.configure(state="disabled")
                    self.mode_picker.configure(state="readonly")
                    self.event_list.configure(state="normal")
                    self.refresh_btn.configure(state="normal")
                    self.update_live_recovery()
                    if self.closing:
                        self.after_cancel(self.drain_after)
                        self.destroy()
                        return
                    if not self.status.get().startswith("Paused after an error"):
                        self.status.set("Off. Nothing is being listened to or posted.")
        except queue.Empty:
            pass
        self.drain_after = self.after(100, self.drain)

    def close(self):
        self.service.end_live_continuation()
        self.stop_event.set()
        if self.running:
            self.closing = True
            self.status.set("Pausing the live session before closing…")
        else:
            self.after_cancel(self.drain_after)
            self.destroy()


class App(tk.Tk):
    def __init__(self, profiles):
        super().__init__()
        self.profiles = profiles
        self.themes = desk_themes.THEMES
        self.theme_name = desk_themes.load_theme(profiles)
        self.set_theme_palette(self.theme_name)
        self.service = ResidentService(profiles, profiles.active_id()) if profiles.active_id() else None
        self.title("Postmark ↔ Nomi Companion")
        self.geometry("1080x780")
        self.minsize(900, 650)
        self.configure(bg=BG)
        self.busy = False
        self.current = None
        self.ids = []
        self.tasks = queue.Queue()
        self.controls = []
        self.events_window = None
        self.profile_labels = {}
        self.nomi_confirmation_queue = []
        self.protocol("WM_DELETE_WINDOW", self.close)

        lamplit_ui.build_mail(self)

        self.rebuild_profile_selector()
        self.refresh()
        self.after(100, self.drain)
        if not self.service:
            self.after(350, self.add_resident)
        elif not self.service.handle:
            self.status.set("Legacy state was preserved. Complete the migrated resident profile before checking mail.")
            self.after(450, self.edit_resident)
        elif self.service.profile.get("migrated_from_legacy"):
            self.status.set(f"Migrated {self.service.name} into an isolated resident profile. Existing mail state was preserved.")

    def set_theme_palette(self, name):
        global BG, CARD, INK, MUTED, CYAN, LILAC, ACTIVE
        colors = desk_themes.palette(name)
        BG, CARD, INK, MUTED = (colors[k] for k in ('NIGHT','PANEL','TEXT','MUTED'))
        CYAN = LILAC = colors['GOLD']
        ACTIVE = colors['SOFT']
        lamplit_ui.set_palette(name)

    def change_theme(self, _event=None):
        name = self.theme_choice.get()
        if name not in self.themes:
            self.theme_choice.set(self.theme_name)
            return
        try:
            desk_themes.save_theme(self.profiles, name)
        except Exception:
            self.theme_choice.set(self.theme_name)
            messagebox.showerror('Theme not saved', 'The desk theme could not be saved. Your mail is unchanged.', parent=self)
            return
        old = desk_themes.palette(self.theme_name)
        self.theme_name = name
        self.set_theme_palette(name)
        desk_themes.recolor(self, old, desk_themes.palette(name))
        lamplit_ui.configure_theme(self)

    def button(self, parent, text, command, color):
        button = tk.Button(
            parent, text=text, command=command, bg=color,
            fg=BG if color in (CYAN, LILAC) else INK, relief="flat", bd=0,
            padx=12, pady=8, cursor="hand2", font=("Segoe UI", 10, "bold"),
        )
        self.controls.append(button)
        return button

    def textbox(self, parent, height):
        return tk.Text(
            parent, height=height, bg=CARD, fg=INK, insertbackground=CYAN,
            selectbackground="#575072", relief="flat", padx=15, pady=12,
            wrap="word", font=("Segoe UI", 11), undo=True,
        )

    def connected(self):
        return bool(self.service and self.service.connected())

    def rebuild_profile_selector(self):
        self.profile_labels = {}
        default = self.profiles.index.get("default_profile_id")
        selected = ""
        for profile in self.profiles.profiles():
            label = f"{'★ ' if profile['id'] == default else ''}{profile.get('nomi_name') or 'Resident'} — @{profile.get('handle') or 'needs setup'}"
            self.profile_labels[label] = profile["id"]
            if profile["id"] == self.profiles.active_id():
                selected = label
        self.resident_combo.configure(values=list(self.profile_labels))
        self.resident_choice.set(selected)
        if self.service:
            marker = "DEFAULT · " if self.service.profile_id == default else ""
            self.active_identity.set(f"{marker}{self.service.name} / @{self.service.handle or 'needs setup'}")
        else:
            self.active_identity.set("NO RESIDENT SELECTED")

    def switch_resident(self, _event=None):
        profile_id = self.profile_labels.get(self.resident_choice.get())
        if not profile_id or (self.service and profile_id == self.service.profile_id):
            return
        if self.busy:
            messagebox.showinfo("One moment", "Let the current operation finish before switching residents.", parent=self)
            self.rebuild_profile_selector()
            return
        if self.events_window and self.events_window.winfo_exists():
            messagebox.showinfo("Close Live Events", "Close the current resident's Live Events window before switching.", parent=self)
            self.rebuild_profile_selector()
            return
        self.autosave()
        self.profiles.set_active(profile_id)
        self.service = ResidentService(self.profiles, profile_id)
        self.current = None
        self.rebuild_profile_selector()
        self.refresh()
        self.status.set(f"Active resident changed to {self.service.name} / @{self.service.handle}. All mailbox and event state now comes from that profile only.")

    def work(self, label, function, done):
        if self.busy:
            return
        if self.events_window and self.events_window.winfo_exists():
            messagebox.showinfo("Live session open", "Close Live Map & Events before starting mailbox work for this resident.", parent=self)
            return
        self.busy = True
        resident_id = self.service.profile_id if self.service else None
        self.status.set(label)
        for control in self.controls:
            control.configure(state="disabled")
        self.resident_combo.configure(state="disabled")
        def run():
            try:
                value = function()
                self.tasks.put((done, value, None, resident_id))
            except Exception as error:
                self.tasks.put((done, None, str(error), resident_id))
        threading.Thread(target=run, daemon=True).start()

    def drain(self):
        try:
            while True:
                done, value, error, resident_id = self.tasks.get_nowait()
                self.busy = False
                for control in self.controls:
                    control.configure(state="normal")
                self.resident_combo.configure(state="readonly")
                if not self.service or self.service.profile_id != resident_id:
                    self.status.set("A completed operation belongs to another resident profile; its state stayed isolated there.")
                    continue
                self.refresh()
                if error:
                    entry = self.service.entries().get(self.current, {}) if self.current else {}
                    if entry.get("status") == "response_unknown":
                        self.status.set(entry.get("error", "Nomi response is uncertain. Check the normal chat."))
                    elif entry.get("status") == "needs_send_check":
                        self.status.set("Submission is uncertain. Inspect GitHub before any retry.")
                    else:
                        self.status.set(error)
                    messagebox.showerror("Needs attention", error, parent=self)
                else:
                    done(value)
        except queue.Empty:
            pass
        self.after(100, self.drain)

    def refresh(self):
        self.listbox.delete(0, "end")
        self.ids = []
        if not self.service:
            self.detail.configure(text="Add a resident to begin")
            self._clear_text()
            return
        entries = self.service.entries()
        labels = {
            "pending": "held draft", "needs_attention": "continue", "response_unknown": "check Nomi",
            "rejected": "kept unsent", "submitting": "submitting", "needs_send_check": "check GitHub",
            "pr_open": "submitted", "contacting_nomi": "with Nomi", "reading": "reading",
            "awaiting_nomi_confirmation": "approve Nomi",
        }
        for key, entry in sorted(entries.items(), reverse=True):
            if bool(entry.get("archived_at")) != self.show_archived.get():
                continue
            # Cleared letters remain durable and stay in `seen` for duplicate
            # protection, but no longer clutter the active mailbox.
            if (entry.get("status") == "rejected" and not entry.get("archived_at")) or entry.get("cleared_from_mailbox_at"):
                continue
            self.ids.append(key)
            sender = (entry.get("fields") or {}).get("from", "unknown")
            self.listbox.insert("end", f"{sender} · {labels.get(entry.get('status'), entry.get('status'))}")
        if self.current in self.ids:
            index = self.ids.index(self.current)
            self.listbox.selection_set(index)
            self.show_current()
        elif self.ids:
            self.current = self.ids[0]
            self.listbox.selection_set(0)
            self.show_current()
        else:
            self.current = None
            self.detail.configure(text=f"{self.service.name}'s mailbox")
            self._clear_text()
        self.update_actions()

    def _clear_text(self):
        for widget in (self.incoming, self.draft):
            widget.configure(state="normal")
            widget.delete("1.0", "end")

    def select(self, _event=None):
        selection = self.listbox.curselection()
        if selection:
            self.autosave()
            self.current = self.ids[selection[0]]
            self.show_current()

    def show_current(self):
        if not self.current or not self.service:
            return
        entry = self.service.entries().get(self.current, {})
        self.detail.configure(text=f"From {(entry.get('fields') or {}).get('from', 'unknown')} · {entry.get('status', 'unknown').replace('_', ' ')}")
        self.incoming.configure(state="normal")
        self.incoming.delete("1.0", "end")
        self.incoming.insert("1.0", entry.get("body", ""))
        self.incoming.configure(state="disabled")
        self.draft.configure(state="normal")
        self.draft.delete("1.0", "end")
        self.draft.insert("1.0", entry.get("draft", ""))
        self.update_actions()

    def update_actions(self):
        entry = self.service.entries().get(self.current, {}) if self.service and self.current else {}
        archived = bool(entry.get("archived_at"))
        if archived:
            entry = {}
        pending = entry.get("status") == "pending"
        submitted = entry.get("status") == "pr_open"
        awaiting = entry.get("status") == "awaiting_nomi_confirmation"
        uncertain = entry.get("status") == "response_unknown"
        self.save_btn.configure(state="normal" if pending else "disabled")
        self.reject_btn.configure(
            text="Clear submitted" if submitted else "Keep unsent",
            state="normal" if pending or submitted else "disabled",
        )
        self.send_btn.configure(state="normal" if pending else "disabled")
        self.retry_btn.configure(
            text="Send to Nomi…" if awaiting else "Continue letter",
            state="normal" if awaiting or entry.get("status") == "needs_attention" else "disabled",
        )
        self.recover_btn.configure(state="normal" if can_paste_reply(entry) else "disabled")
        self.resend_btn.configure(state="normal" if uncertain else "disabled")
        self.pr_btn.configure(state="normal" if entry.get("pr") else "disabled")
        self.draft.configure(state="normal" if pending else "disabled")
        can_archive = entry.get("status") in {"pending", "awaiting_nomi_confirmation", "needs_attention", "response_unknown", "rejected"} and not entry.get("pr") and not entry.get("submission")
        self.archive_btn.configure(text="Restore mail" if archived else "Archive mail…", state="normal" if archived or can_archive else "disabled")
        lamplit_ui.sync_mail(self)

    def autosave(self):
        if not self.service or not self.current:
            return
        entry = self.service.entries().get(self.current, {})
        text = self.draft.get("1.0", "end").strip()
        if not entry.get("archived_at") and entry.get("status") == "pending" and text and text != entry.get("draft", ""):
            try:
                self.service.save_draft(self.current, text)
            except Exception:
                pass

    def check_mail(self):
        if not self.service:
            self.add_resident()
            return
        service = self.service
        if not service.connected():
            self.edit_resident()
            return
        if not service.initialized():
            def initialized(keys):
                if keys:
                    self.status.set(
                        f"Imported {len(keys)} existing letter(s). Review each one before anything reaches {service.name}."
                    )
                    self.review_nomi_deliveries(keys)
                else:
                    self.status.set(
                        "Mailbox initialized. Existing letters were marked seen for this resident only."
                    )
            self.work(
                f"Preparing {service.name}'s independent mailbox…",
                service.initialize,
                initialized,
            )
            return
        self.work(
            f"Checking mail for {service.name} / @{service.handle}…",
            service.poll,
            self.review_nomi_deliveries,
        )

    def nomi_delivery_confirmed(self, key, *, continuation=False, next_part=None):
        entry = self.service.entries().get(key, {})
        fields = entry.get("fields") or {}
        sender = fields.get("from", "unknown sender")
        title = fields.get("title", "Untitled letter")
        total = int(entry.get("nomi_parts", 0) or 0)
        completed = int(entry.get("nomi_parts_completed", 0) or 0)
        first_part = int(next_part or (completed + 1))
        remaining = max(total - first_part + 1, 1)
        action = "Continue sending" if continuation else "Send"
        preview = str(entry.get("body") or "").strip().replace("\r", "")
        if len(preview) > 420:
            preview = preview[:417].rstrip() + "…"
        return messagebox.askyesno(
            f"Confirm delivery to {self.service.name}",
            f"ACTIVE RESIDENT: {self.service.name} / @{self.service.handle}\n"
            f"NOMI TARGET: {self.service.name}\nFROM: {sender}\nTITLE: {title}\n"
            f"FIRST PART TO SEND: {first_part} of {total or '?'}\n"
            f"PARTS TO SEND NOW: {remaining}\n\n{preview}\n\n"
            f"{action} this letter to the Nomi now?",
            parent=self,
        )

    def review_nomi_deliveries(self, keys):
        self.nomi_confirmation_queue = list(keys or [])
        if not self.nomi_confirmation_queue:
            self.status.set("Mail checked. Nothing is waiting to be sent to this Nomi.")
            return
        self._review_next_nomi_delivery()

    def _review_next_nomi_delivery(self):
        while self.nomi_confirmation_queue:
            key = self.nomi_confirmation_queue.pop(0)
            entry = self.service.entries().get(key, {})
            if entry.get("archived_at") or entry.get("status") != "awaiting_nomi_confirmation":
                continue
            self.current = key
            self.refresh()
            if not self.nomi_delivery_confirmed(key):
                self.status.set("Left queued for approval. Nothing was sent to the Nomi.")
                self.after_idle(self._review_next_nomi_delivery)
                return

            def done(_value):
                self.status.set("Letter delivered to the Nomi. Draft held for review.")
                self.after(100, self._review_next_nomi_delivery)

            self.work(
                f"Sending confirmed letter to {self.service.name}…",
                lambda selected=key: self.service.confirm_delivery(selected),
                done,
            )
            return
        if not self.busy:
            self.status.set("All newly found letters were reviewed. Unapproved letters remain queued.")

    def save(self):
        if not self.current:
            return
        try:
            self.service.save_draft(self.current, self.draft.get("1.0", "end"))
            self.status.set("Edits saved in this resident's isolated mailbox. Nothing was mailed.")
            self.refresh()
        except Exception as error:
            messagebox.showerror("Could not save", str(error), parent=self)

    def retry_letter(self):
        if not self.current:
            return
        key = self.current
        entry = self.service.entries().get(key, {})
        if entry.get("archived_at"):
            return
        if entry.get("status") == "awaiting_nomi_confirmation":
            if not self.nomi_delivery_confirmed(key):
                self.status.set("Left queued for approval. Nothing was sent to the Nomi.")
                return
            self.work(
                f"Sending confirmed letter to {self.service.name}…",
                lambda: self.service.confirm_delivery(key),
                lambda _value: self.status.set("Letter delivered to the Nomi. Draft held for review."),
            )
            return
        if entry.get("status") == "needs_attention":
            next_part = self.choose_resume_part(key)
            if next_part is None:
                self.status.set("Continuation cancelled. Nothing new was sent to the Nomi.")
                return
            if not self.nomi_delivery_confirmed(key, continuation=True, next_part=next_part):
                self.status.set("Continuation cancelled. Nothing new was sent to the Nomi.")
                return
            self.work(
                f"Continuing from part {next_part}…",
                lambda: self.service.retry_letter(
                    key, next_part=next_part, human_checked_chat=True
                ),
                lambda _value: self.status.set("Letter completed. Draft held for review."),
            )

    def choose_resume_part(self, key):
        entry = self.service.entries().get(key, {})
        total = int(entry.get("nomi_parts", 0) or 0)
        completed = int(entry.get("nomi_parts_completed", 0) or 0)
        if total < 1:
            messagebox.showerror(
                "Missing part count",
                "This letter has no saved part count, so it cannot be resumed safely.",
                parent=self,
            )
            return None
        default = min(completed + 1, total)
        chosen = simpledialog.askinteger(
            "Choose the next part",
            f"Saved checkpoint: {completed} of {total} parts confirmed.\n\n"
            "After checking the normal Nomi chat, enter the FIRST part that should be sent now. "
            "The app will never resend an earlier confirmed part.",
            parent=self, initialvalue=default, minvalue=completed + 1, maxvalue=total,
        )
        if chosen is None:
            return None
        if chosen > completed + 1 and not messagebox.askyesno(
            "Confirm repaired checkpoint",
            f"This records parts 1–{chosen - 1} as present in the normal Nomi chat and resumes at part {chosen}.\n\n"
            "Did you personally verify those earlier parts?",
            parent=self,
        ):
            return None
        return chosen

    def recover_reply(self):
        if not self.current:
            return
        key = self.current
        dialog = tk.Toplevel(self)
        dialog.title("Paste Nomi reply")
        dialog.geometry("720x500")
        dialog.configure(bg=CARD)
        dialog.transient(self)
        dialog.grab_set()
        result = {"text": None}

        body = tk.Frame(dialog, bg=CARD)
        body.pack(fill="both", expand=True, padx=24, pady=20)
        tk.Label(
            body, text="Paste the complete Nomi reply", font=("Segoe UI", 16, "bold"),
            fg=CYAN, bg=CARD,
        ).pack(anchor="w")
        tk.Label(
            body,
            text="Copy the reply from the normal Nomi chat. Saving it creates a held draft only—no letter part is resent and nothing is mailed.",
            wraplength=660, justify="left", font=("Segoe UI", 9), fg=MUTED, bg=CARD,
        ).pack(anchor="w", pady=(6, 12))
        reply_box = tk.Text(
            body, bg=BG, fg=INK, insertbackground=CYAN, selectbackground="#575072",
            relief="flat", padx=14, pady=12, wrap="word", font=("Segoe UI", 11), undo=True,
        )
        reply_box.pack(fill="both", expand=True)
        notice = tk.StringVar(value="")
        tk.Label(
            body, textvariable=notice, wraplength=660, justify="left",
            font=("Segoe UI", 9), fg=LILAC, bg=CARD,
        ).pack(anchor="w", pady=(7, 0))
        actions = tk.Frame(body, bg=CARD)
        actions.pack(fill="x", pady=(10, 0))

        def paste_clipboard():
            try:
                clipboard_text = self.clipboard_get()
            except tk.TclError:
                notice.set("The clipboard does not currently contain text.")
                return
            reply_box.delete("1.0", "end")
            reply_box.insert("1.0", clipboard_text)
            notice.set(f"Pasted {len(clipboard_text):,} characters. Review, then save.")
            reply_box.focus_set()

        def save_reply(_event=None):
            value = reply_box.get("1.0", "end-1c").strip()
            if not value:
                notice.set("Paste a non-empty Nomi reply first.")
                return
            result["text"] = value
            dialog.destroy()

        def cancel(_event=None):
            dialog.destroy()

        tk.Button(
            actions, text="Paste clipboard", command=paste_clipboard,
            bg=CYAN, fg=BG, relief="flat", padx=12, pady=9,
            font=("Segoe UI", 10, "bold"),
        ).pack(side="left")
        tk.Button(
            actions, text="Cancel", command=cancel,
            bg=BG, fg=INK, relief="flat", padx=12, pady=9,
            font=("Segoe UI", 10, "bold"),
        ).pack(side="right", padx=(8, 0))
        tk.Button(
            actions, text="Save reply", command=save_reply,
            bg=LILAC, fg=BG, relief="flat", padx=12, pady=9,
            font=("Segoe UI", 10, "bold"),
        ).pack(side="right")
        dialog.bind("<Escape>", cancel)
        dialog.bind("<Control-Return>", save_reply)
        dialog.protocol("WM_DELETE_WINDOW", cancel)
        reply_box.focus_set()
        self.wait_window(dialog)
        text = result["text"]
        if text is None:
            return
        try:
            self.service.recover_reply(key, text)
            self.status.set("Pasted reply saved. Nothing was resent or mailed.")
            self.refresh()
        except Exception as error:
            messagebox.showerror("Could not recover", str(error), parent=self)

    def resend_after_timeout(self):
        if not self.current:
            return
        if not messagebox.askyesno(
            "Confirm normal chat was checked",
            "The uncertain part may already have reached this Nomi. Have you checked the normal Nomi chat?\n\n"
            "On the next screen, choose the uncertain part if it is missing, or the following part if you verified it arrived. Only choose Yes after checking.",
            parent=self,
        ):
            return
        key = self.current
        next_part = self.choose_resume_part(key)
        if next_part is None:
            self.status.set("Resend cancelled. Nothing new was sent to the Nomi.")
            return
        self.work(
            f"Resending from checked part {next_part}…",
            lambda: self.service.resend_after_timeout(key, next_part=next_part),
            lambda _value: self.status.set("Recovered draft held for review."),
        )

    def toggle_archived(self):
        self.autosave()
        self.refresh()

    def archive_mail(self):
        if self.busy or not self.current:
            return
        service, key = self.service, self.current
        entry = service.entries()[key]
        if entry.get("archived_at"):
            service.pipeline().restore(key)
            self.show_archived.set(False)
        else:
            if not messagebox.askyesno("Archive without replying?",
                f"Archive this letter for {service.name} / @{service.handle}?\n\n"
                "Its original status, letter, draft, checkpoints and duplicate protection remain saved. "
                "No Nomi message or public mail is sent. An uncertain earlier message may already have arrived; archiving does not undo it.", parent=self):
                return
            self.autosave()
            service.pipeline().archive(key)
        self.refresh()
        self.status.set("Archive updated locally. Nothing was sent or deleted.")

    def dismiss_current(self):
        if not self.current:
            return
        entry = self.service.entries().get(self.current, {})
        if entry.get("status") == "pr_open":
            if not messagebox.askyesno(
                "Clear submitted letter?",
                "Remove this completed letter from the active mailbox? Its submission record and duplicate protection will be kept.",
                parent=self,
            ):
                return
            self.service.clear_submitted(self.current)
            self.status.set("Submitted letter cleared. Its history and duplicate protection were kept.")
            self.refresh()
            return
        if entry.get("status") == "pending" and messagebox.askyesno(
            "Keep unsent?", "Keep this reply unsent?", parent=self
        ):
            self.service.reject(self.current)
            self.status.set("Kept unsent and cleared from the active mailbox. Nothing was mailed.")
            self.refresh()

    def send(self):
        if not self.current:
            return
        text = self.draft.get("1.0", "end").strip()
        entry = self.service.entries()[self.current]
        recipient = (entry.get("fields") or {}).get("from", "unknown")
        if not text:
            messagebox.showinfo("Empty reply", "Write a reply before approving it.", parent=self)
            return
        if not messagebox.askyesno(
            "Approve this exact resident and reply?",
            f"ACTIVE RESIDENT: {self.service.name} / @{self.service.handle}\nTO: {recipient}\n\nSubmit the exact displayed draft through GitHub?",
            parent=self,
        ):
            return
        key = self.current
        self.work("Submitting the approved reply…", lambda: self.service.send_approved(key, text), lambda _url: self.status.set("Pull request opened. Delivery follows Postmark's normal review and processing."))

    def open_pr(self):
        if self.current:
            url = self.service.entries().get(self.current, {}).get("pr")
            if url:
                webbrowser.open(url)

    def open_home(self):
        if self.service and self.service.handle:
            webbrowser.open(f"https://postmark.town/residents/{self.service.handle}/")

    def open_live_events(self):
        if self.busy:
            messagebox.showinfo("Mailbox busy", "Finish the current mailbox operation before opening Live Map & Events.", parent=self)
            return
        if not self.service:
            self.add_resident()
            return
        if self.events_window and self.events_window.winfo_exists():
            self.events_window.lift()
            return
        self.events_window = LiveEventsWindow(self, self.service)

    def add_resident(self):
        self.profile_dialog(None)

    def edit_resident(self):
        if not self.service:
            self.add_resident()
            return
        self.profile_dialog(self.service.profile_id)

    def profile_dialog(self, profile_id):
        editing = profile_id is not None
        current_settings = self.profiles.read_settings(profile_id) if editing else {}
        current_profile = self.profiles.profile(profile_id) if editing else {}
        state_exists = editing and self.profiles.mail_state_path(profile_id).exists()
        dialog = tk.Toplevel(self)
        dialog.title("Edit Resident" if editing else "Add Resident")
        dialog.geometry("590x720")
        dialog.configure(bg=CARD)
        dialog.transient(self)
        dialog.grab_set()
        body = tk.Frame(dialog, bg=CARD)
        body.pack(fill="both", expand=True, padx=25, pady=22)
        tk.Label(body, text="Resident profile", font=("Segoe UI", 17, "bold"), fg=CYAN, bg=CARD).pack(anchor="w")
        tk.Label(body, text="Each profile has its own mailbox, recovery checkpoints, drafts, outgoing locks, and Live Events cursor.", wraplength=530, justify="left", font=("Segoe UI", 9), fg=MUTED, bg=CARD).pack(anchor="w", pady=(7, 12))

        def field(label, variable, secret=False):
            tk.Label(body, text=label, font=("Segoe UI", 9, "bold"), fg=MUTED, bg=CARD).pack(anchor="w", pady=(7, 3))
            widget = tk.Entry(body, textvariable=variable, show="•" if secret else "", bg=BG, fg=INK, insertbackground=CYAN, relief="flat", font=("Segoe UI", 10))
            widget.pack(fill="x", ipady=7)
            return widget

        handle = tk.StringVar(value=current_profile.get("handle", ""))
        handle_widget = field("Postmark resident handle", handle)
        if state_exists and handle.get():
            handle_widget.configure(state="disabled")
            tk.Label(body, text="Handle is locked after this resident's inbox baseline exists.", font=("Segoe UI", 8), fg=LILAC, bg=CARD).pack(anchor="w")
        nomi_key = tk.StringVar(value=read_secret(current_settings, "encrypted_key"))
        field("Nomi API key", nomi_key, True)
        selected_nomi = tk.StringVar()
        choices = {}
        existing_nomi_id = current_settings.get("nomi_id")
        existing_name = current_profile.get("nomi_name") or current_settings.get("nomi_name")
        if existing_nomi_id:
            label = f"{existing_name or 'Nomi'} · {str(existing_nomi_id)[:8]}"
            choices[label] = existing_nomi_id
            selected_nomi.set(label)
        tk.Label(body, text="Nomi", font=("Segoe UI", 9, "bold"), fg=MUTED, bg=CARD).pack(anchor="w", pady=(7, 3))
        nomi_combo = ttk.Combobox(body, textvariable=selected_nomi, values=list(choices), state="readonly")
        nomi_combo.pack(fill="x", ipady=5)
        postmark_key = tk.StringVar()
        key_label = ("Postmark household key (saved; blank keeps it)"
                     if current_settings.get("encrypted_postmark_key") else
                     "Postmark household key (needed for Live Events)")
        field(key_label, postmark_key, True)
        github_account = tk.StringVar(value=current_settings.get("github_account", ""))
        field("Expected GitHub account for outgoing mail (optional but recommended)", github_account)
        options = tk.Frame(body, bg=CARD)
        options.pack(fill="x", pady=10)
        tk.Label(options, text="Nomi message limit", font=("Segoe UI", 9, "bold"), fg=MUTED, bg=CARD).pack(side="left")
        limit = tk.StringVar(value=str(current_settings.get("input_limit", 800)))
        ttk.Combobox(options, textvariable=limit, values=["400", "800"], state="readonly", width=6).pack(side="left", padx=10)
        tk.Label(options, text="800 requires a qualifying Nomi plan", font=("Segoe UI", 8), fg=MUTED, bg=CARD).pack(side="left")
        make_default = tk.BooleanVar(value=editing and self.profiles.index.get("default_profile_id") == profile_id)
        ttk.Checkbutton(body, text="Use this resident by default on startup", variable=make_default).pack(anchor="w", pady=4)
        mail_mode = tk.StringVar(value=current_settings.get("initial_mail_mode", "baseline"))
        mail_box = tk.LabelFrame(
            body, text="First mailbox setup", bg=CARD, fg=CYAN,
            font=("Segoe UI", 9, "bold"), padx=10, pady=7,
        )
        mail_box.pack(fill="x", pady=(7, 3))
        mode_state = "disabled" if state_exists else "normal"
        tk.Radiobutton(
            mail_box, text="Start fresh — mark existing letters seen",
            variable=mail_mode, value="baseline", state=mode_state,
            bg=CARD, fg=INK, activebackground=CARD, activeforeground=INK,
            selectcolor=BG, font=("Segoe UI", 9),
        ).pack(anchor="w")
        tk.Radiobutton(
            mail_box, text="Import existing mail — queue every letter for confirmation",
            variable=mail_mode, value="import", state=mode_state,
            bg=CARD, fg=INK, activebackground=CARD, activeforeground=INK,
            selectcolor=BG, font=("Segoe UI", 9),
        ).pack(anchor="w")
        tk.Label(
            mail_box,
            text=("Mailbox state already exists; this choice is locked." if state_exists else
                  "Importing never contacts Nomi until you approve each letter."),
            wraplength=500, justify="left", font=("Segoe UI", 8), fg=LILAC, bg=CARD,
        ).pack(anchor="w", pady=(4, 0))
        notice = tk.StringVar(value="Load Nomis after entering the Nomi key, then select the correct one.")
        tk.Label(body, textvariable=notice, wraplength=530, justify="left", font=("Segoe UI", 9), fg=LILAC, bg=CARD).pack(anchor="w", pady=9)
        row = tk.Frame(body, bg=CARD)
        row.pack(fill="x", pady=(4, 0))

        def load_nomis():
            api_key = nomi_key.get().strip()
            if not api_key:
                notice.set("Enter this resident's Nomi API key first.")
                return
            load_btn.configure(state="disabled")
            local = queue.Queue()
            def request():
                try:
                    result = mail_pipeline.request_json(
                        f"{nomi_client.NOMI_API}/nomis",
                        headers={"Authorization": api_key}, timeout=30,
                    )
                    local.put((result.get("nomis", []), None))
                except Exception as error:
                    local.put((None, str(error)))
            def check():
                try:
                    nomis, error = local.get_nowait()
                except queue.Empty:
                    dialog.after(100, check)
                    return
                load_btn.configure(state="normal")
                if error:
                    notice.set(error)
                    return
                choices.clear()
                for nomi in nomis:
                    label = f"{nomi.get('name', 'Nomi')} · {nomi['uuid'][:8]}"
                    choices[label] = nomi["uuid"]
                nomi_combo.configure(values=list(choices))
                if len(choices) == 1:
                    selected_nomi.set(next(iter(choices)))
                notice.set("Select the Nomi that belongs to this Postmark resident.")
            threading.Thread(target=request, daemon=True).start()
            dialog.after(100, check)

        def save_profile():
            chosen_handle = handle.get().strip()
            chosen_label = selected_nomi.get()
            if not chosen_handle:
                notice.set("Enter the Postmark resident handle.")
                return
            if chosen_label not in choices:
                notice.set("Load and select this resident's Nomi.")
                return
            if not nomi_key.get().strip():
                notice.set("Enter the Nomi API key.")
                return
            settings = {
                "resident_handle": chosen_handle,
                "nomi_id": choices[chosen_label],
                "nomi_name": chosen_label.split(" · ", 1)[0],
                "input_limit": int(limit.get()),
                "nomi_reply_timeout": int(current_settings.get("nomi_reply_timeout", 180)),
                "part_pause": float(current_settings.get("part_pause", 4)),
                "initial_mail_mode": mail_mode.get(),
                "encrypted_key": crypt_key(nomi_key.get().strip()),
            }
            if postmark_key.get().strip():
                settings["encrypted_postmark_key"] = crypt_key(postmark_key.get().strip())
            settings["github_account"] = github_account.get().strip()
            try:
                if editing:
                    if int(current_settings.get("input_limit", 800)) != int(limit.get()):
                        ResidentService(self.profiles, profile_id).pipeline().upgrade_message_limit(int(limit.get()))
                    self.profiles.update(
                        profile_id, handle=chosen_handle,
                        nomi_name=settings["nomi_name"], settings=settings,
                        make_default=make_default.get(),
                    )
                    chosen_id = profile_id
                else:
                    chosen_id = self.profiles.add(
                        handle=chosen_handle, nomi_name=settings["nomi_name"],
                        settings=settings, make_default=make_default.get(),
                    )
                self.profiles.set_active(chosen_id)
                self.service = ResidentService(self.profiles, chosen_id)
                self.current = None
                self.rebuild_profile_selector()
                self.refresh()
                dialog.destroy()
                self.status.set(f"Resident profile saved: {self.service.name} / @{self.service.handle}. Its state is isolated from every other resident.")
            except Exception as error:
                notice.set(str(error))

        load_btn = tk.Button(row, text="Load my Nomis", command=load_nomis, bg=CYAN, fg=BG, relief="flat", padx=12, pady=9)
        load_btn.pack(side="left")
        tk.Button(row, text="Save Resident", command=save_profile, bg=LILAC, fg=BG, relief="flat", padx=12, pady=9).pack(side="right")

    def remove_resident(self):
        if not self.service:
            return
        if self.busy or (self.events_window and self.events_window.winfo_exists()):
            messagebox.showinfo("Resident in use", "Finish active work and close Live Events before removing a resident.", parent=self)
            return
        answer = messagebox.askyesnocancel(
            "Remove resident",
            f"Remove {self.service.name} / @{self.service.handle} from the app?\n\n"
            "Yes = remove profile AND permanently delete only this resident's local state.\n"
            "No = remove profile but archive this resident's local state for recovery.\n"
            "Cancel = do nothing.",
            parent=self,
        )
        if answer is None:
            return
        name = self.service.name
        archived = self.profiles.remove(self.service.profile_id, delete_state=bool(answer))
        active = self.profiles.active_id()
        self.service = ResidentService(self.profiles, active) if active else None
        self.current = None
        self.rebuild_profile_selector()
        self.refresh()
        self.status.set(
            f"Removed {name}. " + ("Its resident directory was deleted." if answer else f"Its local state was archived at {archived}.")
        )
        if not self.service:
            self.after(200, self.add_resident)

    def set_default(self):
        if not self.service:
            return
        self.profiles.set_default(self.service.profile_id)
        self.rebuild_profile_selector()
        self.status.set(f"{self.service.name} is now the default resident on startup.")

    def close(self):
        if self.busy:
            messagebox.showinfo("One moment", "Let the current mail operation finish before closing.", parent=self)
            return
        self.autosave()
        if self.events_window and self.events_window.winfo_exists():
            self.events_window.service.end_live_continuation()
            self.events_window.stop_event.set()
            if self.events_window.running:
                self.status.set("Waiting for the live session to stop before closing…")
                self.after(200, self.close)
                return
            self.events_window.destroy()
        self.destroy()


def prepare_data_root():
    """Use the generic root, copying legacy app data only once for migration."""
    if DATA.exists():
        DATA.mkdir(parents=True, exist_ok=True)
        return DATA
    legacy = None
    try:
        for candidate in APPDATA_ROOT.iterdir():
            if not candidate.is_dir() or candidate == DATA:
                continue
            settings = candidate / "settings.json"
            if settings.exists() and any(candidate.glob(".*-mail-state.json")):
                legacy = candidate
                break
    except OSError:
        legacy = None
    if legacy is None:
        DATA.mkdir(parents=True, exist_ok=True)
        return DATA
    import shutil
    shutil.copytree(legacy, DATA)
    return DATA


def lock_app(root):
    root.mkdir(parents=True, exist_ok=True)
    handle = open(root / "app.lock", "a+b")
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        if handle.read(1) == b"":
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    return handle


if __name__ == "__main__":
    try:
        data_root = prepare_data_root()
        lock = lock_app(data_root)
    except OSError:
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo("Postmark ↔ Nomi Companion", "The app is already open. Look for its window on the taskbar.")
        sys.exit(0)
    try:
        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w")
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w")
        profiles = ProfileStore(data_root)
        profiles.activate_default_on_startup()
        App(profiles).mainloop()
    except Exception as error:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Postmark ↔ Nomi Companion", str(error))
