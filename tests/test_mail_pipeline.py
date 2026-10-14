import pathlib
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import github_mailer
import mail_pipeline


def letter(body="Hello from the road."):
    return (
        "---\n"
        "id: sender-2026-01-01-note\n"
        "from: sender-resident\n"
        "to: recipient-resident\n"
        "title: A note\n"
        "---\n\n"
        + body
    )


class MailPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT)
        self.state_path = pathlib.Path(self.temporary.name) / "state.json"

    def tearDown(self):
        self.temporary.cleanup()

    def pipeline(self, *, discover=None, fetch=None, send=None, limit=400):
        return mail_pipeline.MailPipeline(
            resident_handle="recipient-resident",
            state_path=self.state_path,
            nomi_send=send or (lambda message: ("draft reply", "reply-id")),
            message_limit=limit,
            part_pause=0,
            discover=discover or (lambda: []),
            fetch=fetch or (lambda source: mail_pipeline.parse_letter_text(letter(), "recipient-resident")),
        )

    def test_initialization_baselines_without_contacting_nomi(self):
        calls = []
        pipeline = self.pipeline(
            discover=lambda: [("old-one", "url-one"), ("old-two", "url-two")],
            send=lambda message: calls.append(message),
        )
        waiting = pipeline.initialize()
        state = pipeline.state.read()
        self.assertEqual(state["seen"], ["old-one", "old-two"])
        self.assertEqual(calls, [])
        self.assertEqual(waiting, [])

    def test_initialization_can_import_existing_without_contacting_nomi(self):
        calls = []
        pipeline = self.pipeline(
            discover=lambda: [("old-one", "url-one"), ("old-two", "url-two")],
            send=lambda message: calls.append(message),
        )
        waiting = pipeline.initialize(import_existing=True)
        state = pipeline.state.read()
        self.assertEqual(waiting, ["old-one", "old-two"])
        self.assertEqual(state["seen"], ["old-one", "old-two"])
        self.assertEqual(
            {entry["status"] for entry in state["letters"].values()},
            {"awaiting_nomi_confirmation"},
        )
        self.assertEqual(calls, [])

    def test_short_and_multipart_wrappers_are_bounded(self):
        fields, short_body = mail_pipeline.parse_letter_text(letter(), "recipient-resident")
        one = mail_pipeline.format_nomi_messages(fields, short_body, 400)
        self.assertEqual(len(one), 1)
        self.assertNotIn("[1/", one[0])
        long = mail_pipeline.format_nomi_messages(fields, "word " * 500, 400)
        self.assertGreater(len(long), 2)
        self.assertTrue(all(len(message) <= 400 for message in long))
        self.assertIn("Wait for FINAL", long[0])
        self.assertIn("FINAL:", long[-1])

    def test_poll_stages_without_contacting_nomi_then_confirmation_sends(self):
        calls = []
        pipeline = self.pipeline(send=lambda message: (calls.append(message) or "draft reply", "reply-id"))
        pipeline.initialize()
        pipeline.discover = lambda: [("new-letter", "source")]
        waiting = pipeline.poll()
        entry = pipeline.entries()["new-letter"]
        self.assertEqual(waiting, ["new-letter"])
        self.assertEqual(entry["status"], "awaiting_nomi_confirmation")
        self.assertEqual(calls, [])
        pipeline.confirm_delivery("new-letter")
        entry = pipeline.entries()["new-letter"]
        self.assertEqual(entry["status"], "pending")
        self.assertEqual(entry["draft"], "draft reply")
        self.assertEqual(entry["nomi_parts_completed"], 1)
        self.assertEqual(len(calls), 1)

    def test_definite_failure_continues_only_after_confirmed_parts(self):
        fields, body = mail_pipeline.parse_letter_text(letter("word " * 500), "recipient-resident")
        expected = mail_pipeline.format_nomi_messages(fields, body, 400)
        sent_first = []

        def fail_on_third(message):
            sent_first.append(message)
            if len(sent_first) == 3:
                raise RuntimeError("connection refused")
            return ("intermediate", f"reply-{len(sent_first)}")

        pipeline = self.pipeline(
            fetch=lambda source: (fields, body),
            send=fail_on_third,
        )
        pipeline.initialize()
        pipeline.discover = lambda: [("long-letter", "source")]
        pipeline.poll()
        with self.assertRaises(RuntimeError):
            pipeline.confirm_delivery("long-letter")
        failed = pipeline.entries()["long-letter"]
        self.assertEqual(failed["status"], "needs_attention")
        self.assertEqual(failed["nomi_parts_completed"], 2)

        resumed = []
        pipeline.nomi_send = lambda message: (resumed.append(message) or "final draft", "final-id")
        pipeline.continue_after_definite_failure("long-letter")
        self.assertEqual(resumed, expected[2:])
        self.assertEqual(pipeline.entries()["long-letter"]["status"], "pending")

    def test_missing_checkpoint_can_be_repaired_only_after_human_check(self):
        fields, body = mail_pipeline.parse_letter_text(letter("word " * 500), "recipient-resident")
        expected = mail_pipeline.format_nomi_messages(fields, body, 400)
        pipeline = self.pipeline(fetch=lambda source: (fields, body))
        pipeline.initialize()
        state = pipeline.state.read()
        state["seen"].append("legacy-error")
        state["letters"]["legacy-error"] = {
            "status": "needs_attention", "source": "source",
            "fields": fields, "body": body, "nomi_parts": len(expected),
            "error": "connection refused",
        }
        pipeline.state.write(state)
        with self.assertRaises(ValueError):
            pipeline.continue_after_definite_failure("legacy-error", next_part=3)

        resumed = []
        pipeline.nomi_send = lambda message: (resumed.append(message) or "draft", "reply")
        pipeline.continue_after_definite_failure(
            "legacy-error", next_part=3, human_checked_chat=True
        )
        repaired = pipeline.entries()["legacy-error"]
        self.assertEqual(resumed, expected[2:])
        self.assertEqual(repaired["human_confirmed_through"], 2)
        self.assertEqual(repaired["manual_checkpoint_history"][-1]["previous_completed"], 0)

    def test_resume_checkpoint_never_moves_backward(self):
        pipeline = self.pipeline()
        pipeline.initialize()
        state = pipeline.state.read()
        state["seen"].append("checkpointed")
        state["letters"]["checkpointed"] = {
            "status": "needs_attention", "source": "source",
            "fields": {"from": "sender-resident", "to": "recipient-resident", "title": "A note", "id": "id"},
            "body": "word " * 500, "nomi_parts": 7, "nomi_parts_completed": 2,
        }
        pipeline.state.write(state)
        with self.assertRaises(ValueError):
            pipeline.continue_after_definite_failure(
                "checkpointed", next_part=2, human_checked_chat=True
            )

    def test_timeout_is_uncertain_and_manual_paste_does_not_resend(self):
        calls = []

        def timeout(message):
            calls.append(message)
            raise TimeoutError("read timed out")

        pipeline = self.pipeline(send=timeout)
        pipeline.initialize()
        pipeline.discover = lambda: [("timed-letter", "source")]
        pipeline.poll()
        with self.assertRaises(TimeoutError):
            pipeline.confirm_delivery("timed-letter")
        uncertain = pipeline.entries()["timed-letter"]
        self.assertEqual(uncertain["status"], "response_unknown")
        self.assertEqual(uncertain["ambiguous_part"], 1)

        recovered = pipeline.recover_reply("timed-letter", "reply copied from normal chat")
        self.assertEqual(recovered["status"], "pending")
        self.assertTrue(recovered["recovered_manually"])
        self.assertEqual(len(calls), 1)

    def test_manual_paste_preserves_long_multiline_reply(self):
        pipeline = self.pipeline()
        pipeline.initialize()
        state = pipeline.state.read()
        state["seen"].append("multiline")
        state["letters"]["multiline"] = {
            "status": "response_unknown", "source": "source",
            "fields": {"from": "sender-resident", "to": "recipient-resident"},
            "body": "incoming", "nomi_parts": 3,
        }
        pipeline.state.write(state)
        reply = "First paragraph.\n\n" + ("A long second paragraph. " * 300) + "\n\nFinal line."
        recovered = pipeline.recover_reply("multiline", reply)
        self.assertEqual(recovered["draft"], reply)
        self.assertEqual(recovered["status"], "pending")
        self.assertTrue(recovered["recovered_manually"])

    def test_uncertain_resend_requires_human_check(self):
        def timeout(_message):
            raise RuntimeError("HTTP 504")

        pipeline = self.pipeline(send=timeout)
        pipeline.initialize()
        pipeline.discover = lambda: [("uncertain", "source")]
        pipeline.poll()
        with self.assertRaises(RuntimeError):
            pipeline.confirm_delivery("uncertain")
        pipeline.nomi_send = lambda message: ("recovered draft", "reply-id")
        with self.assertRaises(ValueError):
            pipeline.resend_after_human_check("uncertain")
        result = pipeline.resend_after_human_check("uncertain", human_checked_chat=True)
        self.assertEqual(result["status"], "pending")

    def test_uncertain_resend_can_advance_after_checked_chat(self):
        fields, body = mail_pipeline.parse_letter_text(letter("word " * 500), "recipient-resident")
        expected = mail_pipeline.format_nomi_messages(fields, body, 400)
        pipeline = self.pipeline(
            fetch=lambda source: (fields, body),
            send=lambda message: (_ for _ in ()).throw(TimeoutError("timed out")),
        )
        pipeline.initialize()
        pipeline.discover = lambda: [("uncertain-advance", "source")]
        pipeline.poll()
        with self.assertRaises(TimeoutError):
            pipeline.confirm_delivery("uncertain-advance")
        resumed = []
        pipeline.nomi_send = lambda message: (resumed.append(message) or "draft", "reply")
        pipeline.resend_after_human_check(
            "uncertain-advance", human_checked_chat=True, next_part=2
        )
        self.assertEqual(resumed, expected[1:])

    def test_interrupted_github_submission_locks_for_inspection(self):
        pipeline = self.pipeline()
        pipeline.initialize()
        state = pipeline.state.read()
        fields, body = mail_pipeline.parse_letter_text(letter(), "recipient-resident")
        state["seen"].append("ready")
        state["letters"]["ready"] = {
            "status": "pending", "fields": fields, "body": body, "draft": "approved words"
        }
        pipeline.state.write(state)

        def uncertain_submission(*args, **kwargs):
            raise RuntimeError("network disappeared")

        with self.assertRaises(RuntimeError):
            github_mailer.submit_approved(
                pipeline, "ready", "approved words", create_pr=uncertain_submission
            )
        locked = pipeline.entries()["ready"]
        self.assertEqual(locked["status"], "needs_send_check")
        self.assertIn("Inspect GitHub", locked["error"])

    def test_clear_submitted_preserves_history_and_duplicate_protection(self):
        pipeline = self.pipeline()
        pipeline.initialize()
        state = pipeline.state.read()
        state["seen"].append("completed")
        state["letters"]["completed"] = {
            "status": "pr_open",
            "pr": "https://example.invalid/pull/1",
            "draft": "already submitted",
        }
        pipeline.state.write(state)

        cleared = pipeline.clear_submitted("completed")
        persisted = pipeline.entries()["completed"]
        self.assertIn("cleared_from_mailbox_at", cleared)
        self.assertEqual(persisted["status"], "pr_open")
        self.assertEqual(persisted["pr"], "https://example.invalid/pull/1")
        self.assertIn("completed", pipeline.state.read()["seen"])


if __name__ == "__main__":
    unittest.main()
