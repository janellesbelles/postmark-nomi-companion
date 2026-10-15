"""Minimal Nomi chat client. Inject key and UUID; do not persist them here."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


NOMI_API = "https://api.nomi.ai/v1"

class UncertainNomiResult(RuntimeError):
    pass


class RejectedNomiRequest(RuntimeError):
    """A documented rejection: this request did not initiate a Nomi reply."""


# Never display arbitrary server text: it may contain submitted text or secrets.
REJECTION_REASONS = {
    'NomiStillResponding': 'This Nomi may still be answering another message. Wait for the normal chat to finish before retrying.',
    'MessageLengthLimitExceeded': 'The message exceeds your account input limit. Check the separate Live Nomi input limit.',
    'NomiNotFound': 'Check that the Nomi UUID belongs to the account for this API key.',
    'InvalidRouteParams': 'Check the Nomi UUID format.',
    'InvalidContentType': 'The request format was rejected.',
    'InvalidBody': 'The message request format was rejected.',
    'NomiNotReady': 'This Nomi is not ready yet. Wait before retrying.',
    'OngoingVoiceCallDetected': 'End the Nomi voice call before retrying.',
    'LimitExceeded': 'The daily message quota has been reached.',
    'TooManyRequests': 'The API rate limit was reached. Wait before retrying.',
    'Unauthorized': 'Check the saved API key locally in connection settings.',
}


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
        reason = None
        try:
            body = json.loads(exc.read(8192))
            reason = body.get('error', {}).get('type')
        except Exception:
            pass
        finally:
            exc.close()
        # NoReply means a message may have arrived even though no reply came
        # back. Unknown/malformed errors also remain locked, regardless of 4xx.
        if 400 <= exc.code < 500 and isinstance(reason, str) and reason in REJECTION_REASONS:
            raise RejectedNomiRequest(
                f'Nomi rejected the request: {reason} (HTTP {exc.code}). '
                f'{REJECTION_REASONS[reason]} No automatic retry.'
            ) from None
        detail = ' (NoReply)' if reason == 'NoReply' else ''
        raise UncertainNomiResult(
            f'Nomi returned HTTP {exc.code}{detail}; message delivery is uncertain. '
            'Check normal Nomi chat before recovery. No automatic resend.'
        ) from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise UncertainNomiResult(
            "Nomi did not return a certain result. Pause and inspect the normal Nomi chat; "
            "do not automatically resend this message."
        ) from None
    except Exception:
        raise UncertainNomiResult('Nomi returned an unreadable response. Check the normal chat before resending.') from None

    if not isinstance(result, dict) or not isinstance(result.get("replyMessage"), dict):
        raise UncertainNomiResult('Nomi returned an unreadable response. Check the normal chat before resending.')
    reply = result["replyMessage"]
    if not isinstance(reply.get("text"), str):
        raise UncertainNomiResult('Nomi returned no usable reply text. Check the normal chat before resending.')
    text = reply["text"].strip()
    if not text:
        raise UncertainNomiResult(
            "Nomi returned no reply text. Pause and reconcile in the normal Nomi chat."
        )
    return text, reply.get("uuid")
