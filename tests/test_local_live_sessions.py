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




    def run_session(self, approved=True, reply="SAY: My own words.", opening=False, fail_speech=False, nomi_timeout=False, pause_after_nomi=False, nomi_error=None):
        class Service:
            handle = "resident-one"
            name = "Nomi One"
            input_limit = 400
            live_input_limit = 400

            def live_state(self):
                return copy.deepcopy(self.state)

            def save_live_nomi(self, request):
                self.state['nomi'] = copy.deepcopy(request)

            def accept_live_chatter(self, result, *, baseline=False):
                fresh, ids = app.event_bridge.remember_voices(result,self.state.get('heard_ids',[]),baseline=baseline)
                self.state['heard_ids'] = ids
                self.state['latest'] = max(self.state.get('latest',100), result.get('latest',100))
                return fresh

            def postmark_key(self):
                return "fake-key"

            def event_cursor(self):
                return 100

            def save_event_cursor(self, cursor):
                self.cursor = cursor

            def save_live_speech(self, speech):
                self.saved.append(dict(speech))
                self.state['speech'] = dict(speech)

            def event_checkpoint(self, checkpoint=None, *, clear=False):
                if clear:
                    self.checkpoint = None
                elif checkpoint is not None:
                    self.checkpoint = dict(checkpoint)
                return getattr(self, 'checkpoint', None)

            def _send_nomi(self, packet):
                if self.state['nomi']['status'] != 'awaiting_reply':
                    raise AssertionError('Nomi checkpoint must precede request')
                self.packets.append(packet)
                if nomi_error:
                    raise nomi_error
                if nomi_timeout:
                    raise TimeoutError('uncertain Nomi delivery')
                if pause_after_nomi:
                    session.stop_event.set()
                return reply, "reply-id"

        session = object.__new__(app.LiveEventsWindow)
        session.service = Service()
        session.service.state = {'latest':100}
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
            return approved

        session.approve_live_speech = approve
        listened = 0

        def listen(*args, **kwargs):
            nonlocal listened
            listened += 1
            if listened > 2:
                session.stop_event.set()
            return {"latest": 101, "where": {"place": "The garden"},
                    "voices": [{"handle": "neighbor", "said": "Hello!", 'at_ms':100+listened}]}

        def speak(*args, **kwargs):
            self.assertEqual(session.service.saved[-1]['status'], 'unknown')
            self.assertEqual(session.service.state['speech']['nonce'], kwargs['nonce'])
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

    def test_nomi_timeout_preserves_checkpoint_and_never_posts(self):
        session, decisions, posted = self.run_session(opening=True, nomi_timeout=True)
        posted.assert_not_called()
        self.assertEqual(session.service.state['nomi']['status'], 'response_unknown')
        self.assertEqual(decisions, [])

    def test_documented_rejection_is_saved_without_expectation_of_reply(self):
        session, decisions, posted = self.run_session(opening=True, nomi_error=app.nomi_client.RejectedNomiRequest('NomiStillResponding'))
        self.assertEqual(session.service.state['nomi']['status'],'request_rejected')
        self.assertEqual(len(session.service.packets),1)
        original = copy.deepcopy(session.service.state['nomi'])
        posted.assert_not_called()
        with patch.object(app.event_bridge,'listen') as listened, patch.object(app.event_bridge,'speak') as spoke:
            session.relay_loop()
            listened.assert_not_called()
            spoke.assert_not_called()
        self.assertEqual(session.service.state['nomi'],original)
        self.assertEqual(len(session.service.packets),1)

    def test_explicit_retry_uses_exact_rejected_packet_and_keeps_new_timeout_locked(self):
        session, _, _ = self.run_session(opening=True, nomi_error=app.nomi_client.RejectedNomiRequest('NomiStillResponding'))
        packet = session.service.state['nomi']['packets'][0]
        with patch.object(session.service,'_send_nomi',side_effect=app.nomi_client.UncertainNomiResult('timeout')) as send, patch.object(app.event_bridge,'listen',return_value={'latest':101,'voices':[]}), patch.object(app.event_bridge,'speak') as spoke:
            session.relay_loop(retry_rejected=True)
            send.assert_called_once_with(packet)
            spoke.assert_not_called()
        self.assertEqual(session.service.state['nomi']['status'],'response_unknown')
        with patch.object(session.service,'_send_nomi') as send:
            session.relay_loop(retry_rejected=True)
            send.assert_not_called()

    def test_pause_after_nomi_reply_never_posts_and_preserves_reply(self):
        session, decisions, posted = self.run_session(opening=True, pause_after_nomi=True)
        posted.assert_not_called()
        self.assertEqual(session.service.state['nomi']['status'], 'reply_ready')

    def test_failed_reconnect_does_not_unlock_rejected_request(self):
        session, _, _ = self.run_session(opening=True, nomi_error=app.nomi_client.RejectedNomiRequest('NomiStillResponding'))
        original = copy.deepcopy(session.service.state['nomi'])
        with patch.object(app.event_bridge, 'listen', side_effect=RuntimeError('offline')), patch.object(session.service, '_send_nomi') as send:
            session.relay_loop(retry_rejected=True)
        send.assert_not_called()
        self.assertEqual(session.service.state['nomi'], original)

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
                self.assertEqual(str(window.keep_private_btn['state']),'disabled')
                window.service.save_live_nomi({'status':'response_unknown','packets':['synthetic saved packet']})
                window.update_live_recovery()
                self.assertEqual(str(window.keep_private_btn['state']),'normal')
                window.service.save_live_speech({'status':'unknown','nonce':'synthetic'})
                window.update_live_recovery()
                self.assertEqual(str(window.keep_private_btn['state']),'disabled')
                for geometry in ['930x690','760x570']:
                    window.geometry(geometry)
                    window.update()
                    self.assertLessEqual(window.ask_btn.winfo_rootx()+window.ask_btn.winfo_width(), window.winfo_rootx()+window.winfo_width())
                    self.assertLessEqual(window.keep_private_btn.winfo_rooty()+window.keep_private_btn.winfo_height(), window.winfo_rooty()+window.winfo_height())
                window.running = True
                window.close()
                self.assertTrue(window.winfo_exists())
                self.assertTrue(window.stop_event.is_set())
                window.tasks.put(('stopped', None))
                window.drain()
                self.assertFalse(window.winfo_exists())
            finally:
                for callback in parent.tk.call('after', 'info'):
                    parent.after_cancel(callback)
                parent.destroy()


if __name__ == "__main__":
    unittest.main()
