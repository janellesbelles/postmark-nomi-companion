# Companion maintenance workflow

Keep this repository current when updating the local Postmark Nomi companion. This is the canonical public source repository.

- Implement and test changes here, or reconcile changes from another working copy before publishing.
- Keep the installed application and the current distributable handoff consistent with the tested source when those artifacts are part of the task.
- Update README, changelog, and maintainer handoff when behavior or known limitations change.
- Check for credentials, resident identifiers, private correspondence, local state, backups, and machine paths before every publication. Never commit these.
- Run relevant tests, commit the intended changes, and push to this repository as part of an authorized companion update. The owner explicitly requested ongoing synchronization with local updates.
- Verify the published commit matches the tested local commit. Report any failed publication or remaining artifact drift plainly.
- Do not overwrite unrelated edits, include another project's files, or create public messages/letters as part of source synchronization.
