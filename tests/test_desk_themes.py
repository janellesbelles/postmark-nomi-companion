"""Synthetic native UI checks. No real profiles or live API calls."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import desk_themes
import lamplit_ui
from profile_store import ProfileStore
from test_live_sessions import app

ROOT = Path(__file__).resolve().parents[1]


class DeskThemeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.profiles = ProfileStore(self.temp.name)
        self.first = self.profiles.add(handle='resident-one',nomi_name='One',settings={})
        self.second = self.profiles.add(handle='resident-two',nomi_name='Two',settings={})
        self.profiles.set_active(self.first)
        self.block = patch('urllib.request.urlopen',side_effect=AssertionError('No live APIs'))
        self.block.start()
        self.window = app.App(self.profiles)

    def tearDown(self):
        for callback in self.window.tk.splitlist(self.window.tk.call('after','info')):
            self.window.after_cancel(callback)
        self.window.destroy()
        self.block.stop()
        self.temp.cleanup()

    def letter(self, status='pending'):
        pipeline = self.window.service.pipeline()
        state = {'version':1,'seen':['synthetic'],'letters':{}}
        state['letters'] = {'synthetic':{'status':status,'fields':{'from':'neighbor'},
            'body':'A synthetic letter.','draft':'A saved synthetic draft.',
            'nomi_parts':3,'nomi_parts_completed':1,'ambiguous_part':2}}
        pipeline.state.write(state)
        self.window.refresh()

    def test_six_palettes_survive_restart_without_changing_resident_data(self):
        self.assertEqual(len(desk_themes.THEMES),6)
        self.letter('response_unknown')
        before = self.window.service.pipeline().state.read()
        settings = self.profiles.read_settings(self.first)
        self.window.geometry('900x650')
        for name in desk_themes.THEMES:
            self.window.theme_choice.set(name)
            self.window.change_theme()
            self.window.update()
            self.assertEqual(self.window.incoming.cget('bg'),desk_themes.palette(name)['PAPER'])
            self.assertEqual(self.window.service.pipeline().state.read(),before)
            self.assertEqual(self.profiles.read_settings(self.first),settings)
            self.assertEqual(desk_themes.load_theme(ProfileStore(self.temp.name)),name)
            for widget in (self.window.theme_combo,self.window.resident_combo,self.window.retry_btn,
                           self.window.recover_btn,self.window.resend_btn):
                self.assertTrue(widget.winfo_ismapped())
                self.assertLessEqual(widget.winfo_rooty()+widget.winfo_height(),self.window.winfo_rooty()+650)

    def test_theme_keeps_unsaved_draft_selection_and_unknown_locks(self):
        self.letter()
        self.window.draft.delete('1.0','end')
        self.window.draft.insert('1.0','Still editing this draft.')
        selected = self.window.listbox.curselection()
        self.window.theme_choice.set('Rosewater')
        self.window.change_theme()
        self.assertEqual(self.window.draft.get('1.0','end-1c'),'Still editing this draft.')
        self.assertEqual(self.window.listbox.curselection(),selected)
        self.assertEqual(self.window.current,'synthetic')
        self.assertEqual(self.window.service.pipeline().entries()['synthetic']['draft'],'A saved synthetic draft.')

    def test_live_and_recovery_dialog_theme_changes_do_not_restart_or_unlock(self):
        self.window.service.save_live_nomi({'status':'response_unknown','packets':['saved exact packet']})
        self.window.service.save_live_speech({'status':'unknown','text':'Synthetic uncertain speech.'})
        with patch.object(app.LiveEventsWindow,'refresh_calendar'):
            live = app.LiveEventsWindow(self.window,self.window.service)
        self.window.events_window = live
        before = self.window.service.live_state()
        live.geometry('760x650')
        for name in desk_themes.THEMES:
            self.window.theme_choice.set(name)
            self.window.change_theme()
            self.window.update()
            self.assertEqual(live.transcript.cget('bg'),desk_themes.palette(name)['PANEL'])
            self.assertEqual(str(live.keep_private_btn['state']),'disabled')
            self.assertEqual(str(live.paste_live_btn['state']),'disabled')
            self.assertFalse(live.running)
            self.assertEqual(self.window.service.live_state(),before)
            self.assertTrue(live.toggle_btn.winfo_ismapped())
        live.close()

    def test_archived_filter_still_autosaves_draft_and_restore_is_available(self):
        self.letter()
        self.window.draft.delete('1.0','end')
        self.window.draft.insert('1.0','Edited before filtering.')
        self.window.archived_toggle.invoke()
        self.assertEqual(self.window.service.pipeline().entries()['synthetic']['draft'],'Edited before filtering.')
        self.assertEqual(self.window.ids,[])

    def test_unknown_saved_theme_falls_back_and_invalid_choice_is_not_written(self):
        self.profiles.index['ui_theme']='not-a-theme'
        self.assertEqual(desk_themes.load_theme(self.profiles),desk_themes.DEFAULT)
        with self.assertRaises(ValueError): desk_themes.save_theme(self.profiles,'invalid')

    def test_pending_minimum_layout_keeps_review_and_theme_controls_inside_window(self):
        self.letter()
        self.window.geometry('900x650')
        self.window.update()
        for widget in (self.window.send_btn,self.window.save_btn,self.window.archive_btn,
                       self.window.theme_combo,self.window.draft):
            self.assertTrue(widget.winfo_ismapped())
            self.assertGreater(widget.winfo_height(),15)
            self.assertLessEqual(widget.winfo_rooty()+widget.winfo_height(),self.window.winfo_rooty()+650)
            self.assertLessEqual(widget.winfo_rootx()+widget.winfo_width(),self.window.winfo_rootx()+900)

    def test_theme_save_failure_preserves_previous_palette(self):
        previous=self.window.theme_name
        self.window.theme_choice.set('Rosewater')
        with patch.object(self.profiles,'save',side_effect=OSError('Synthetic write error')), patch.object(app.messagebox,'showerror'):
            self.window.change_theme()
        self.assertEqual(self.window.theme_name,previous)
        self.assertEqual(self.window.theme_choice.get(),previous)
        self.assertEqual(desk_themes.load_theme(self.profiles),previous)

    def test_letter_list_adapter_keeps_selection_and_busy_keyboard_lock(self):
        self.letter()
        self.assertEqual(self.window.listbox.curselection(),(0,))
        self.window.listbox.configure(state='disabled')
        self.assertTrue(self.window.listbox._disabled)
        self.window.listbox.configure(state='normal')
        self.assertFalse(self.window.listbox._disabled)
        self.window.listbox.delete(0,'end')
        self.assertEqual(self.window.listbox.curselection(),())

    def test_palette_contrast_is_readable(self):
        def luminance(color):
            channels = [int(color[i:i+2],16)/255 for i in (1,3,5)]
            channels = [v/12.92 if v<=0.04045 else ((v+0.055)/1.055)**2.4 for v in channels]
            return sum(v*w for v,w in zip(channels,(0.2126,0.7152,0.0722)))
        for name in desk_themes.THEMES:
            p=desk_themes.palette(name)
            for fg,bg in [('TEXT','NIGHT'),('TEXT','PANEL'),('MUTED','PANEL'),('GOLD','SIDEBAR'),('PAPER_INK','PAPER'),('NIGHT','GOLD')]:
                a,b=sorted((luminance(p[fg]),luminance(p[bg])))
                self.assertGreaterEqual((b+0.05)/(a+0.05),4.5,(name,fg,bg))


if __name__=='__main__':
    unittest.main()
