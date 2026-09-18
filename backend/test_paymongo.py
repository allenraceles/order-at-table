"""Focused checks for PayMongo request amounts and webhook authentication."""

import hashlib
import hmac
import io
import json
import time
import unittest
import asyncio
from contextlib import contextmanager
from unittest.mock import patch

from backend.app.main import create_paymongo_session, paymongo_webhook, verify_paymongo_signature


class PayMongoTests(unittest.TestCase):
    def test_checkout_uses_server_prices_in_centavos(self):
        response = {"data": {"id": "cs_test", "attributes": {"checkout_url": "https://checkout.paymongo.com/example"}}}
        captured = {}

        def fake_urlopen(request, timeout):
            captured["request"] = request
            self.assertEqual(timeout, 15)
            return io.BytesIO(json.dumps(response).encode())

        with patch.dict("os.environ", {"PAYMONGO_SECRET_KEY": "sk_test_example", "ORDER_CUSTOMER_URL": "http://localhost:5173"}):
            with patch("backend.app.main.urlopen", fake_urlopen):
                result = create_paymongo_session("1234", 18, [
                    {"name": "Meal", "option": None, "quantity": 2, "unit_price": 175},
                ])
        self.assertEqual(result, ("cs_test", "https://checkout.paymongo.com/example"))
        body = json.loads(captured["request"].data)
        attributes = body["data"]["attributes"]
        self.assertEqual(attributes["line_items"][0]["amount"], 17500)
        self.assertEqual(attributes["line_items"][0]["quantity"], 2)
        self.assertEqual(attributes["payment_method_types"], ["gcash", "qrph"])
        self.assertEqual(attributes["reference_number"], "1234")

    def test_signature_rejects_tampering_and_replay(self):
        body = b'{"data":{"type":"checkout_session.payment.paid"}}'
        timestamp = str(int(time.time()))
        signature = hmac.new(b"webhook_secret", timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
        header = f"t={timestamp},te={signature},li="
        self.assertTrue(verify_paymongo_signature(body, header, "webhook_secret", False))
        self.assertFalse(verify_paymongo_signature(body + b" ", header, "webhook_secret", False))
        self.assertFalse(verify_paymongo_signature(body, header, "wrong_secret", False))
        self.assertFalse(verify_paymongo_signature(body, header, "webhook_secret", True))
        stale = str(int(time.time()) - 3600)
        self.assertFalse(verify_paymongo_signature(body, f"t={stale},te={signature},li=", "webhook_secret", False))

    def test_paid_checkout_accepts_paymongo_event_resource(self):
        session = {"id": "cs_test", "attributes": {"reference_number": "1234", "payments": [
            {"attributes": {"status": "paid", "currency": "PHP", "amount": 29900}}
        ]}}
        event = {"data": {"id": "evt_test", "type": "event", "attributes": {
            "type": "checkout_session.payment.paid", "livemode": False, "data": session,
        }}}
        body = json.dumps(event).encode()
        timestamp = str(int(time.time()))
        signature = hmac.new(b"webhook_secret", timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()

        class RequestStub:
            headers = {"Paymongo-Signature": f"t={timestamp},te={signature}"}

            async def body(self):
                return body

        class ConnectionStub:
            def execute(self, query, params):
                if query.lstrip().startswith("SELECT"):
                    self.params = params
                    return self
                self.updated = True

            def fetchone(self):
                return {"total": 299, "status": "awaiting_payment"}

        connection = ConnectionStub()

        @contextmanager
        def fake_database():
            yield connection

        with patch.dict("os.environ", {"PAYMONGO_SECRET_KEY": "sk_test_example", "PAYMONGO_WEBHOOK_SECRET": "webhook_secret"}):
            with patch("backend.app.main.database", fake_database):
                result = asyncio.run(paymongo_webhook(RequestStub()))
        self.assertEqual(result, {"ok": True})
        self.assertEqual(connection.params, ("1234", "cs_test"))
        self.assertTrue(connection.updated)


if __name__ == "__main__":
    unittest.main()
