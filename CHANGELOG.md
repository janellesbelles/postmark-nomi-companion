# Multi-resident update

- Mail input now defaults to 800; changing a resident's limit repacks unsent letters and pins existing delivery checkpoints to their original chunk size.

- Household keys persist across profile edits: blank or omitted secret fields preserve encrypted credentials, and the UI explicitly labels saved household keys. Settings patches preserve other resident options.

- Live sessions now default to the full 800-character Nomi input allowance, independently of existing mail checkpoints; full 500-character map speeches fit without splitting. New resident mail settings also default to 800.

- Shortened live chatter and opening prompts. Long individual voices now split into numbered parts without clipping; intermediate fragments cannot produce public replies before the final part.

- Fixed live-history replay on restart: baseline each new session without forwarding the initial snapshot, persist timestamped voice IDs per resident, and prevent listening cursors from moving backward.

- Added multiline Paste Nomi response to Live Map & Events. Nomi timeouts retain the current packet and remaining queue per resident across restarts, and block blind resends.
- Recovered SAY responses require separate public approval; PASS stays private. Listening resumes only on explicit request, and uncertain Postmark speech remains locked.

- Added explicit Map hangout and Event modes under Live Map & Events, including a Nomi-authored opening-line button for quiet rooms.
- Public speech now requires a resident-labelled approval preview; proposals, nonces, and unknown/confirmed delivery are saved per resident.
- Pausing/closing waits for the active relay to finish, and mailbox work cannot overlap a live session.

- Added a prominent active-resident selector and identity banner.
- Added Add, Edit, Remove, and Set as Default controls.
- Replaced baked-in resident identity with shared parameterized mail/event pipelines.
- Added random per-resident state namespaces under the app-data root.
- Added independent Nomi and Postmark credentials/options per resident using Windows DPAPI.
- Added automatic preservation/migration of the prior installed resident.
- Preserved multipart continuation, ambiguous-timeout recovery, Paste Reply, explicit resend, and outgoing `needs_send_check` locks.
- Bound every background result to the resident profile that started it.
- Prevented resident switching during active mail work or an open Live Events session.
- Added archive-or-delete behavior for resident removal.
- Kept-unsent letters now leave the active mailbox while remaining durably recorded for duplicate protection.
- Submitted letters can now be cleared from the active mailbox without deleting their submission history or duplicate protection.
- Added a hard per-letter confirmation gate before new or continued mail is sent to Nomi; declining leaves the letter queued locally.
- Added a first-run mailbox choice per resident: baseline existing mail or safely import it into the confirmation queue.
- Hardened multipart recovery: retries now show and repair missing checkpoints explicitly instead of silently restarting a whole letter, and checkpoints can never move backward.
- Replaced the one-line Paste Reply prompt with a multiline recovery window that handles long replies and makes the no-send boundary explicit.
- Enabled Paste Reply for every safe pre-submission inbound state, including letters still waiting at the Nomi confirmation gate.
- Clarified uncertain-part recovery so a checked part can either be resent when missing or skipped when visibly present, without asking the user to make a contradictory confirmation.
