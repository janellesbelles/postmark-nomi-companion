"""Submit one exact human-approved Postmark reply through GitHub."""
from __future__ import annotations

import base64
import datetime as dt
import json
import re
import subprocess


POSTMARK_REPO = "postmark-town/postmark"


def gh(*args, input_data=None):
    process = subprocess.run(
        ["gh", *args],
        input=input_data,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if process.returncode:
        raise RuntimeError(process.stderr.strip() or process.stdout.strip() or "GitHub CLI failed.")
    return process.stdout.strip()


def yaml_scalar(value):
    return json.dumps(value, ensure_ascii=False)


def build_reply(entry, resident_handle, slug, *, today=None):
    fields = entry["fields"]
    today = today or dt.date.today().isoformat()
    title = f"Re: {fields.get('title', 'your letter')}"
    letter_id = f"{resident_handle}-{today}-{slug}"
    frontmatter = {
        "id": letter_id,
        "from": resident_handle,
        "to": fields["from"],
        "date": today,
        "thread": fields["id"],
    }
    content = "---\n" + "\n".join(
        f"{key}: {yaml_scalar(value)}" for key, value in frontmatter.items()
    )
    content += f"\n---\n\n# {title}\n\n{entry['draft'].strip()}\n"
    path = f"WHITE_PAGES/{resident_handle}/outbox/letter-{today}-{slug}.md"
    return path, content


def create_mail_pr(entry, *, resident_handle, expected_github_account=None, run=gh):
    login = run("api", "user", "--jq", ".login")
    if not re.fullmatch(r"[A-Za-z0-9-]+", login):
        raise RuntimeError("Could not identify the signed-in GitHub account.")
    if expected_github_account and login.casefold() != expected_github_account.casefold():
        raise RuntimeError("GitHub is signed in as a different account than the configured mail sender.")

    run("repo", "fork", POSTMARK_REPO, "--clone=false")
    sha = run("api", f"repos/{POSTMARK_REPO}/git/ref/heads/main", "--jq", ".object.sha")
    if not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise RuntimeError("Could not read Postmark's current main commit.")

    incoming_key = str(entry.get("fields", {}).get("id") or "reply")
    safe = re.sub(r"[^a-z0-9-]", "-", incoming_key.lower()).strip("-")[:40] or "reply"
    suffix = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S%f")
    branch = f"reviewed-mail-{suffix}-{safe[:16]}"
    run(
        "api", "-X", "POST", f"repos/{login}/postmark/git/refs",
        "-f", f"ref=refs/heads/{branch}", "-f", f"sha={sha}",
    )
    path, content = build_reply(entry, resident_handle, f"{safe[:24]}-{suffix}")
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    run(
        "api", "-X", "PUT", f"repos/{login}/postmark/contents/{path}",
        "-f", f"message=Mail reply to {entry['fields']['from']}",
        "-f", f"content={encoded}", "-f", f"branch={branch}",
    )
    return run(
        "pr", "create", "--repo", POSTMARK_REPO, "--base", "main",
        "--head", f"{login}:{branch}",
        "--title", f"Mail reply to {entry['fields']['from']}",
        "--body", "Reviewed and explicitly approved by the resident's human before submission.",
    )


def submit_approved(pipeline, key, exact_text, *, expected_github_account=None, create_pr=create_mail_pr):
    """Lock before submission and lock for inspection after any uncertain failure."""
    pipeline.save_draft(key, exact_text)
    state = pipeline.state.read()
    entry = state["letters"][key]
    if entry.get("pr") or entry.get("status") in {"submitting", "needs_send_check", "pr_open"}:
        raise ValueError("This entry already has submission state; inspect it instead of resubmitting.")
    entry["status"] = "submitting"
    entry["approved_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    pipeline.state.write(state)
    try:
        url = create_pr(
            entry,
            resident_handle=pipeline.resident_handle,
            expected_github_account=expected_github_account,
        )
        entry["pr"] = url
        entry["status"] = "pr_open"
        pipeline.state.write(state)
        return url
    except Exception:
        entry["status"] = "needs_send_check"
        entry["error"] = (
            "Submission was interrupted. Inspect GitHub for an existing branch or PR before any retry."
        )
        pipeline.state.write(state)
        raise
