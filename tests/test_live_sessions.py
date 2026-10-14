"""Exercise live sessions without real Nomi calls or public speech."""
import importlib.util
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
    def run_session(self, approved=True, reply="SAY: My own words.", opening=False, fail_speech=False):
        class Service:
            handle = "resident-one"
            name = "Nomi One"
            input_limit = 400

            def postmark_key(self):
                return "fake-key"

            def event_cursor(self):
                return 100

            def save_event_cursor(self, cursor):
                self.cursor = cursor

            def save_live_speech(self, speech):
                self.saved.append(dict(speech))

            def _send_nomi(self, packet):
                self.packets.append(packet)
                return reply, "reply-id"

        session = object.__new__(app.LiveEventsWindow)
        session.service = Service()
        session.service.saved = []
        session.service.packets = []
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
                session.stop_event.set()
            return approved

        session.approve_live_speech = approve
        listened = 0

        def listen(*args, **kwargs):
            nonlocal listened
            listened += 1
            if listened > 1 or reply == "PASS":
                session.stop_event.set()
            return {"latest": 101, "where": {"place": "The garden"},
                    "voices": [{"handle": "neighbor", "said": "Hello!"}]}

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


if __name__ == "__main__":
    unittest.main()
