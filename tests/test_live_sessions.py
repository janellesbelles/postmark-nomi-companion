"""Exercise live sessions without real Nomi calls or public speech."""
import importlib.util
import copy
import pathlib
import queue
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("live_app_test", ROOT / "Companion App.pyw")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class LiveSessionTests(unittest.TestCase):
    def run_session(self, approved=True, reply="SAY: My own words.", opening=False, fail_speech=False, fail_nomi=False, heard_text="Hello!"):
        class Service:
            handle = "resident-one"
            name = "Nomi One"
            input_limit = 400
            live_input_limit = 400

            def postmark_key(self):
                return "fake-key"

            def event_cursor(self):
                return self.state.get("latest", 100)

            def accept_live_chatter(self, result, *, baseline=False):
                fresh, ids = app.event_bridge.remember_voices(result, self.state.get("heard_ids", []), baseline=baseline)
                self.state["heard_ids"] = ids
                self.state["latest"] = result.get("latest", 100)
                return fresh

            def save_event_cursor(self, cursor):
                self.cursor = cursor

            def save_live_speech(self, speech):
                self.saved.append(dict(speech))
                self.state["speech"] = dict(speech)

            def live_state(self):
                return copy.deepcopy(self.state)

            def save_live_nomi(self, request):
                self.state["nomi"] = copy.deepcopy(request)

            def _send_nomi(self, packet):
                self.packets.append(packet)
                if fail_nomi:
                    raise TimeoutError("Nomi response timed out")
                return reply, "reply-id"

        session = object.__new__(app.LiveEventsWindow)
        session.service = Service()
        session.service.saved = []
        session.service.packets = []
        session.service.state = {}
        session.stop_event = threading.Event()
        session.tasks = queue.Queue()
        session.speech_requests = queue.Queue()
        session.session_context = "Map hangout"
        session.actual_place = ""
        if opening:
            session.speech_requests.put(True)
        decisions = []

        def approve(text):
            decisions.append(text)
            if not approved:
                # A declined proposal is distinct from pausing the session.
                pass
            return approved

        session.approve_live_speech = approve
        listened = 0

        def listen(*args, **kwargs):
            nonlocal listened
            listened += 1
            if listened > 2 or (reply == "PASS" and listened > 1):
                session.stop_event.set()
            if listened == 1:
                return {"latest": 100, "voices": [{"handle": "neighbor", "said": "Old room history", "at_ms": 100}]}
            return {"latest": 101, "where": {"place": "The garden"},
                    "voices": [{"handle": "neighbor", "said": heard_text, "at_ms": 101}]}

        def speak(*args, **kwargs):
            if fail_speech:
                raise TimeoutError("uncertain public delivery")
            session.stop_event.set()
            return {"spoke": True, "latest": 102, "voices": []}

        with patch.object(app.event_bridge, "listen", side_effect=listen), patch.object(
            app.event_bridge, "speak", side_effect=speak
        ) as posted:
            session.relay_loop()
        return session, decisions, posted

    def test_rejected_public_proposal_never_posts(self):
        session, decisions, posted = self.run_session(approved=False)
        posted.assert_not_called()
        self.assertEqual(decisions, ["My own words."])
        self.assertEqual([s["status"] for s in session.service.saved], ["proposed", "kept_private"])

    def test_approved_words_and_resident_nonce_are_preserved(self):
        session, decisions, posted = self.run_session()
        self.assertEqual(decisions, ["My own words."])
        self.assertEqual(posted.call_args.args[:3], ("fake-key", "resident-one", "My own words."))
        self.assertEqual([s["status"] for s in session.service.saved], ["proposed", "unknown", "confirmed"])
        self.assertEqual(posted.call_args.kwargs["nonce"], session.service.saved[0]["nonce"])

    def test_quiet_map_can_request_nomi_opening(self):
        session, decisions, posted = self.run_session(opening=True)
        self.assertIn("start a conversation", session.service.packets[0])
        self.assertLessEqual(len(session.service.packets[0]), 400)
        self.assertEqual(decisions, ["My own words."])
        self.assertEqual(posted.call_count, 1)

    def test_pass_never_becomes_public_speech(self):
        session, decisions, posted = self.run_session(reply="PASS", opening=True)
        posted.assert_not_called()
        self.assertEqual(decisions, [])

    def test_uncertain_public_send_is_saved_and_never_retried(self):
        session, decisions, posted = self.run_session(fail_speech=True)
        self.assertEqual(posted.call_count, 1)
        self.assertEqual(session.service.saved[-1]["status"], "unknown")
        events = list(session.tasks.queue)
        self.assertTrue(any(kind == "relay_error" for kind, _ in events))
        self.assertEqual(events[-1][0], "stopped")

    def test_nomi_timeout_retains_packet_and_cannot_blindly_resume(self):
        session, decisions, posted = self.run_session(fail_nomi=True)
        request = session.service.live_state()["nomi"]
        self.assertEqual(request["status"], "response_unknown")
        self.assertEqual(request["packets"], session.service.packets)
        self.assertEqual(len(session.service.packets), 1)
        self.assertEqual(decisions, [])
        posted.assert_not_called()
        with patch.object(app.event_bridge, "listen") as listen:
            session.relay_loop()
        listen.assert_not_called()
        self.assertEqual(len(session.service.packets), 1)

    def test_session_initial_history_is_never_forwarded(self):
        session, _, _ = self.run_session()
        self.assertEqual(len(session.service.packets), 1)
        self.assertIn("Hello!", session.service.packets[0])
        self.assertNotIn("Old room history", session.service.packets[0])

    def test_long_voice_only_proposes_public_reply_after_final_part(self):
        session, decisions, posted = self.run_session(heard_text="A full speech. " * 60)
        self.assertGreater(len(session.service.packets), 1)
        self.assertEqual(decisions, ["My own words."])
        self.assertEqual(posted.call_count, 1)
        self.assertIn("FINAL:", session.service.packets[-1])

    def test_heard_ids_survive_restart_and_cursor_never_moves_back(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            profiles = app.ProfileStore(directory)
            profile = profiles.add(handle="resident-one", nomi_name="One", settings={})
            service = app.ResidentService(profiles, profile)
            old = {"latest": 200, "voices": [{"handle": "neighbor", "said": "Same words", "at_ms": 150}]}
            self.assertEqual(service.accept_live_chatter(old, baseline=True)["voices"], [])
            reopened = app.ResidentService(app.ProfileStore(directory), profile)
            self.assertEqual(reopened.accept_live_chatter(old)["voices"], [])
            new = {"latest": 201, "voices": [{"handle": "neighbor", "said": "Same words", "at_ms": 201}]}
            self.assertEqual(len(reopened.accept_live_chatter(new)["voices"]), 1)
            self.assertEqual(reopened.accept_live_chatter(new)["voices"], [])
            reopened.save_event_cursor(100)
            self.assertEqual(reopened.event_cursor(), 201)
            reopened.accept_live_chatter({"latest": 50, "voices": []}, baseline=True)
            self.assertEqual(reopened.event_cursor(), 201)
    def make_recovery(self, service):
        session = object.__new__(app.LiveEventsWindow)
        session.service = service
        session.stop_event = threading.Event()
        session.tasks = queue.Queue()
        session.speech_requests = queue.Queue()
        session.session_context = "Map hangout"
        session.actual_place = ""
        return session

    def test_paste_recovery_survives_restart_requires_approval_and_preserves_queue(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            profiles = app.ProfileStore(directory)
            profile = profiles.add(handle="resident-one", nomi_name="One", settings={})
            first = app.ResidentService(profiles, profile)
            first.save_live_nomi({"status": "response_unknown", "request_id": "r1", "packets": ["already sent", "still queued"]})
            reopened = app.ResidentService(app.ProfileStore(directory), profile)
            recovered = reopened.recover_live_reply("SAY: Hello\nworld!")
            self.assertEqual(recovered["packets"], ["already sent", "still queued"])
            session = self.make_recovery(reopened)
            session.approve_live_speech = lambda text: text == "Hello\nworld!"
            with patch.object(reopened, "postmark_key", return_value="fake-key"), patch.object(reopened, "_send_nomi") as nomi, patch.object(
                app.event_bridge, "listen"
            ) as listen, patch.object(app.event_bridge, "speak", return_value={"spoke": True, "latest": 123, "voices": []}) as speech:
                session.relay_loop(recovery_only=True)
            nomi.assert_not_called()
            listen.assert_not_called()
            self.assertEqual(speech.call_args.args[2], "Hello\nworld!")
            self.assertEqual(reopened.live_state()["nomi"]["packets"], ["still queued"])
            self.assertEqual(reopened.live_state()["nomi"]["status"], "queued")

    def test_paste_pass_and_public_uncertainty_lock_are_isolated(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            profiles = app.ProfileStore(directory)
            first = profiles.add(handle="resident-one", nomi_name="One", settings={})
            second = profiles.add(handle="resident-two", nomi_name="Two", settings={})
            a, b = app.ResidentService(profiles, first), app.ResidentService(profiles, second)
            a.save_live_nomi({"status": "awaiting_reply", "request_id": "r1", "packets": ["chatter"]})
            with self.assertRaises(ValueError):
                b.recover_live_reply("PASS")
            with self.assertRaises(ValueError):
                a.recover_live_reply("private text without a public marker")
            a.recover_live_reply("PASS")
            session = self.make_recovery(a)
            with patch.object(a, "postmark_key", return_value="fake"), patch.object(a, "_send_nomi") as nomi, patch.object(app.event_bridge, "speak") as speak:
                session.relay_loop(recovery_only=True)
            nomi.assert_not_called()
            speak.assert_not_called()
            self.assertEqual(a.live_state()["nomi"]["status"], "resolved")
            a.save_live_nomi({"status": "response_unknown", "packets": ["chatter"]})
            a.save_live_speech({"status": "unknown", "nonce": "locked"})
            with self.assertRaises(ValueError):
                a.recover_live_reply("SAY: Cannot bypass lock")
            self.assertEqual(a.live_state()["speech"]["nonce"], "locked")

    def test_live_speech_state_is_resident_isolated_and_keeps_cursor(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            profiles = app.ProfileStore(directory)
            first = profiles.add(handle="resident-one", nomi_name="One", settings={})
            second = profiles.add(handle="resident-two", nomi_name="Two", settings={})
            a, b = app.ResidentService(profiles, first), app.ResidentService(profiles, second)
            a.save_live_speech({"status": "unknown", "text": "hello", "nonce": "one"})
            a.save_event_cursor(101)
            self.assertEqual(a.live_state()["speech"]["status"], "unknown")
            self.assertEqual(a.event_cursor(), 101)
            self.assertEqual(b.live_state(), {})

    def test_live_window_renders_with_map_mode_off(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            profiles = app.ProfileStore(directory)
            profile = profiles.add(handle="resident-one", nomi_name="One", settings={})
            parent = app.App(profiles)
            parent.withdraw()
            try:
                with patch.object(app.LiveEventsWindow, "refresh_calendar"):
                    window = app.LiveEventsWindow(parent, app.ResidentService(profiles, profile))
                window.update()
                self.assertEqual(window.mode.get(), "Map hangout")
                self.assertFalse(window.running)
                self.assertEqual(str(window.ask_btn["state"]), "disabled")
                window.close()
            finally:
                parent.destroy()

    def test_multiline_paste_window_saves_response_without_contacting_nomi(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            profiles = app.ProfileStore(directory)
            profile = profiles.add(handle="resident-one", nomi_name="One", settings={})
            service = app.ResidentService(profiles, profile)
            service.save_live_nomi({"status": "response_unknown", "request_id": "r1", "packets": ["saved chatter"]})
            parent = app.App(profiles)
            parent.withdraw()
            try:
                with patch.object(app.LiveEventsWindow, "refresh_calendar"):
                    window = app.LiveEventsWindow(parent, service)
                self.assertEqual(str(window.paste_live_btn["state"]), "normal")
                window.paste_live_reply()
                dialog = next(child for child in window.winfo_children() if isinstance(child, app.tk.Toplevel))
                actions = next(child for child in dialog.winfo_children() if isinstance(child, app.tk.Frame))
                buttons = {str(child["text"]): child for child in actions.winfo_children()}
                for geometry in ("650x440", "480x320"):
                    dialog.geometry(geometry)
                    dialog.update()
                    for name in ("Paste clipboard", "Save response", "Cancel"):
                        button = buttons[name]
                        self.assertTrue(button.winfo_ismapped())
                        self.assertGreaterEqual(button.winfo_rooty(), dialog.winfo_rooty())
                        self.assertLessEqual(button.winfo_rooty() + button.winfo_height(), dialog.winfo_rooty() + dialog.winfo_height())
                        self.assertLessEqual(button.winfo_rootx() + button.winfo_width(), dialog.winfo_rootx() + dialog.winfo_width())
                reply = "SAY: First paragraph.\n\nSecond paragraph."
                with patch.object(app.tk.Toplevel, "clipboard_get", return_value=reply):
                    buttons["Paste clipboard"].invoke()
                with patch.object(app.threading, "Thread") as worker:
                    buttons["Save response"].invoke()
                self.assertEqual(service.live_state()["nomi"]["reply"], reply)
                self.assertEqual(service.live_state()["nomi"]["status"], "reply_ready")
                worker.return_value.start.assert_called_once()
                window.running = False
                window.close()
            finally:
                parent.destroy()


if __name__ == "__main__":
    unittest.main()
