"""Lamplit Desk presentation only: no network, keys, migration or delivery logic.

Builders wire the existing controller methods and preserve its widget contract.
All drawings are local Tk canvas artwork; no remote assets are required.
"""
from __future__ import annotations

import json
import tkinter as tk
from tkinter import ttk
import desk_themes
import banner_art

NIGHT = '#101d2b'
PANEL = '#172838'
SIDEBAR = '#142431'
SOFT = '#1c3040'
LINE = '#344655'
TEXT = '#e7e9e4'
MUTED = '#b6c4cb'
GOLD = '#e3bc7d'
PAPER = '#f3ead9'
PAPER_INK = '#302e2a'
GREEN = '#aad1b9'
SERIF = 'Georgia'
SANS = 'Segoe UI'
THEME_NAME = desk_themes.DEFAULT


def set_palette(name):
    global THEME_NAME
    THEME_NAME = name
    globals().update(desk_themes.palette(name))


def label(parent, text='', *, size=10, color=None, bg=None, serif=False, **kwargs):
    color = TEXT if color is None else color
    bg = NIGHT if bg is None else bg
    return tk.Label(parent, text=text, font=(SERIF if serif else SANS, size),
                    fg=color, bg=bg, **kwargs)


def configure_theme(root):
    root.option_add('*Font', (SANS, 10))
    root.option_add('*Background', NIGHT)
    root.option_add('*Foreground', TEXT)
    root.option_add('*selectBackground', SOFT)
    root.option_add('*selectForeground', GOLD)
    root.option_add('*Entry.background', PANEL)
    root.option_add('*Entry.foreground', TEXT)
    root.option_add('*Entry.insertBackground', GOLD)
    root.option_add('*Text.background', PAPER)
    root.option_add('*Text.foreground', PAPER_INK)
    root.option_add('*Text.insertBackground', PAPER_INK)
    root.option_add('*Button.background', PANEL)
    root.option_add('*Button.foreground', TEXT)
    root.option_add('*Button.activeBackground', SOFT)
    root.option_add('*Button.activeForeground', GOLD)
    root.option_add('*Button.disabledForeground', '#84929b')
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('TCombobox', fieldbackground=PANEL, background=PANEL,
                    foreground=TEXT, arrowcolor=GOLD, bordercolor=LINE, padding=5)
    style.map('TCombobox', fieldbackground=[('readonly', PANEL), ('disabled', SIDEBAR)],
              foreground=[('readonly', TEXT), ('disabled', MUTED)],
              selectbackground=[('readonly', PANEL)], selectforeground=[('readonly', TEXT)])
    style.configure('TCheckbutton', background=PANEL, foreground=TEXT)
    style.map('TCheckbutton', background=[('active', PANEL)])
    style.configure('Vertical.TScrollbar', background=LINE, troughcolor=PANEL,
                    arrowcolor=MUTED, bordercolor=PANEL)
    style.map('Vertical.TScrollbar',background=[('active',SOFT),('!active',LINE)])
    style.configure('Lamplit.Treeview',background=PANEL,fieldbackground=PANEL,foreground=TEXT,
                    rowheight=60,font=(SANS,10),borderwidth=0,indent=8)
    style.map('Lamplit.Treeview',background=[('selected',SOFT)],foreground=[('selected',GOLD)])


class LetterList(ttk.Treeview):
    """Two-line mailbox rows with the existing controller's Listbox contract."""
    def __init__(self,parent):
        super().__init__(parent,show='tree',selectmode='browse',style='Lamplit.Treeview',height=5)
        self.column('#0',stretch=True,width=150,minwidth=80)
        self._labels=[]
        self._disabled=False
        for sequence in ('<ButtonPress-1>', '<KeyPress>'):
            self.bind(sequence,lambda _e:'break' if self._disabled else None)
        for sequence in ('<ButtonRelease-1>','<KeyRelease-Up>','<KeyRelease-Down>','<KeyRelease-Home>','<KeyRelease-End>'):
            self.bind(sequence,self._notify)

    def _notify(self,_event=None):
        if not self._disabled:
            self.event_generate('<<ListboxSelect>>')

    def configure(self,cnf=None,**kwargs):
        if 'state' in kwargs:
            self._disabled=kwargs.pop('state')=='disabled'
            self.state(['disabled'] if self._disabled else ['!disabled'])
        return super().configure(cnf,**kwargs)

    config=configure

    def insert(self,index,text):
        sender,separator,status=text.partition(' · ')
        display=sender+('\n'+status if separator else '')
        item=super().insert('',index,text=display)
        self._labels.append((item,text))
        return item

    def delete(self,first,last=None):
        if first==0 and last=='end':
            items=self.get_children()
            if items:
                super().delete(*items)
            self._labels.clear()
        else:
            raise ValueError('Mailbox rows are refreshed as one resident-scoped list.')

    def selection_set(self,index):
        items=self.get_children()
        if 0<=int(index)<len(items):
            super().selection_set(items[int(index)])
            self.see(items[int(index)])

    def curselection(self):
        selected=set(self.selection())
        return tuple(i for i,item in enumerate(self.get_children()) if item in selected)


class LamplitBanner(tk.Canvas):
    """Responsive, non-interactive artwork unique to the selected desk theme."""
    def __init__(self, parent, caption=None, height=96):
        super().__init__(parent, height=height, bg=PANEL, bd=0, highlightthickness=0,
                         takefocus=False)
        self.caption = caption
        self.bind('<Configure>', self.redraw)

    def redraw(self, _event=None):
        self.delete('all')
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4 or h < 4:
            return
        self.configure(bg=PANEL)
        banner_art.draw(self, THEME_NAME, w, h, desk_themes.palette(THEME_NAME), self.caption)


def paper_text(parent, height=1):
    return tk.Text(parent, height=height, wrap='word', undo=True,
                   bg=PAPER, fg=PAPER_INK, insertbackground=PAPER_INK,
                   selectbackground=GOLD, selectforeground=NIGHT,
                   relief='flat', padx=22, pady=16, font=(SERIF, 12),
                   spacing1=3, spacing3=7, borderwidth=0)


def scroll_text(parent, widget):
    scroll = ttk.Scrollbar(parent, orient='vertical', command=widget.yview)
    widget.configure(yscrollcommand=scroll.set)
    return scroll


def build_mail(app):
    configure_theme(app)
    app.geometry('1180x850')
    app.minsize(900, 650)
    app.status = tk.StringVar(value='Ready. Check mail collects letters only.')
    footer = tk.Frame(app, bg=SIDEBAR, highlightbackground=LINE, highlightthickness=1)
    footer.pack(side='bottom', fill='x')
    app.active_identity = tk.StringVar()
    label(footer, textvariable=app.active_identity, bg=SIDEBAR, color=GOLD, size=9).pack(side='left',padx=16,pady=9)
    status_label = label(footer, textvariable=app.status, bg=SIDEBAR, color=MUTED,
                         size=9, anchor='w', justify='left', wraplength=820)
    status_label.pack(side='left',padx=12,pady=9,fill='x',expand=True)
    footer.bind('<Configure>',lambda e:status_label.configure(wraplength=max(180,e.width-260)))

    shell=tk.Frame(app,bg=NIGHT)
    shell.pack(fill='both',expand=True)
    sidebar=tk.Frame(shell,bg=SIDEBAR,width=178)
    sidebar.pack(side='left',fill='y')
    sidebar.pack_propagate(False)
    label(sidebar,'✦  Postmark',serif=True,size=19,color=GOLD,bg=SIDEBAR).pack(anchor='w',padx=12,pady=(29,0))
    label(sidebar,'A little light at your desk',size=9,color=MUTED,bg=SIDEBAR).pack(anchor='w',padx=17,pady=(4,25))
    label(sidebar,'YOUR RESIDENT',size=9,color=MUTED,bg=SIDEBAR).pack(anchor='w',padx=17,pady=(0,7))
    app.resident_choice=tk.StringVar()
    app.resident_combo=ttk.Combobox(sidebar,textvariable=app.resident_choice,state='readonly',width=16,font=(SANS,10,'bold'))
    app.resident_combo.pack(fill='x',padx=12,pady=(0,24))
    app.resident_combo.bind('<<ComboboxSelected>>',app.switch_resident)
    label(sidebar,textvariable=app.active_identity,bg=SIDEBAR,color=GOLD,size=10,
          wraplength=150,justify='left').pack(fill='x',padx=14,pady=(0,14))
    app.identity_bar=sidebar
    app.button(sidebar,'✉  Mailbox',lambda:app.listbox.focus_set(),SOFT).pack(fill='x',padx=10,pady=4)
    app.button(sidebar,'♧  Live conversations',app.open_live_events,PANEL).pack(fill='x',padx=10,pady=4)
    app.button(sidebar,'⚙  Resident settings',app.edit_resident,PANEL).pack(fill='x',padx=10,pady=4)
    app.resident_menu=tk.Menu(app,tearoff=False,bg=PANEL,fg=TEXT,activebackground=SOFT,activeforeground=GOLD)
    for name,command in [('Add resident…',app.add_resident),('Edit resident…',app.edit_resident),
                         ('Set as default',app.set_default),('Remove resident…',app.remove_resident),
                         ('Resident home ↗',app.open_home)]:
        app.resident_menu.add_command(label=name,command=command)
    def manage():
        if not app.busy:
            app.resident_menu.tk_popup(app.manage_btn.winfo_rootx(),app.manage_btn.winfo_rooty()+app.manage_btn.winfo_height())
    app.manage_btn=app.button(sidebar,'Manage residents…',manage,PANEL)
    app.manage_btn.pack(fill='x',padx=10,pady=(18,4))
    label(sidebar,'You review every\npublic message.',color=MUTED,bg=SIDEBAR,size=10,justify='left').pack(side='bottom',anchor='w',padx=17,pady=23)

    main=tk.Frame(shell,bg=NIGHT)
    main.pack(side='left',fill='both',expand=True)
    label(sidebar,'DESK THEME',size=9,color=MUTED,bg=SIDEBAR).pack(anchor='w',padx=17,pady=(12,4))
    app.theme_choice=tk.StringVar(value=app.theme_name)
    app.theme_combo=ttk.Combobox(sidebar,textvariable=app.theme_choice,values=list(app.themes),state='readonly',width=16)
    app.theme_combo.pack(fill='x',padx=12,pady=(0,8))
    app.theme_combo.bind('<<ComboboxSelected>>',app.change_theme)
    label(sidebar,textvariable=app.theme_choice,bg=SIDEBAR,color=MUTED,size=9,
          wraplength=150,justify='left').pack(fill='x',padx=14,pady=(0,6))
    dropdown=app.theme_combo.tk.call('ttk::combobox::PopdownWindow',str(app.theme_combo))
    app.theme_combo.tk.call(str(dropdown)+'.f.l','configure','-width',28)
    app.mail_banner=LamplitBanner(main)
    app.mail_banner.pack(fill='x')
    heading=tk.Frame(main,bg=NIGHT)
    heading.pack(fill='x',padx=22,pady=(17,13))
    app.check_btn=app.button(heading,'✉  Check mail',app.check_mail,GOLD)
    app.check_btn.pack(side='right',padx=(10,0))
    label(heading,'Your writing desk',serif=True,size=23).pack(anchor='w')
    heading_note=label(heading,'Check mail collects letters. Sending to your Nomi is a separate choice.',size=9,color=MUTED,wraplength=600,justify='left')
    heading_note.pack(anchor='w',pady=(4,0))
    heading.bind('<Configure>',lambda e:heading_note.configure(wraplength=max(180,e.width-155)))

    body=tk.Frame(main,bg=NIGHT)
    body.pack(fill='both',expand=True,padx=20,pady=(0,18))
    body.columnconfigure(1,weight=1)
    body.rowconfigure(0,weight=1)
    mailbox=tk.Frame(body,bg=PANEL,width=202)
    mailbox.grid(row=0,column=0,sticky='nsew',padx=(0,18))
    mailbox.grid_propagate(False)
    mailbox.columnconfigure(0,weight=1)
    mailbox.rowconfigure(2,weight=1)
    label(mailbox,'IN YOUR MAILBOX',size=9,color=GOLD,bg=PANEL).grid(row=0,column=0,columnspan=2,sticky='w',padx=12,pady=(14,6))
    app.show_archived=tk.BooleanVar(value=False)
    app.archived_toggle=tk.Checkbutton(mailbox,text='Show archived mail',variable=app.show_archived,command=app.toggle_archived,
        bg=PANEL,fg=TEXT,selectcolor=NIGHT,activebackground=PANEL,activeforeground=GOLD,font=(SANS,9))
    app.archived_toggle.grid(row=1,column=0,columnspan=2,sticky='w',padx=9,pady=5)
    app.controls.append(app.archived_toggle)
    app.listbox=LetterList(mailbox)
    app.listbox.grid(row=2,column=0,sticky='nsew',padx=(9,0),pady=8)
    app.listbox.bind('<<ListboxSelect>>',app.select)
    list_scroll=ttk.Scrollbar(mailbox,orient='vertical',command=app.listbox.yview)
    list_scroll.grid(row=2,column=1,sticky='ns',pady=8)
    app.listbox.configure(yscrollcommand=list_scroll.set)
    label(mailbox,'Letters and drafts stay\ninside this resident’s profile.',size=9,color=MUTED,bg=PANEL,justify='left').grid(row=3,column=0,columnspan=2,sticky='w',padx=12,pady=14)

    right=tk.Frame(body,bg=NIGHT)
    right.grid(row=0,column=1,sticky='nsew')
    right.columnconfigure(0,weight=1)
    right.rowconfigure(2,weight=3)
    right.rowconfigure(4,weight=2)
    app.reader_frame=right
    app.detail=label(right,'Choose a letter',serif=True,size=15,color=GOLD,anchor='w',justify='left')
    app.detail.grid(row=0,column=0,sticky='ew',pady=(0,9))
    right.bind('<Configure>',lambda e:app.detail.configure(wraplength=max(200,e.width-10)))
    incoming_bar=tk.Frame(right,bg=NIGHT)
    incoming_bar.grid(row=1,column=0,sticky='ew',pady=(0,6))
    label(incoming_bar,'INCOMING LETTER',size=9,color=MUTED).pack(side='left')
    app.retry_btn=app.button(incoming_bar,'Send to Nomi…',app.retry_letter,GOLD)
    app.retry_btn.pack(side='right')
    paper=tk.Frame(right,bg=PAPER)
    paper.grid(row=2,column=0,sticky='nsew')
    paper.columnconfigure(0,weight=1)
    paper.rowconfigure(0,weight=1)
    app.incoming=paper_text(paper)
    app.incoming.grid(row=0,column=0,sticky='nsew')
    scroll_text(paper,app.incoming).grid(row=0,column=1,sticky='ns')
    letter_actions=tk.Frame(right,bg=NIGHT)
    letter_actions.grid(row=3,column=0,sticky='ew',pady=(6,9))
    app.archive_btn=app.button(letter_actions,'Archive mail…',app.archive_mail,PANEL)
    app.archive_btn.pack(side='left')
    app.button(letter_actions,'Delivery details…',lambda:delivery_details(app),PANEL).pack(side='right')

    app.draft_section=tk.Frame(right,bg=NIGHT)
    app.draft_section.grid(row=4,column=0,sticky='nsew')
    app.draft_section.columnconfigure(0,weight=1)
    app.draft_section.rowconfigure(1,weight=1)
    label(app.draft_section,'Your Nomi’s reply · held for you',serif=True,size=15).grid(row=0,column=0,columnspan=2,sticky='w',pady=(2,7))
    app.draft=paper_text(app.draft_section)
    app.draft.grid(row=1,column=0,sticky='nsew')
    scroll_text(app.draft_section,app.draft).grid(row=1,column=1,sticky='ns')
    actions=tk.Frame(app.draft_section,bg=NIGHT)
    actions.grid(row=2,column=0,columnspan=2,sticky='ew',pady=(8,0))
    actions.columnconfigure(1,weight=1)
    app.save_btn=app.button(actions,'Save edits',app.save,PANEL)
    app.save_btn.grid(row=0,column=0,sticky='w',padx=(0,8))
    app.reject_btn=app.button(actions,'Keep unsent',app.dismiss_current,PANEL)
    app.reject_btn.grid(row=0,column=1,sticky='w')
    app.send_btn=app.button(actions,'Review & mail…',app.send,GOLD)
    app.send_btn.grid(row=0,column=2,sticky='e',padx=(8,0))
    app.recovery_frame=tk.Frame(right,bg=PANEL)
    app.recovery_frame.grid(row=5,column=0,sticky='ew',pady=(10,0))
    label(app.recovery_frame,'NEXT STEP / RECOVERY',size=9,color=GOLD,bg=PANEL).grid(row=0,column=0,columnspan=2,sticky='w',padx=10,pady=(8,3))
    app.continue_btn=app.button(app.recovery_frame,'Continue letter…',app.retry_letter,GOLD)
    app.recover_btn=app.button(app.recovery_frame,'Paste reply…',app.recover_reply,PANEL)
    app.resend_btn=app.button(app.recovery_frame,'Resend after check…',app.resend_after_timeout,PANEL)
    app.pr_btn=app.button(app.recovery_frame,'Check receipt',app.open_pr,PANEL)
    app.recovery_buttons=[app.continue_btn,app.recover_btn,app.resend_btn,app.pr_btn]


def sync_mail(app):
    """Only presentation: derive visibility from controller-assigned states."""
    if not hasattr(app,'recovery_buttons'):
        return
    entry=app.service.entries().get(app.current,{}) if app.service and app.current else {}
    show_draft=bool(entry.get('draft')) or entry.get('status')=='pending'
    if show_draft:
        app.draft_section.grid()
        app.reader_frame.rowconfigure(4,weight=2)
    else:
        app.draft_section.grid_remove()
        app.reader_frame.rowconfigure(4,weight=0)
    app.continue_btn.configure(state='disabled' if entry.get('status')=='awaiting_nomi_confirmation' else app.retry_btn['state'])
    visible=[]
    for button in app.recovery_buttons:
        button.grid_remove()
        if str(button['state'])!='disabled':
            visible.append(button)
    if visible:
        app.recovery_frame.grid()
        for index,button in enumerate(visible):
            button.grid(row=1+index//2,column=index%2,sticky='ew',padx=8,pady=(0,7))
        for col in (0,1):
            app.recovery_frame.columnconfigure(col,weight=1)
    else:
        app.recovery_frame.grid_remove()


def delivery_details(app):
    if not app.service or not app.current:
        return
    entry=app.service.entries().get(app.current,{})
    dialog=tk.Toplevel(app)
    dialog.title('Delivery details — '+app.service.name)
    dialog.geometry('680x460')
    dialog.minsize(450,300)
    dialog.configure(bg=NIGHT)
    dialog.transient(app)
    label(dialog,f'{app.service.name} / @{app.service.handle}',serif=True,size=16,color=GOLD).pack(anchor='w',padx=20,pady=15)
    text=paper_text(dialog)
    text.pack(fill='both',expand=True,padx=20)
    details=f"State: {entry.get('status','unknown')}\nParts confirmed: {entry.get('nomi_parts_completed',0)}/{entry.get('nomi_parts',0)}\n\n{entry.get('error','')}"
    if entry.get('receipt'):
        details+='\n\nReceipt:\n'+json.dumps(entry['receipt'],ensure_ascii=False,indent=2)
    text.insert('1.0',details)
    text.configure(state='disabled')
    tk.Button(dialog,text='Close',command=dialog.destroy,bg=PANEL,fg=TEXT,padx=18,pady=8).pack(anchor='e',padx=20,pady=12)


def build_live(window, service):
    window.geometry('1040x820')
    window.minsize(760,650)
    window.configure(bg=NIGHT)
    identity=tk.Frame(window,bg=SIDEBAR)
    identity.pack(fill='x')
    label(identity,f'LIVE CONVERSATIONS   ·   {service.name} / @{service.handle}',size=11,color=GOLD,bg=SIDEBAR).pack(side='left',padx=22,pady=12)
    window.live_badge=tk.StringVar(value='Paused · nothing is being sent')
    label(identity,textvariable=window.live_badge,size=10,color=GREEN,bg=SIDEBAR).pack(side='right',padx=20)
    window.live_banner=LamplitBanner(window,'A quiet window onto the town.',height=76)
    window.live_banner.pack(fill='x')
    header=tk.Frame(window,bg=NIGHT)
    header.pack(fill='x',padx=22,pady=(14,12))
    label(header,'Town conversations',serif=True,size=23).pack(anchor='w')
    label(header,'Live follows this resident’s actual location. Every public reply needs your approval.',color=MUTED,size=9).pack(anchor='w',pady=(4,0))
    toolbar=tk.Frame(window,bg=NIGHT)
    toolbar.pack(fill='x',padx=22,pady=(0,12))
    window.mode_picker=ttk.Combobox(toolbar,textvariable=window.mode,values=['Map hangout','Event'],state='readonly',width=14)
    window.mode_picker.pack(side='left',padx=(0,8))
    window.refresh_btn=window._button(toolbar,'Refresh events',window.refresh_calendar,PANEL)
    window.refresh_btn.pack(side='left',padx=(0,8))
    # Existing controller owns browser navigation and all live actions.
    import webbrowser
    window._button(toolbar,'Open the World ↗',lambda:webbrowser.open('https://postmark.town/world'),PANEL).pack(side='left')
    window.toggle_btn=window._button(toolbar,'Start listening',window.toggle,GOLD)
    window.toggle_btn.pack(side='right')
    window.status=tk.StringVar(value='Off. Nothing is being listened to or posted.')
    footer=tk.Frame(window,bg=SIDEBAR)
    footer.pack(side='bottom',fill='x')
    status=label(footer,textvariable=window.status,color=MUTED,bg=SIDEBAR,size=9,wraplength=960,justify='left')
    status.pack(fill='x',padx=20,pady=11)
    footer.bind('<Configure>',lambda e:status.configure(wraplength=max(300,e.width-40)))
    main=tk.Frame(window,bg=NIGHT)
    main.pack(fill='both',expand=True,padx=22,pady=(0,16))
    left=tk.Frame(main,bg=PANEL,width=212)
    left.pack(side='left',fill='y',padx=(0,18))
    left.pack_propagate(False)
    label(left,'TOWN CALENDAR',color=GOLD,bg=PANEL,size=9).pack(anchor='w',padx=13,pady=(14,8))
    window.event_list=tk.Listbox(left,bg=PANEL,fg=TEXT,selectbackground=SOFT,selectforeground=GOLD,
        font=(SANS,10),relief='flat',borderwidth=0,highlightthickness=0,exportselection=False,activestyle='none')
    window.event_list.pack(fill='both',expand=True,padx=10,pady=5)
    window.event_list.bind('<<ListboxSelect>>',window.show_event)
    window.event_detail=tk.StringVar(value='Loading the town calendar…')
    label(left,textvariable=window.event_detail,wraplength=182,justify='left',size=9,color=MUTED,bg=PANEL).pack(anchor='w',padx=13,pady=13)
    right=tk.Frame(main,bg=NIGHT)
    right.pack(side='left',fill='both',expand=True)
    label(right,'NEARBY CONVERSATION',color=GOLD,size=9).pack(anchor='w')
    transcript_frame=tk.Frame(right,bg=PANEL)
    transcript_frame.pack(fill='both',expand=True,pady=(7,10))
    window.transcript=tk.Text(transcript_frame,bg=PANEL,fg=TEXT,relief='flat',padx=18,pady=15,
        wrap='word',font=(SERIF,12),state='disabled',height=1,spacing3=8)
    window.transcript.pack(side='left',fill='both',expand=True)
    scroll_text(transcript_frame,window.transcript).pack(side='right',fill='y')
    window.ask_btn=window._button(right,'Ask Nomi to say something…',window.ask_to_speak,GOLD)
    window.ask_btn.pack(anchor='w',pady=(0,8))
    window.ask_btn.configure(state='disabled')
    window.live_recovery_frame=tk.Frame(right,bg=PANEL)
    window.live_recovery_frame.pack(fill='x',pady=(0,8))
    label(window.live_recovery_frame,'A QUIET PLACE TO PAUSE',size=9,color=GOLD,bg=PANEL).pack(anchor='w',padx=12,pady=(9,6))
    window.keep_private_btn=window._button(window.live_recovery_frame,'Keep private & start fresh…',window.keep_private_and_reset,GOLD)
    window.keep_private_btn.pack(anchor='w',padx=10,pady=(0,6))
    window.paste_live_btn=window._button(window.live_recovery_frame,'Paste Nomi response…',window.paste_live_reply,PANEL)
    window.paste_live_btn.pack(anchor='w',padx=10,pady=(0,9))
    label(right,f'Nomi input: {service.live_input_limit} characters  ·  Public speech: 500 max\nNo travel, RSVP, or spending.',color=MUTED,size=9,justify='left').pack(anchor='w')
    window.status.trace_add('write',lambda *_:sync_live(window))


def sync_live(window):
    if not hasattr(window,'live_badge'):
        return
    state=window.service.live_state()
    request=state.get('nomi') or {}
    locked=(state.get('speech') or {}).get('status')=='unknown'
    unfinished=bool(request.get('packets')) or request.get('status') in {'awaiting_reply','response_unknown','reply_ready','request_rejected'}
    if window.running:
        window.live_badge.set('Reviewing recovery' if window.recovering_live else 'Listening nearby')
    elif locked:
        window.live_badge.set('Needs attention · check public delivery')
    elif unfinished:
        window.live_badge.set('Paused · reply needs attention')
    else:
        window.live_badge.set('Paused · nothing is being sent')
    # Recovery is contextual, but the start-fresh action stays discoverable when paused.
    if not window.running and (unfinished or locked):
        if not window.live_recovery_frame.winfo_manager():
            window.live_recovery_frame.pack(fill='x',pady=(0,8),before=window.live_recovery_frame.master.winfo_children()[-1])
    else:
        window.live_recovery_frame.pack_forget()
