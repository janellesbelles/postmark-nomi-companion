"""No live traffic: documented errors, redaction and uncertain outcomes."""
import io
import json
import unittest
import urllib.error
from unittest.mock import patch
import nomi_client


class NomiErrorTests(unittest.TestCase):
    def failure(self, code, body):
        error = urllib.error.HTTPError('https://api.nomi.ai/mock',code,'mock',{},io.BytesIO(body))
        with patch.object(nomi_client.urllib.request,'urlopen',side_effect=error) as request:
            try:
                nomi_client.send_chat('synthetic-secret','synthetic-uuid','synthetic-private-message')
            except Exception as caught:
                request.assert_called_once()
                return caught
        self.fail('Expected failure')

    def test_documented_rejections_have_safe_actionable_reason(self):
        for reason in nomi_client.REJECTION_REASONS:
            with self.subTest(reason=reason):
                caught = self.failure(400,json.dumps({'error':{'type':reason,'detail':'synthetic-secret synthetic-private-message'}}).encode())
                self.assertIsInstance(caught,nomi_client.RejectedNomiRequest)
                self.assertIn(reason,str(caught))
                self.assertNotIn('synthetic-secret',str(caught))
                self.assertNotIn('synthetic-private-message',str(caught))

    def test_no_reply_and_unrecognized_or_malformed_errors_remain_uncertain(self):
        for body in [b'{"error":{"type":"NoReply"}}',b'{"error":{"type":"synthetic-secret"}}',b'not JSON',b'{"error":null}',b'{"error":{"type":[]}}']:
            with self.subTest(body=body):
                caught = self.failure(400,body)
                self.assertIsInstance(caught,nomi_client.UncertainNomiResult)
                self.assertNotIn('synthetic-secret',str(caught))

    def test_server_error_cannot_be_downgraded_by_rejection_name(self):
        self.assertIsInstance(self.failure(500,b'{"error":{"type":"NomiStillResponding"}}'),nomi_client.UncertainNomiResult)

    def test_unusable_success_response_stays_uncertain(self):
        for payload in [[], None, {"replyMessage": []}, {"replyMessage": {"text": {"private": "data"}}}]:
            with self.subTest(payload=payload), patch.object(nomi_client.urllib.request, 'urlopen') as request:
                request.return_value.__enter__.return_value = io.BytesIO(json.dumps(payload).encode())
                with self.assertRaises(nomi_client.UncertainNomiResult):
                    nomi_client.send_chat('synthetic-secret','synthetic-uuid','synthetic-message')
