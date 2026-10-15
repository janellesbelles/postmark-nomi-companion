"""Unmarked live replies stay private without trapping the listen loop."""
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from unittest.mock import patch
from profile_store import ProfileStore
from test_live_sessions import app

ROOT = Path(__file__).resolve().parents[1]


class PrivateReplyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.profiles = ProfileStore(self.temp.name)
        self.first = self.profiles.add(handle='resident-one',nomi_name='One',settings={})
        self.second = self.profiles.add(handle='resident-two',nomi_name='Two',settings={})
        self.service = app.ResidentService(self.profiles,self.first)
        self.block = patch('urllib.request.urlopen',side_effect=AssertionError('No live APIs'))
        self.block.start()

    def tearDown(self):
        self.block.stop()
        self.temp.cleanup()

    def save_reply(self, reply='A normal private response.', remainder=None):
        self.service.save_live_nomi({'status':'reply_ready','request_id':'synthetic-request',
            'packets':['original synthetic packet']+list(remainder or []),'reply':reply})

    def run_session(self, *, recovery_only=True, next_reply='PASS'):
        window = object.__new__(app.LiveEventsWindow)
        window.service = self.service
        window.stop_event = threading.Event()
        window.tasks = queue.Queue()
        window.speech_requests = queue.Queue()
        window.session_context = 'Map hangout'
        window.actual_place = ''
        listens = 0
        def listen(*args,**kwargs):
            nonlocal listens
            listens += 1
            if listens > 1:
                window.stop_event.set()
            return {'latest':100+listens,'voices':[]}
        with patch.object(self.service,'postmark_key',return_value='synthetic-key'), patch.object(self.service,'_send_nomi',return_value=(next_reply,'synthetic-reply')) as nomi, patch.object(app.event_bridge,'speak') as speech, patch.object(app.event_bridge,'listen',side_effect=listen) as heard:
            window.relay_loop(recovery_only=recovery_only)
        return nomi,speech,heard,list(window.tasks.queue)

    def test_saved_unmarked_reply_does_not_request_nomi_or_post(self):
        exact = '*Synthetic action* Hello, neighbor.\nSecond line.'
        self.save_reply(exact)
        nomi,speech,heard,tasks = self.run_session()
        nomi.assert_not_called()
        speech.assert_not_called()
        heard.assert_not_called()
        state = self.service.live_state()
        self.assertEqual(state['nomi']['status'],'resolved')
        self.assertEqual(state['private_reply_history'][0]['reply'],exact)
        self.assertFalse(any(k=='relay_error' for k,v in tasks))
        self.assertEqual(app.ResidentService(self.profiles,self.second).live_state(),{})

    def test_resume_advances_old_reply_then_only_sends_unsent_remaining_packet(self):
        self.save_reply(remainder=['remaining synthetic packet'])
        nomi,speech,heard,tasks = self.run_session(recovery_only=False)
        self.assertEqual(nomi.call_args.args[0],'remaining synthetic packet')
        self.assertEqual(nomi.call_count,1)
        speech.assert_not_called()
        self.assertEqual(heard.call_count,2)  # baseline, then actual listening after queue
        self.assertFalse(any(k=='relay_error' for k,v in tasks))
        self.assertEqual(self.service.live_state()['nomi']['status'],'resolved')

    def test_fresh_unmarked_response_is_private_and_listening_continues(self):
        self.service.save_live_nomi({'status':'queued','packets':['new synthetic packet']})
        nomi,speech,heard,tasks = self.run_session(recovery_only=False,next_reply='Normal unmarked words.')
        self.assertEqual(nomi.call_count,1)
        speech.assert_not_called()
        self.assertEqual(heard.call_count,2)
        self.assertFalse(any(k=='relay_error' for k,v in tasks))
        self.assertEqual(len(self.service.live_state()['private_reply_history']),1)

    def test_crash_after_private_record_is_restart_safe_and_history_not_duplicated(self):
        self.save_reply()
        with patch.object(app.LiveEventsWindow,'finish_live_reply',side_effect=RuntimeError('synthetic interruption')):
            self.run_session()
        self.assertEqual(self.service.live_state()['nomi']['status'],'reply_ready')
        self.service = app.ResidentService(self.profiles,self.first)
        nomi,speech,heard,tasks = self.run_session()
        nomi.assert_not_called()
        speech.assert_not_called()
        self.assertEqual(len(self.service.live_state()['private_reply_history']),1)
        self.assertEqual(self.service.live_state()['nomi']['status'],'resolved')

    def test_overlength_say_still_requires_manual_review_without_consumption(self):
        self.save_reply('SAY: '+'x'*501)
        nomi,speech,heard,tasks = self.run_session()
        nomi.assert_not_called()
        speech.assert_not_called()
        self.assertTrue(any(k=='relay_error' for k,v in tasks))
        self.assertEqual(self.service.live_state()['nomi']['status'],'reply_ready')
        self.assertNotIn('private_reply_history',self.service.live_state())

    def test_uncertain_nomi_and_public_send_locks_still_take_priority(self):
        for lock in ['nomi','public']:
            self.save_reply()
            if lock=='nomi':
                request = self.service.live_state()['nomi']
                request['status'] = 'response_unknown'
                self.service.save_live_nomi(request)
            else:
                self.service.save_live_speech({'status':'unknown','nonce':'original-synthetic-nonce'})
            nomi,speech,heard,tasks = self.run_session()
            nomi.assert_not_called()
            speech.assert_not_called()
            heard.assert_not_called()
            self.assertTrue(any(k=='relay_error' for k,v in tasks))
            self.assertNotIn('private_reply_history',self.service.live_state())

    def test_keep_private_preserves_overlength_reply_and_remaining_packets(self):
        reply = 'SAY: '+'x'*720
        self.save_reply(reply,remainder=['unsent remainder'])
        original = self.service.live_state()['nomi']
        self.service.save_live_speech({'status':'proposed','text':'held speech','nonce':'synthetic'})
        self.service.keep_live_private_and_reset()
        state = self.service.live_state()
        self.assertEqual(state['abandoned_live_history'][0]['nomi'],original)
        self.assertEqual(state['speech']['status'],'kept_private')
        self.assertEqual(state['nomi']['packets'],[])
        self.assertEqual(app.ResidentService(self.profiles,self.second).live_state(),{})
        nomi,speech,heard,tasks = self.run_session(recovery_only=False)
        nomi.assert_not_called()
        speech.assert_not_called()
        self.assertEqual(heard.call_count,2)
        self.assertTrue(heard.call_args_list[0].kwargs['wait']==1)
        self.assertFalse(any(k=='relay_error' for k,v in tasks))

    def test_uncertain_nomi_can_be_abandoned_not_reclassified_as_delivered(self):
        original = {'status':'response_unknown','packets':['possibly delivered','never sent'],'error':'NoReply'}
        self.service.save_live_nomi(original)
        self.service.keep_live_private_and_reset('SAY: '+'x'*720)
        archive = self.service.live_state()['abandoned_live_history'][0]
        self.assertEqual(archive['nomi'],original)
        self.assertEqual(archive['manually_preserved_reply'],'SAY: '+'x'*720)
        self.assertEqual(self.service.live_state()['nomi']['status'],'abandoned_private')
        with self.assertRaises(ValueError):
            self.service.recover_live_reply('SAY: stale reply')

    def test_keep_private_cannot_unlock_unknown_public_post(self):
        self.save_reply()
        self.service.save_live_speech({'status':'unknown','text':'possibly posted','nonce':'saved'})
        original = self.service.live_state()
        with self.assertRaises(ValueError):
            self.service.keep_live_private_and_reset()
        self.assertEqual(self.service.live_state(),original)

    def test_atomic_reset_failure_keeps_original_checkpoint(self):
        self.save_reply(remainder=['remaining'])
        original = self.service.live_state()
        with patch.object(app.mail_pipeline.AtomicState,'write',side_effect=OSError('synthetic disk failure')):
            with self.assertRaises(OSError):
                self.service.keep_live_private_and_reset()
        self.assertEqual(self.service.live_state(),original)

    def test_new_button_and_dialog_preserve_full_reply_without_starting_relay(self):
        self.save_reply('SAY: '+'x'*720)
        parent = app.App(self.profiles)
        parent.withdraw()
        try:
            with patch.object(app.LiveEventsWindow,'refresh_calendar'):
                window = app.LiveEventsWindow(parent,self.service)
            window.update()
            self.assertEqual(str(window.keep_private_btn['state']),'normal')
            window.keep_private_btn.invoke()
            window.update()
            dialog = next(w for w in window.winfo_children() if isinstance(w,app.tk.Toplevel))
            box = next(w for w in dialog.winfo_children() if isinstance(w,app.tk.Text))
            self.assertEqual(box.get('1.0','end-1c'),'SAY: '+'x'*720)
            actions = next(w for w in dialog.winfo_children() if isinstance(w,app.tk.Frame))
            save = next(w for w in actions.winfo_children() if w.cget('text')=='Keep private — no posting')
            for geometry in ('650x460','480x360'):
                dialog.geometry(geometry)
                dialog.update()
                self.assertTrue(save.winfo_ismapped())
                self.assertLessEqual(save.winfo_rooty()+save.winfo_height(),dialog.winfo_rooty()+dialog.winfo_height())
            save.invoke()
            window.update()
            self.assertFalse(window.running)
            self.assertEqual(str(window.keep_private_btn['state']),'disabled')
            self.assertEqual(self.service.live_state()['abandoned_live_history'][0]['nomi']['reply'],'SAY: '+'x'*720)
            window.close()
        finally:
            for callback in parent.tk.call('after','info'):
                parent.after_cancel(callback)
            parent.destroy()


if __name__ == '__main__': unittest.main()
