import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import event_bridge as bridge


class BridgeSafetyTests(unittest.TestCase):
    def test_calendar_keeps_current_and_upcoming_only(self):
        original = bridge.request_json
        bridge.request_json = lambda *args, **kwargs: {
            "now": [{"title": "Tea", "phase": "underway"}],
            "coming": [
                {"title": "Dance", "phase": "announced"},
                {"title": "Cancelled", "cancelled": True},
            ],
            "ended": [{"title": "Yesterday"}],
        }
        try:
            _raw, events = bridge.current_calendar()
        finally:
            bridge.request_json = original
        self.assertEqual([event["title"] for event in events], ["Tea", "Dance"])

    def test_own_voice_is_filtered(self):
        result = {"voices": [
            {"handle": "resident-a", "said": "hello"},
            {"handle": "resident-b", "said": "my own words"},
        ]}
        self.assertEqual(
            bridge.other_voices(result, "resident-b"),
            [{"handle": "resident-a", "said": "hello", "ago": ""}],
        )

    def test_packets_fit_both_supported_limits(self):
        voices = [
            {"handle": f"friend-{index}", "said": "a small public sentence " * 4}
            for index in range(12)
        ]
        for limit in (400, 800):
            packets = bridge.chatter_packets(voices, limit)
            self.assertTrue(packets)
            self.assertTrue(all(len(packet) <= limit for packet in packets))

    def test_oversized_voice_is_disclosed_as_clipped(self):
        packets = bridge.chatter_packets(
            [{"handle": "speaker", "said": "x" * 1000}], 400
        )
        self.assertEqual(len(packets), 1)
        self.assertLessEqual(len(packets[0]), 400)
        self.assertIn("[…]", packets[0])

    def test_only_explicit_say_is_public(self):
        self.assertIsNone(bridge.speech_from_nomi("PASS"))
        self.assertEqual(bridge.speech_from_nomi("SAY: Hello there."), "Hello there.")
        with self.assertRaises(ValueError):
            bridge.speech_from_nomi("I might say hello.")
        with self.assertRaises(ValueError):
            bridge.speech_from_nomi("SAY: " + "x" * 501)

    def test_speak_requires_confirmation_and_preserves_nonce(self):
        original = bridge.request_json
        captured = {}

        def fake_request(*args, **kwargs):
            captured.update(kwargs["payload"])
            return {"spoke": True, "latest": 101, "voices": []}

        bridge.request_json = fake_request
        try:
            bridge.speak("key", "resident", "hello", since=100, nonce="retry-key")
        finally:
            bridge.request_json = original
        self.assertEqual(captured["nonce"], "retry-key")
        self.assertEqual(captured["since"], 100)


if __name__ == "__main__":
    unittest.main()
