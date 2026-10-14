"""Archive-only regression tests: synthetic residents, no network access."""
import importlib.machinery
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import mail_pipeline
import github_mailer
from profile_store import ProfileStore

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('archive_app_test', str(ROOT / 'Companion App.pyw'))
spec = importlib.util.spec_from_loader(loader.name, loader)
app_module = importlib.util.module_from_spec(spec)
loader.exec_module(app_module)


class ArchivePipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.calls = []
        self.pipeline = mail_pipeline.MailPipeline(resident_handle='resident-one',
            state_path=Path(self.temp.name)/'mail.json', discover=lambda:[],
            fetch=lambda source:({'id':'synthetic-letter','from':'neighbor','to':'resident-one'}, 'Synthetic incoming letter.'),
            nomi_send=lambda message:(self.calls.append(message) or 'reply','synthetic-reply'), part_pause=0)
        self.pipeline.initialize()
        self.pipeline.poll(discovered=[('letter','source')])

    def tearDown(self):
        self.temp.cleanup()

    def test_archive_unanswered_survives_poll_and_restore_without_send(self):
        before = dict(self.pipeline.entries()['letter'])
        self.pipeline.archive('letter')
        self.assertEqual(self.pipeline.poll(discovered=[('letter','source')]), [])
        restored = self.pipeline.restore('letter')
        for key,value in before.items():
            self.assertEqual(restored[key],value)
        self.assertEqual(self.calls, [])
        self.assertIn('letter',self.pipeline.state.read()['seen'])

    def test_archived_entries_cannot_send_edit_or_recover(self):
        self.pipeline.archive('letter')
        with self.assertRaises(ValueError): self.pipeline.confirm_delivery('letter')
        with self.assertRaises(ValueError): self.pipeline.recover_reply('letter','copied reply')
        state = self.pipeline.state.read()
        state['letters']['letter']['status'] = 'pending'
        self.pipeline.state.write(state)
        with self.assertRaises(ValueError): self.pipeline.save_draft('letter','changed')
        with patch.object(github_mailer,'create_mail_pr') as create:
            with self.assertRaises(ValueError): github_mailer.submit_approved(self.pipeline,'letter','changed',create_pr=create)
            create.assert_not_called()
        self.assertEqual(self.calls, [])

    def test_unknown_nomi_status_and_checkpoint_survive_archive_restart(self):
        state = self.pipeline.state.read()
        state['letters']['letter'].update(status='response_unknown',nomi_parts=3,nomi_parts_completed=1,ambiguous_part=2)
        self.pipeline.state.write(state)
        self.pipeline.archive('letter')
        with self.assertRaises(ValueError): self.pipeline.resend_after_human_check('letter',human_checked_chat=True,next_part=3)
        restarted = mail_pipeline.MailPipeline(resident_handle='resident-one',state_path=self.pipeline.state.path,nomi_send=lambda m:self.fail('No sends'))
        restored = restarted.restore('letter')
        self.assertEqual(restored['status'],'response_unknown')
        self.assertEqual(restored['nomi_parts_completed'],1)
        self.assertEqual(restored['ambiguous_part'],2)

    def test_submitted_and_active_work_cannot_be_archived(self):
        for status in ['contacting_nomi','reading','submitting','needs_send_check','pr_open','queued','delivered']:
            state = self.pipeline.state.read()
            state['letters']['letter']['status'] = status
            self.pipeline.state.write(state)
            with self.assertRaises(ValueError): self.pipeline.archive('letter')

    def test_submission_records_block_archive_even_if_status_is_pending(self):
        for field in ['pr','submission']:
            state = self.pipeline.state.read()
            for existing in ['pr','submission']:
                state['letters']['letter'].pop(existing, None)
            state['letters']['letter'].update(status='pending',**{field:{'nonce':'synthetic'}})
            self.pipeline.state.write(state)
            with self.assertRaises(ValueError): self.pipeline.archive('letter')


class ArchiveWindowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.profiles = ProfileStore(self.temp.name)
        self.first = self.profiles.add(handle='resident-one',nomi_name='One',settings={})
        self.second = self.profiles.add(handle='resident-two',nomi_name='Two',settings={})
        for profile in [self.first,self.second]:
            mail_pipeline.AtomicState(self.profiles.mail_state_path(profile)).write({'seen':['same'], 'letters':{'same':{
                'status':'awaiting_nomi_confirmation','fields':{'from':'neighbor'},'body':'Synthetic letter','draft':'Synthetic draft'}}})
        self.profiles.set_active(self.first)
        self.network = patch('urllib.request.urlopen', side_effect=AssertionError('No real APIs in archive tests'))
        self.network.start()
        self.app = app_module.App(self.profiles)
        self.app.update()

    def tearDown(self):
        for callback in self.app.tk.call('after','info'):
            self.app.after_cancel(callback)
        self.app.destroy()
        self.network.stop()
        self.temp.cleanup()

    def test_archive_restore_ui_preserves_other_resident_and_disables_send(self):
        with patch.object(app_module.messagebox,'askyesno',return_value=True):
            self.app.archive_mail()
        self.assertEqual(self.app.ids,[])
        self.app.show_archived.set(True)
        self.app.refresh()
        self.assertEqual(self.app.ids,['same'])
        self.assertEqual(str(self.app.retry_btn['state']),'disabled')
        self.assertEqual(str(self.app.send_btn['state']),'disabled')
        self.assertEqual(str(self.app.recover_btn['state']),'disabled')
        self.app.archive_mail()
        self.assertEqual(self.app.ids,['same'])
        self.assertFalse(self.app.show_archived.get())
        other = mail_pipeline.AtomicState(self.profiles.mail_state_path(self.second)).read()
        self.assertNotIn('archived_at',other['letters']['same'])

    def test_declined_archive_changes_nothing(self):
        with patch.object(app_module.messagebox,'askyesno',return_value=False):
            self.app.archive_mail()
        self.assertEqual(self.app.ids,['same'])
        self.assertNotIn('archived_at',self.app.service.entries()['same'])

    def test_archived_confirmation_queue_never_starts_delivery(self):
        self.app.service.pipeline().archive('same')
        with patch.object(self.app,'work') as work:
            self.app.review_nomi_deliveries(['same'])
            work.assert_not_called()

    def test_controls_fit_default_and_minimum_window(self):
        for geometry in ['1080x780','900x650']:
            self.app.geometry(geometry)
            self.app.update()
            button = self.app.archive_btn
            self.assertTrue(button.winfo_ismapped())
            self.assertLessEqual(button.winfo_rootx()+button.winfo_width(),self.app.winfo_rootx()+self.app.winfo_width())
            self.assertLessEqual(button.winfo_rooty()+button.winfo_height(),self.app.winfo_rooty()+self.app.winfo_height())

    def test_pending_edits_saved_before_archive(self):
        state = self.app.service.pipeline().state.read()
        state['letters']['same']['status'] = 'pending'
        self.app.service.pipeline().state.write(state)
        self.app.refresh()
        self.app.draft.delete('1.0','end')
        self.app.draft.insert('1.0','Last synthetic edit')
        with patch.object(app_module.messagebox,'askyesno',return_value=True):
            self.app.archive_mail()
        self.assertEqual(self.app.service.entries()['same']['draft'],'Last synthetic edit')

    def test_archive_filter_saves_pending_edits_before_switching_view(self):
        state = self.app.service.pipeline().state.read()
        state['letters']['same']['status'] = 'pending'
        self.app.service.pipeline().state.write(state)
        self.app.refresh()
        self.app.draft.delete('1.0','end')
        self.app.draft.insert('1.0','Edit before changing the view')
        self.app.archived_toggle.invoke()
        self.assertEqual(self.app.service.entries()['same']['draft'],'Edit before changing the view')
        self.assertEqual(self.app.ids,[])


if __name__ == '__main__': unittest.main()
