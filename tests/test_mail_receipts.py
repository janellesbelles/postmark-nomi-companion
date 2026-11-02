"""Delivered replies remain protected without a duplicate local profile."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import mail_pipeline
import mail_receipts as receipts
from profile_store import ProfileStore
from test_live_sessions import app


def proof(thread='incoming',ident='outgoing',**overrides):
    return dict({'id':ident,'from':'resident-one','to':'neighbor','thread':thread,
                 'delivered_at':'2026-01-02T12:00:00Z'},**overrides)


class DeliveryFeedTests(unittest.TestCase):
    def test_all_pages_are_checked(self):
        pages=[[proof('letter-'+str(i),'reply-'+str(i)) for i in range(100)],[proof('last','last-reply')]]
        with patch.object(receipts,'request_json',side_effect=pages) as network:
            found=receipts.delivered_replies('resident-one')
        self.assertEqual(len(found),101)
        self.assertIn('offset=100',network.call_args.args[0])

    def test_foreign_mailbox_malformed_delivery_and_incomplete_feed_fail_closed(self):
        for page in ([proof(**{'from':'resident-two'})],[proof(delivered_at='not-a-time')],
                     [proof(delivered_at='2026-01-02T12:00:00')],{'letters':[],'complete':False},
                     {'unexpected':'shape'},[None]):
            with self.subTest(page=page),self.assertRaises(receipts.DeliveryHistoryError):
                receipts.delivered_replies('resident-one',request=lambda url:page)

    def test_queued_or_intro_letters_are_not_delivered_reply_evidence(self):
        found=receipts.delivered_replies('resident-one',request=lambda u:[proof(thread=None,ident='intro'),proof(delivered_at=None,ident='queued'),proof()])
        self.assertEqual(set(found),{'incoming'})

    def test_repeated_pages_do_not_silently_truncate_history(self):
        page=[proof('letter-'+str(i),'reply-'+str(i)) for i in range(100)]
        with self.assertRaises(receipts.DeliveryHistoryError):
            receipts.delivered_replies('resident-one',request=lambda url:page)


class ReceiptLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.profiles=ProfileStore(self.temp.name)
        self.ident=self.profiles.add(handle='resident-one',nomi_name='One',settings={})
        self.service=app.ResidentService(self.profiles,self.ident)
        self.path=self.profiles.mail_state_path(self.ident)
        self.history=receipts.DeliveryHistory(self.profiles.root)
        self.entry={'status':'pending','draft':'Do not resubmit this draft.',
            'fields':{'id':'incoming','from':'neighbor','to':'resident-one'},'body':'Incoming body.',
            'nomi_parts':2,'nomi_parts_completed':1,'ambiguous_part':2}
        self.write()

    def tearDown(self):
        self.temp.cleanup()

    def write(self,entry=None):
        ProfileStore._write(self.path,{'version':1,'seen':['incoming'],'letters':{'incoming':copy.deepcopy(entry or self.entry)}})

    def seed(self):
        self.history.refresh('resident-one',request=lambda url:[proof()])

    def test_opening_and_reopening_uses_local_receipts_without_network(self):
        self.seed()
        with patch('urllib.request.urlopen') as network:
            for i in range(2):
                entries=app.ResidentService(self.profiles,self.ident).entries()
                entry=entries['incoming']
                self.assertEqual(entry['status'],'pr_open')
                self.assertEqual(entry['draft'],'')
                self.assertTrue(entry['cleared_from_mailbox_at'])
                self.assertEqual(entry['delivery_receipt']['id'],'outgoing')
            network.assert_not_called()
        self.assertEqual(len(ProfileStore._read(self.path)['delivery_reconciliation_history']),1)

    def test_history_survives_rolling_back_only_resident_state(self):
        self.seed()
        self.history.reconcile('resident-one',self.path)
        self.write()  # stale resident file restored from a backup
        self.service.entries()
        self.assertTrue(ProfileStore._read(self.path)['letters']['incoming']['cleared_from_mailbox_at'])

    def test_empty_later_feed_does_not_erase_positive_proof(self):
        self.seed()
        self.history.refresh('resident-one',request=lambda url:[])
        self.assertIsNotNone(self.history.receipt_for('resident-one','incoming',self.entry))

    def test_new_profile_same_handle_keeps_ledger_but_other_resident_does_not_inherit(self):
        self.seed()
        other=self.profiles.add(handle='resident-two',nomi_name='Two',settings={})
        otherpath=self.profiles.mail_state_path(other)
        ProfileStore._write(otherpath,{'version':1,'seen':[],'letters':{'incoming':self.entry}})
        self.assertEqual(self.history.reconcile('resident-two',otherpath),0)
        self.assertIsNone(self.history.receipt_for('resident-two','incoming',self.entry))
        self.assertIsNotNone(receipts.DeliveryHistory(self.profiles.root).receipt_for('resident-one','incoming',self.entry))

    def test_different_sender_or_recipient_does_not_match(self):
        self.seed()
        for field,value in (('from','other-neighbor'),('to','resident-two')):
            entry=copy.deepcopy(self.entry)
            entry['fields'][field]=value
            self.assertIsNone(self.history.receipt_for('resident-one','incoming',entry))

    def test_uncertain_repeat_nomi_checkpoint_is_audited_but_not_resent(self):
        self.seed()
        entry=dict(self.entry,status='response_unknown',active_nomi_part=2,error='NoReply')
        self.write(entry)
        self.history.reconcile('resident-one',self.path)
        state=ProfileStore._read(self.path)
        audit=state['delivery_reconciliation_history'][0]
        self.assertTrue(audit['repeat_nomi_uncertain'])
        self.assertEqual(audit['checkpoint']['active_nomi_part'],2)
        self.assertEqual(state['letters']['incoming']['draft'],'')

    def test_uncertain_public_submission_and_rejected_records_are_not_unlocked(self):
        self.seed()
        for status in ('needs_send_check','submitting','rejected'):
            entry=dict(self.entry,status=status)
            self.write(entry)
            self.assertEqual(self.history.reconcile('resident-one',self.path),0)
            self.assertEqual(ProfileStore._read(self.path)['letters']['incoming'],entry)

    def test_known_answered_send_and_resume_gates_do_not_contact_nomi_or_github(self):
        self.seed()
        calls=[lambda:self.service.confirm_delivery('incoming'),lambda:self.service.retry_letter('incoming'),
               lambda:self.service.resend_after_timeout('incoming'),lambda:self.service.recover_reply('incoming','Copied reply.'),
               lambda:self.service.send_approved('incoming','Approved words.')]
        with patch('urllib.request.urlopen') as network,patch.object(self.service,'_send_nomi') as nomi,patch.object(app.github_mailer,'submit_approved') as submit:
            for call in calls:
                self.write()
                with self.assertRaisesRegex(ValueError,'already answered'): call()
            network.assert_not_called()
            nomi.assert_not_called()
            submit.assert_not_called()

    def test_failed_history_check_blocks_unknown_delivery_without_state_changes(self):
        before=ProfileStore._read(self.path)
        with (patch.object(receipts,'request_json',side_effect=TimeoutError('synthetic')),
              patch.object(self.service,'_send_nomi') as nomi):
            with self.assertRaises(receipts.DeliveryHistoryError): self.service.confirm_delivery('incoming')
            nomi.assert_not_called()
        self.assertEqual(ProfileStore._read(self.path),before)
        self.assertFalse(self.history.state.exists())

    def test_poll_never_refetches_or_stages_known_answered_history(self):
        pipeline=mail_pipeline.MailPipeline(resident_handle='resident-one',state_path=self.path,
            nomi_send=lambda text:self.fail('No send'),answered_ids={'incoming'},
            discover=lambda:[('incoming','old-url'),('fresh','new-url')],
            fetch=lambda url:({'id':'fresh','from':'neighbor','to':'resident-one'},'New body.'))
        pipeline.state.write({'version':1,'seen':[],'letters':{}})
        self.assertEqual(pipeline.poll(),['fresh'])
        self.assertNotIn('incoming',pipeline.entries())
        self.assertIn('incoming',pipeline.state.read()['seen'])

    def test_alias_filename_with_answered_header_id_is_not_offered(self):
        pipeline=mail_pipeline.MailPipeline(resident_handle='resident-one',state_path=self.path,
            nomi_send=lambda text:self.fail('No send'),answered_ids={'incoming'},discover=lambda:[('alias','url')],
            fetch=lambda url:(self.entry['fields'],'Old body.'))
        pipeline.state.write({'version':1,'seen':[],'letters':{}})
        self.assertEqual(pipeline.poll(),[])
        self.assertTrue(pipeline.entries()['alias']['cleared_from_mailbox_at'])

    def test_unreadable_ledger_is_not_treated_as_empty(self):
        ProfileStore._write(self.history.state.path,{'version':999,'residents':{}})
        with self.assertRaises(receipts.DeliveryHistoryError): self.history.receipts('resident-one')

    def test_existing_answered_waiting_entry_is_not_offered_or_sent(self):
        self.write(dict(self.entry,status='awaiting_nomi_confirmation'))
        pipeline=mail_pipeline.MailPipeline(resident_handle='resident-one',state_path=self.path,
            nomi_send=lambda text:self.fail('No send'),answered_ids={'incoming'},discover=lambda:[])
        self.assertEqual(pipeline.poll(),[])
        with self.assertRaisesRegex(ValueError,'already records a delivered reply'):
            pipeline.confirm_delivery('incoming')

    def test_low_level_github_boundary_refuses_known_delivered_reply(self):
        import github_mailer
        pipeline=mail_pipeline.MailPipeline(resident_handle='resident-one',state_path=self.path,
            nomi_send=lambda text:self.fail('No send'),answered_ids={'incoming'})
        before=ProfileStore._read(self.path)
        with patch.object(github_mailer,'create_mail_pr') as create:
            with self.assertRaisesRegex(ValueError,'No duplicate submission'):
                github_mailer.submit_approved(pipeline,'incoming','Duplicate',create_pr=create)
            create.assert_not_called()
        self.assertEqual(ProfileStore._read(self.path),before)
