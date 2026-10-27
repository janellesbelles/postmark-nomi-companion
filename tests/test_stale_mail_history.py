"""Same-resident stale copies cannot revive protected mail or send twice."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from profile_store import ProfileStore
import mail_pipeline
from test_live_sessions import app


class StaleMailHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.profiles=ProfileStore(self.temp.name)
        self.ident=self.profiles.add(handle='resident-one',nomi_name='One',settings={})
        self.service=app.ResidentService(self.profiles,self.ident)
        self.duplicate=self.profiles.residents_root/'synthetic-older-copy'
        self.duplicate.mkdir()
        ProfileStore._write(self.duplicate/'settings.json',{'resident_handle':'RESIDENT-ONE'})
        self.active={'version':1,'seen':['letter'],'letters':{'letter':{
            'status':'pending','draft':'Unsent local draft.','fields':{'id':'letter','from':'neighbor','to':'resident-one'}}}}
        self.finished={'version':1,'seen':['letter'],'letters':{'letter':{
            'status':'pr_open','pr':'https://example.invalid/pull/1','cleared_from_mailbox_at':123}}}
        self.save()

    def tearDown(self):
        self.temp.cleanup()

    def save(self):
        ProfileStore._write(self.profiles.mail_state_path(self.ident),self.active)
        ProfileStore._write(self.duplicate/'mail-state.json',self.finished)

    def test_stale_pending_copy_is_blocked_at_all_delivery_and_send_gates(self):
        methods=[lambda:self.service.initialize(),lambda:self.service.poll(),
            lambda:self.service.confirm_delivery('letter'),lambda:self.service.retry_letter('letter'),
            lambda:self.service.resend_after_timeout('letter'),
            lambda:self.service.recover_reply('letter','New copied draft.'),
            lambda:self.service.send_approved('letter','Approved text.')]
        before=self.profiles._read(self.profiles.mail_state_path(self.ident))
        with patch('urllib.request.urlopen') as network,patch('github_mailer.create_mail_pr') as github:
            for method in methods:
                with self.assertRaisesRegex(ValueError,'prevent a duplicate'):
                    method()
            network.assert_not_called()
            github.assert_not_called()
        self.assertEqual(self.profiles._read(self.profiles.mail_state_path(self.ident)),before)

    def test_uncertain_outgoing_copy_is_also_protected(self):
        for status in ('submitting','needs_send_check'):
            self.finished['letters']['letter']={'status':status}
            self.save()
            with self.assertRaisesRegex(ValueError,'prevent a duplicate'):
                self.profiles.assert_mail_history_safe(self.ident,'letter')

    def test_matching_receipts_and_seen_baselines_are_safe(self):
        self.active=copy.deepcopy(self.finished)
        self.save()
        self.profiles.assert_mail_history_safe(self.ident)
        self.active['letters']={}
        self.save()
        self.profiles.assert_mail_history_safe(self.ident)

    def test_missing_seen_marker_blocks_reimport_before_poll(self):
        self.active=copy.deepcopy(self.finished)
        self.active['seen']=[]
        self.save()
        with self.assertRaisesRegex(ValueError,'prevent a duplicate'):
            self.profiles.assert_mail_history_safe(self.ident)

    def test_unrelated_resident_and_new_letters_remain_independent(self):
        self.profiles.assert_mail_history_safe(self.ident,'new-letter')
        ProfileStore._write(self.duplicate/'settings.json',{'resident_handle':'resident-two'})
        self.profiles.assert_mail_history_safe(self.ident)

    def test_unreadable_same_resident_history_fails_closed(self):
        ProfileStore._write(self.duplicate/'mail-state.json',{'letters':{}})
        with patch.object(ProfileStore,'_read',side_effect=[self.active,{'resident_handle':'resident-one'},ValueError('malformed')]):
            with self.assertRaisesRegex(ValueError,'unreadable mail history'):
                self.profiles.assert_mail_history_safe(self.ident)

    def test_poll_preserves_all_durable_entries_when_only_seen_was_lost(self):
        for status in ('pending','response_unknown','awaiting_nomi_confirmation','pr_open','needs_send_check'):
            original={'status':status,'draft':'Preserve unchanged.','nomi_parts_completed':2,
                'nomi_parts':3,'ambiguous_part':3,'archived_at':456}
            path=Path(self.temp.name)/'standalone.json'
            mail_pipeline.AtomicState(path).write({'version':1,'seen':[],'letters':{'letter':original}})
            with patch('urllib.request.urlopen') as network:
                pipeline=mail_pipeline.MailPipeline(resident_handle='resident-one',state_path=path,
                    discover=lambda:[('letter','synthetic-source')],fetch=lambda s:self.fail('No refetch'),
                    nomi_send=lambda s:self.fail('No Nomi call'))
                self.assertEqual(pipeline.poll(),[])
                self.assertEqual(pipeline.state.read()['letters']['letter'],original)
                self.assertIn('letter',pipeline.state.read()['seen'])
                network.assert_not_called()
