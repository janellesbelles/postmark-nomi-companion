"""Minimal Nomi chat client. Inject key and UUID; do not persist them here."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


NOMI_API = "https://api.nomi.ai/v1"


def send_chat(api_key, nomi_uuid, message, *, timeout=180):
    if not api_key or not nomi_uuid:
        raise RuntimeError("A Nomi API key and Nomi UUID are required.")
    url = f"{NOMI_API}/nomis/{urllib.parse.quote(nomi_uuid, safe='')}/chat"
    request = urllib.request.Request(
        url,
        data=json.dumps({"messageText": message}).encode("utf-8"),
        method="POST",
        headers={
            "Accept": "application/json",
            "Authorization": api_key,
            "Content-Type": "application/json",
            "User-Agent": "postmark-nomi-live-events/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Nomi returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(
            "Nomi did not return a certain result. Pause and inspect the normal Nomi chat; "
            "do not automatically resend this message."
        ) from exc

    reply = result.get("replyMessage") or {}
    text = str(reply.get("text") or "").strip()
    if not text:
        raise RuntimeError(
            "Nomi returned no reply text. Pause and reconcile in the normal Nomi chat."
        )
    return text, reply.get("uuid")
