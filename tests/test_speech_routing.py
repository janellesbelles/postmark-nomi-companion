"""Explicit speech after private narration uses the normal approval/send gate."""
import queue
import unittest
from unittest.mock import patch
import event_bridge as bridge
import test_live_sessions as sessions


WORDS="Excuse me! Over here! I'm your neighbor! Come talk to me!"


class SpeechRoutingTests(unittest.TestCase):
    def run_reply(self,reply,**kwargs):
        return sessions.LiveSessionTests().run_session(reply=reply,**kwargs)

    def test_inline_narration_extracts_only_words_and_posts_once_as_selected_resident(self):
        reply='*I waddle into the square and glance around, waving one fin.* SAY: '+WORDS
        session,decisions,posted=self.run_reply(reply)
        self.assertEqual(decisions,[WORDS])
        self.assertEqual(posted.call_count,1)
        self.assertEqual(posted.call_args.args[:3],('fake-key','resident-one',WORDS))
        self.assertEqual(session.service.saved[-1]['status'],'confirmed')
        self.assertEqual([value for kind,value in session.tasks.queue if kind=='spoke'],[WORDS])
        self.assertFalse(any(kind=='private' for kind,value in session.tasks.queue))

    def test_narration_before_newline_say_also_uses_approval(self):
        reply='Private thoughts about the afternoon.\n*I wave.*\nSAY: '+WORDS
        session,decisions,posted=self.run_reply(reply,approved=False)
        self.assertEqual(decisions,[WORDS])
        posted.assert_not_called()
        self.assertEqual(session.service.saved[-1]['status'],'kept_private')

    def test_trailing_actions_stay_private_and_multiline_speech_is_preserved(self):
        self.assertEqual(bridge.speech_from_nomi('*I smile.* SAY: Hello! *I wave.*'),'Hello!')
        self.assertEqual(bridge.speech_from_nomi('SAY: First line.\nSecond line.\n\n*I think privately.*'),
                         'First line.\nSecond line.')
        self.assertEqual(bridge.speech_from_nomi('**I wave.** say: Hello!'),'Hello!')

    def test_mentions_quotes_and_code_examples_are_not_public_intent(self):
        for reply in ('I might write SAY: hello.', '> SAY: quoted words',
                      '```text\nSAY: example\n```','~~~\nSAY: example\n~~~',
                      '*I think about the word SAY: privately.*'):
            with self.subTest(reply=reply),self.assertRaises(bridge.UnmarkedNomiReply):
                bridge.speech_from_nomi(reply)

    def test_ambiguous_or_mixed_public_blocks_need_manual_review(self):
        for reply in ('SAY: Hello\nSAY: Goodbye','SAY:','*I wave.* SAY: Hello *I move* friend.',
                      'SAY: Hello\n```private action```'):
            with self.subTest(reply=reply):
                with self.assertRaises(ValueError) as error:
                    bridge.speech_from_nomi(reply)
                self.assertNotIsInstance(error.exception,bridge.UnmarkedNomiReply)

    def test_overlong_say_after_narration_is_held_not_consumed_as_private(self):
        session,decisions,posted=self.run_reply('*I wave.* SAY: '+'x'*501)
        self.assertEqual(decisions,[])
        posted.assert_not_called()
        self.assertEqual(session.service.live_state()['nomi']['status'],'reply_ready')
        self.assertTrue(any(kind=='relay_error' for kind,value in session.tasks.queue))
        self.assertFalse(any('replied privately' in str(value) for kind,value in session.tasks.queue))

    def test_failed_public_post_is_unknown_and_never_logs_published(self):
        session,decisions,posted=self.run_reply('*I wave.* SAY: '+WORDS,fail_speech=True)
        self.assertEqual(posted.call_count,1)
        self.assertEqual(session.service.saved[-1]['status'],'unknown')
        self.assertTrue(any(kind=='relay_error' for kind,value in session.tasks.queue))
        self.assertFalse(any(kind=='spoke' for kind,value in session.tasks.queue))

    def test_confirmed_receipt_survives_replay_without_second_post(self):
        reply='*I wave.* SAY: '+WORDS
        session,decisions,posted=self.run_reply(reply)
        confirmed=session.service.state['speech']
        session.service.save_live_nomi({'status':'reply_ready','packets':[session.service.packets[0]],
            'reply':reply,'request_id':confirmed['request_id']})
        session.stop_event.clear()
        session.tasks=queue.Queue()
        with (patch.object(session.service,'_send_nomi') as nomi,patch.object(bridge,'speak') as public,
              patch.object(session,'approve_live_speech') as approve):
            session.relay_loop(recovery_only=True)
        nomi.assert_not_called()
        public.assert_not_called()
        approve.assert_not_called()
        self.assertEqual(session.service.state['speech']['nonce'],confirmed['nonce'])
        self.assertEqual(session.service.state['nomi']['status'],'resolved')

    def test_success_requires_boolean_spoke_confirmation(self):
        for result in ({'spoke':False},{'spoke':'false'},{'duplicate':True},{'spoke':None}):
            with self.subTest(result=result),patch.object(bridge,'request_json',return_value=result):
                with self.assertRaises(RuntimeError):
                    bridge.speak('synthetic-key','resident-one',WORDS,nonce='same-nonce')
