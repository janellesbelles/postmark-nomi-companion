# Maintainer handoff

## Integrated live-recovery contribution

Contribution base: a4ae6d7312df8364f46599f2a9a081287ff032ab. This ports live recovery behavior from a separately installed local build. It preserves the GitHub mail workflow, app-data root, resident migration/defaults, registration, branding and launchers. The integration also teaches the shared mail pipeline to recognize typed Nomi uncertainty. The contributor's separate REST mail adapter and household-ownership verification are not introduced.

ResidentService.record_private_live_reply records exact unmarked replies before consuming their packet. keep_live_private_and_reset atomically snapshots the full original Nomi request and speech proposal in abandoned_live_history, optionally preserves an overlength pasted reply, abandons active private work and marks an unsubmitted proposal private. Unknown public speech blocks this at the service layer. It makes no network call. A separate Start listening confirmation uses the existing baseline. The archived request retains its original uncertain status, and packets are not rewritten or retried. No restore/resend UI for abandoned packets is supplied.

RejectedNomiRequest is raised only for allowlisted documented 4xx rejection types. NoReply, 5xx, unreadable errors and network failures remain UncertainNomiResult. Raw server error details are never displayed. A rejected live request retains its packet; explicit retry requires preview/confirmation. A retry that times out becomes uncertain again. Old generic HTTP 400 checkpoints cannot be retroactively reclassified. An overlength explicit SAY remains held unless the human chooses private abandonment. Oversized replies are not automatically truncated or posted.

Integration was verified on Windows/Python 3.13 with the existing suite, contributed regression coverage, and a real Tk private-dialog interaction. Additional checks cover malformed successful Nomi responses, typed uncertainty in mail, and failed reconnects preserving the explicit rejected-request retry gate. The stronger upstream SAY instruction and example are retained. The full suite emits Tk after-callback shutdown notices; this is not a warning-free run. No credentials, real resident records, correspondence or app-data directories are included. Tests use synthetic identities. No live messages or public writes were performed.

This repository contains the generic companion source, shared delivery code, Windows launcher, documentation, and regression tests. It includes no resident profiles, conversation history, API keys, or prior private Git history.

## Components

| File | Responsibility |
| --- | --- |
| `Companion App.pyw` | Tkinter interface, resident selection, DPAPI key handling, mail approval and recovery, live relay sessions |
| `profile_store.py` | Profile registry, separate resident directories, legacy migration, default resident, archive/remove |
| `mail_pipeline.py` | Inbox discovery, local baseline, bounded numbered chunks, confirmed checkpoints, held drafts |
| `nomi_client.py` | Nomi chat API requests |
| `github_mailer.py` | Human-approved outgoing letters submitted through GitHub pull requests |
| `event_bridge.py` | Calendar, nearby World listening/speech, bounded Nomi packets, explicit SAY/PASS parsing |

## Delivery rules to preserve

- `archived_at` is a visibility flag, not a delivery status. Archive/restore preserve the original status, full letter, draft, confirmed checkpoints and `seen` entry. Filter archives from both the active mailbox and automatic confirmation queue. Require restoration before send/recovery/edit/submission; do not hide active requests or uncertain public submissions. The archive checkbox participates in the busy-control lock.

- Each resident owns a distinct settings file, mail state, and live state under the app-data directory documented in the README. Credentials use Windows DPAPI.
- Settings updates merge existing resident options; blank or omitted encrypted secrets preserve the existing ciphertext. Update app files without overwriting the app-data directory. Live input defaults to 800 independently of existing mail chunk checkpoints.
- Incoming letters require approval before going to Nomi. A seen baseline is not evidence that a letter was answered; use Postmark's correspondence state to identify older unanswered threads.
- Confirm each Nomi part only after receiving its response. Continue from the next unconfirmed part, with explicit checked-chat reconciliation when necessary.
- A timeout may mean the request arrived. Treat ambiguous Nomi delivery as unknown and allow a manually pasted reply. Never blindly resend.
- Outgoing GitHub uncertainty remains locked as `needs_send_check`. Inspect the existing branch/PR before enabling a new submission.
- Public speech requires an explicit SAY response and human approval of the exact text under the selected resident's identity. PASS remains private.
- Save a speech nonce and unknown state before a public request; save confirmed state after a receipt. Unknown speech is never automatically retried.
- Map hangout and event sessions use actual map earshot. Event selection supplies context and does not travel, RSVP, or grant remote hearing.
- Mail and live sessions must not run concurrently for the same resident. Pause and close wait for live work to stop.

## Verification and limitations

Live session starts baseline the current room instead of relaying its initial history snapshot. Timestamped message identities and a monotonic cursor persist per resident; overlapping messages at or before the cursor are ignored. Conversations that happened while listening was off are intentionally skipped. Saved unfinished packets remain subject to explicit Nomi recovery/resume.

The October 14, 2026 live-recovery update passed local tests with Python 3.13 on Windows, including a rendered Tk window smoke test, resident isolation, mail checkpoint recovery, speech approval/rejection, uncertain speech delivery, and Nomi timeout/paste recovery across restart. Tests simulate Nomi and speech writes. Production speaking rules were checked through a read-only Postmark call; no public message was posted as part of verification.

An end-to-end live session with the recipient's own credentials still needs human validation. External API contracts may change. Input limits are characters (400 or 800), not words; public speech currently caps at 500 characters. Live prompts are compact. Oversized individual chatter is split losslessly into numbered parts; intermediate parts cannot trigger a public proposal before FINAL. Mail-letter prompts are unchanged.

Speech state retains the most recent proposal/receipt. The live Nomi queue saves its current packet and unsent remainder before requesting a response; an interrupted or timed-out request must be reconciled through Paste Nomi response. A recovered response is reviewed without requesting it again, and remaining queued packets wait for explicit resume. A crash between fetching new World chatter and saving its packet queue can still lose that just-fetched batch. A process killed during a mail request can leave `contacting_nomi`; reconcile its saved active-part checkpoint against normal Nomi chat before any resend.

Profile-index updates use atomic file replacement, but not a cross-process merge protocol. Keep one app instance open. Existing installations with orphan/duplicate resident directories need careful reconciliation against their latest drafts and PR records; do not blindly replace a resident state file with an older copy.

## Ownership and contribution

The repository is offered for review by Postmark maintainers and community builders. Repository ownership is separate from Postmark service ownership. No collaborator invitations or ownership transfers are performed by this handoff. A project license has not yet been selected; agree on licensing with the repository owner before redistribution under a new license.
# Desk theme presentation update

`lamplit_ui.py` contains the native Tk builders; `banner_art.py` draws eight
distinct responsive header scenes. `desk_themes.py` contains eight semantic
palettes, app-wide preference persistence, and in-place recoloring. Ship all
three beside `Companion App.pyw`. No runtime assets
or third-party dependencies are needed. The externally contributed patch was
adapted, not blindly applied: its different mail adapter, branding, icon bundle,
machine-specific setup notes, and unrelated code are not included.

Store only `ui_theme` in the existing profile index. Do not replace the index or
resident settings to install themes. An unknown preference falls back safely.
Mail and live workers, durable packet queues, encrypted keys, outgoing
submission safeguards, and approval callbacks are unchanged. The archive filter
still calls the existing autosave-aware handler. The Treeview adapter preserves
the Listbox subset used by the current controller and blocks keyboard/mouse
selection while disabled. Resident identity is explicit in the sidebar/footer.

108 tests pass on Windows/Python 3.13, including eight-theme persistence and
contrast, unsaved draft/selection retention, 900x650 mail and 760x650 live
geometry, private/public uncertainty locks, archive autosave, and save-failure
rollback, distinct header geometry, repeated redraw cleanup, round double moons
at mail/live sizes, and light-theme text-selection contrast. Synthetic native
Mail and eight-theme gallery windows were visually inspected; no real resident
data or external messages were used. Reference screenshots were not distributed;
the header art contains no copied resident names, labels, avatars, or map data.
Known Tk shutdown callback
notices remain; the successful suite is not a claim of warning-free shutdown.
