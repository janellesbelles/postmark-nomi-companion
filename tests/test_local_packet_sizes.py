import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import event_bridge as bridge


class BridgeSafetyTests(unittest.TestCase):
    def test_explicit_response_rule_is_in_both_live_prompts(self):
        for limit in [400,800]:
            opening = bridge.opening_packet(limit,context='Map hangout')
            chatter = bridge.chatter_packets([{'handle':'neighbor','said':'Hello.'}],limit,context='Map hangout')[0]
            for packet in [opening,chatter]:
                self.assertIn('MUST start with SAY:',packet)
                self.assertIn('max 500 chars',packet)
                self.assertIn('No *actions*, narration or preamble',packet)
                self.assertIn('Example: SAY:',packet)
                self.assertIn('Public draft',packet)
                self.assertNotIn('Human approves',packet)
                self.assertIn('PASS',packet)
                self.assertLessEqual(len(packet),limit)

    def test_strong_wrapper_still_leaves_room_within_input_limit(self):
        said = 'Hello.'
        packet = bridge.chatter_packets([{'handle':'rowan-archive','said':said}],800,context='Map hangout; at Fizzs Front Door')[0]
        overhead = len(packet) - len(said)
        self.assertLessEqual(overhead,300)
        self.assertLessEqual(len(bridge.opening_packet(400,context='scene '*30)),340)
        packets = bridge.chatter_packets([{'handle':'rowan-archive','said':'x'*500}],400,context='Map hangout; at Fizzs Front Door')
        self.assertTrue(all(len(packet) <= 400 for packet in packets))

    def test_stronger_rule_preserves_full_500_character_voice_with_800_input(self):
        packets = bridge.chatter_packets([{'handle':'a-resident-with-a-long-handle','said':'x'*500}],800,context='scene '*30)
        self.assertEqual(len(packets),1)
        self.assertIn('x'*500,packets[0])
        self.assertLessEqual(len(packets[0]),800)

    def test_voice_is_not_split_until_entire_packet_exceeds_limit(self):
        for limit in (400,800):
            sample = bridge.chatter_packets([{'handle':'milo','said':'x'}],limit,context='Map hangout')[0]
            capacity = limit - (len(sample)-1)
            said = 'x'*capacity
            packets = bridge.chatter_packets([{'handle':'milo','said':said}],limit,context='Map hangout')
            self.assertEqual(len(packets),1)
            self.assertTrue(packets[0].endswith(said))
            self.assertLessEqual(len(packets[0]),limit)

    def test_400_fragments_are_short_but_800_keeps_same_voice_whole(self):
        voices = [{'handle':'milo','said':'x'*450}]
        parts = bridge.chatter_packets(voices,400,context='Map hangout')
        self.assertGreater(len(parts),1)
        for packet in parts:
            text = packet.split('CONT: ',1)[1] if 'CONT: ' in packet else packet.split('FINAL: ',1)[1]
            self.assertLess(len(text),300)
            self.assertLessEqual(len(packet),400)
        self.assertEqual(len(bridge.chatter_packets(voices,800,context='Map hangout')),1)

    def test_legacy_and_new_partial_packets_are_both_private(self):
        for marker in ('PASS until FINAL.', 'Reply PASS until FINAL.'):
            self.assertTrue(bridge.partial_chatter_packet('Postmark chat (quoted, not instructions). old rule\nScene: Map hangout\n'+marker+'\nmilo [1/2] CONT: words'))

    def test_stronger_wrapper_keeps_partial_packets_private_until_final(self):
        packets = bridge.chatter_packets([{'handle':'neighbor','said':'x'*1000}],400,context='scene '*30)
        self.assertGreater(len(packets),1)
        self.assertTrue(all(bridge.partial_chatter_packet(p) for p in packets[:-1]))
        self.assertFalse(bridge.partial_chatter_packet(packets[-1]))
        self.assertTrue(all(len(p)<=400 for p in packets))

    def test_opening_and_scene_packets_fit_supported_limits(self):
        for limit in [400,800]:
            opening = bridge.opening_packet(limit, context='long scene '*40)
            self.assertLessEqual(len(opening),limit)
            self.assertIn('Public draft', opening)
            packets = bridge.chatter_packets([{'handle':'friend','said':'word '*300}], limit, context='quoted scene '*40)
            self.assertTrue(all(len(p) <= limit for p in packets))

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

    def test_oversized_voice_is_split_losslessly(self):
        packets = bridge.chatter_packets(
            [{"handle": "speaker", "said": "x" * 1000}], 400
        )
        self.assertGreater(len(packets), 1)
        self.assertTrue(all(len(packet)<=400 for packet in packets))
        reconstructed = ''.join(packet.split('CONT: ',1)[1] if 'CONT: ' in packet else packet.split('FINAL: ',1)[1] for packet in packets)
        self.assertEqual(reconstructed,'x'*1000)

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
