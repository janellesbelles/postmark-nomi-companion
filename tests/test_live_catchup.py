"""Recovery catches up from the durable cursor; fresh starts skip old history."""
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from unittest.mock import patch
from profile_store import ProfileStore
from test_live_sessions import app


class CatchupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.profiles=ProfileStore(self.temp.name)
        self.ident=self.profiles.add(handle='resident-one',nomi_name='One',settings={})
        self.service=app.ResidentService(self.profiles,self.ident)
        self.service.save_event_cursor(100)

    def tearDown(self):
        self.temp.cleanup()

    def run_relay(self,fail=False):
        window=object.__new__(app.LiveEventsWindow)
        window.service=self.service
        window.stop_event=threading.Event()
        window.tasks=queue.Queue()
        window.speech_requests=queue.Queue()
        window.session_context='Map hangout'
        window.actual_place=''
        calls=[]
        def listen(*args,**kwargs):
            calls.append(kwargs)
            if len(calls)>1: window.stop_event.set()
            return {'latest':101,'where':{'place':'Garden'},'voices':[
                {'handle':'neighbor','said':'New words while recovery was paused.','at_ms':101}]}
        with patch.object(self.service,'postmark_key',return_value='synthetic'), patch.object(
            app.event_bridge,'listen',side_effect=listen), patch.object(app.event_bridge,'speak') as public, patch.object(
            self.service,'_send_nomi',side_effect=TimeoutError('synthetic') if fail else None,
            return_value=('PASS','synthetic-id')) as send:
            window.relay_loop()
        public.assert_not_called()
        return calls,send

    def test_paste_recovery_survives_restart_and_forwards_new_chatter_once(self):
        self.service.save_live_nomi({'status':'response_unknown','packets':['already delivered packet']})
        self.service.recover_live_reply('PASS')
        # Finish the recovered packet first; only new chatter goes to Nomi.
        self.service=app.ResidentService(self.profiles,self.ident)
        calls,send=self.run_relay()
        self.assertEqual(calls[0],{'since':100,'wait':5})
        self.assertEqual(send.call_count,1)
        self.assertIn('New words while recovery',send.call_args.args[0])
        self.assertNotIn('already delivered packet',send.call_args.args[0])
        self.assertEqual(self.service.live_state()['nomi']['status'],'resolved')
        calls,send=self.run_relay()
        send.assert_not_called()  # same timestamp/identity cannot replay

    def test_fresh_session_still_baselines_history(self):
        calls,send=self.run_relay()
        self.assertEqual(calls[0]['wait'],1)
        send.assert_not_called()

    def test_timeout_retains_continuation_but_never_blindly_resends(self):
        self.service.save_live_nomi({'status':'resolved','packets':[],'continue_from_cursor':True})
        self.run_relay(fail=True)
        state=self.service.live_state()['nomi']
        self.assertEqual(state['status'],'response_unknown')
        self.assertTrue(state['continue_from_cursor'])
        calls,send=self.run_relay()
        self.assertEqual(calls,[])
        send.assert_not_called()

    def test_explicit_idle_pause_ends_catchup_but_unfinished_does_not(self):
        for status,packets in [('response_unknown',['held']),('reply_ready',['held']),('queued',['held']),('resolved',[])]:
            self.service.save_live_nomi({'status':status,'packets':packets,'continue_from_cursor':True})
            self.service.end_live_continuation()
            self.assertEqual(bool(self.service.live_state()['nomi'].get('continue_from_cursor')),status!='resolved')

    def test_keep_private_reset_does_not_continue_old_room(self):
        self.service.save_live_nomi({'status':'response_unknown','packets':['held'],'continue_from_cursor':True})
        self.service.keep_live_private_and_reset()
        calls,send=self.run_relay()
        self.assertEqual(calls[0]['wait'],1)
        send.assert_not_called()
