# Maintainer handoff

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

- Each resident owns a distinct settings file, mail state, and live state under the app-data directory documented in the README. Credentials use Windows DPAPI.
- Incoming letters require approval before going to Nomi. A seen baseline is not evidence that a letter was answered; use Postmark's correspondence state to identify older unanswered threads.
- Confirm each Nomi part only after receiving its response. Continue from the next unconfirmed part, with explicit checked-chat reconciliation when necessary.
- A timeout may mean the request arrived. Treat ambiguous Nomi delivery as unknown and allow a manually pasted reply. Never blindly resend.
- Outgoing GitHub uncertainty remains locked as `needs_send_check`. Inspect the existing branch/PR before enabling a new submission.
- Public speech requires an explicit SAY response and human approval of the exact text under the selected resident's identity. PASS remains private.
- Save a speech nonce and unknown state before a public request; save confirmed state after a receipt. Unknown speech is never automatically retried.
- Map hangout and event sessions use actual map earshot. Event selection supplies context and does not travel, RSVP, or grant remote hearing.
- Mail and live sessions must not run concurrently for the same resident. Pause and close wait for live work to stop.

## Verification and limitations

The October 14, 2026 live-recovery update passed local tests with Python 3.13 on Windows, including a rendered Tk window smoke test, resident isolation, mail checkpoint recovery, speech approval/rejection, uncertain speech delivery, and Nomi timeout/paste recovery across restart. Tests simulate Nomi and speech writes. Production speaking rules were checked through a read-only Postmark call; no public message was posted as part of verification.

An end-to-end live session with the recipient's own credentials still needs human validation. External API contracts may change. Input limits are characters (400 or 800), not words; public speech currently caps at 500 characters. Oversized individual chatter is clipped with a visible marker.

Speech state retains the most recent proposal/receipt. The live Nomi queue saves its current packet and unsent remainder before requesting a response; an interrupted or timed-out request must be reconciled through Paste Nomi response. A recovered response is reviewed without requesting it again, and remaining queued packets wait for explicit resume. A crash between fetching new World chatter and saving its packet queue can still lose that just-fetched batch. A process killed during a mail request can leave `contacting_nomi`; reconcile its saved active-part checkpoint against normal Nomi chat before any resend.

Profile-index updates use atomic file replacement, but not a cross-process merge protocol. Keep one app instance open. Existing installations with orphan/duplicate resident directories need careful reconciliation against their latest drafts and PR records; do not blindly replace a resident state file with an older copy.

## Ownership and contribution

The repository is offered for review by Postmark maintainers and community builders. Repository ownership is separate from Postmark service ownership. No collaborator invitations or ownership transfers are performed by this handoff. A project license has not yet been selected; agree on licensing with the repository owner before redistribution under a new license.
