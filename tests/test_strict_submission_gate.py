"""Regression tests for the independent Postfix END-OF-MESSAGE strict gate."""
import asyncio
import unittest

from scripts.opensigweave_strict_gate import REJECT, StrictGate, handle_policy, handle_receipt

SECRET = "test-secret-for-isolated-unit-tests-only-123456"


def allowed_attrs(queue_id="ABCDEF0123", sender="u@example.test", user="u@example.test"):
    return {
        "request": "smtpd_access_policy",
        "protocol_state": "END-OF-MESSAGE",
        "queue_id": queue_id,
        "sender": sender,
        "sasl_username": user,
    }


def receipt_attrs(queue_id="ABCDEF0123", sender="u@example.test", user="u@example.test",
                  decision="signed"):
    return {"queue_id": queue_id, "sender": sender, "username": user, "decision": decision}


class StrictGateTests(unittest.TestCase):
    def setUp(self):
        self.now = 500.0
        self.gate = StrictGate(SECRET, clock=lambda: self.now)

    def test_rejects_missing_plugins_or_receipt(self):
        self.assertEqual(self.gate.policy_action(allowed_attrs()), REJECT)

    def test_one_time_signature_receipt(self):
        self.assertTrue(self.gate.record(receipt_attrs(), SECRET))
        self.assertEqual(self.gate.policy_action(allowed_attrs()), "DUNNO")
        self.assertEqual(self.gate.policy_action(allowed_attrs()), REJECT)

    def test_no_untrusted_sender_or_fake_receipt(self):
        self.assertFalse(self.gate.record(receipt_attrs(), "invalid-key"))
        self.assertEqual(self.gate.policy_action(allowed_attrs()), REJECT)
        self.assertTrue(self.gate.record(receipt_attrs(), SECRET))
        self.assertEqual(
            self.gate.policy_action(allowed_attrs(sender="spoof@example.test")), REJECT
        )
        self.assertEqual(self.gate.policy_action(allowed_attrs(user="other@example.test")), REJECT)
        self.assertEqual(self.gate.policy_action(allowed_attrs()), "DUNNO")

    def test_policy_skip_and_protected_messages_are_allowed_with_proof(self):
        for decision in ("policy_skip", "protected_skip", "already_signed"):
            self.assertTrue(self.gate.record(receipt_attrs(decision=decision), SECRET))
            self.assertEqual(self.gate.policy_action(allowed_attrs()), "DUNNO")

    def test_expiry_cannot_replay(self):
        self.assertTrue(self.gate.record(receipt_attrs(), SECRET))
        self.now += 91
        self.assertEqual(self.gate.policy_action(allowed_attrs()), REJECT)

    def test_never_applies_outbound_policy_to_inbound(self):
        attrs = allowed_attrs()
        attrs.pop("sasl_username")
        self.assertEqual(self.gate.policy_action(attrs), "DUNNO")

    def test_protocol_state_and_queue_validation(self):
        attrs = allowed_attrs()
        attrs["protocol_state"] = "RCPT"
        self.assertEqual(self.gate.policy_action(attrs), REJECT)
        for invalid_id in ("", "x", "../tmp", "HEADER\nBYPASS"):
            self.assertFalse(self.gate.record(receipt_attrs(queue_id=invalid_id), SECRET))
            self.assertEqual(self.gate.policy_action(allowed_attrs(queue_id=invalid_id)), REJECT)

    def test_invalid_decision_and_small_secret_rejected(self):
        self.assertFalse(self.gate.record(receipt_attrs(decision="accepted"), SECRET))
        self.assertEqual(self.gate.policy_action(allowed_attrs()), REJECT)
        with self.assertRaises(ValueError):
            StrictGate("short")


class StrictGateWireTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.gate = StrictGate(SECRET)

    async def _wire(self, handler, raw):
        server = await asyncio.start_server(
            lambda r, w: handler(r, w, self.gate), "127.0.0.1", 0
        )
        async with server:
            port = server.sockets[0].getsockname()[1]
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(raw)
            await writer.drain()
            result = await reader.read()
            writer.close()
            await writer.wait_closed()
            return result

    async def test_valid_receipt_allows_exactly_one_postfix_request(self):
        body = b"queue_id=ABCDEF0123&sender=u%40example.test&username=u%40example.test&decision=signed"
        data = (b"POST /receipt HTTP/1.1\r\nHost: gate\r\n"
                + b"X-OSW-Receipt-Key: " + SECRET.encode()
                + b"\r\nContent-Length: " + str(len(body)).encode()
                + b"\r\n\r\n" + body)
        self.assertIn(b"204 No Content", await self._wire(handle_receipt, data))
        policy_data = (
            b"request=smtpd_access_policy\nprotocol_state=END-OF-MESSAGE\n"
            b"queue_id=ABCDEF0123\nsender=u@example.test\n"
            b"sasl_username=u@example.test\n\n"
        )
        self.assertEqual(await self._wire(handle_policy, policy_data), b"action=DUNNO\n\n")
        response = await self._wire(handle_policy, policy_data)
        self.assertIn(b"451 4.7.1", response)

    async def test_refuses_bad_auth_receipt(self):
        body = b"queue_id=ABCDEF0123&sender=u%40example.test&username=u%40example.test&decision=signed"
        data = (b"POST /receipt HTTP/1.1\r\nX-OSW-Receipt-Key: wrong\r\n"
                + b"Content-Length: " + str(len(body)).encode()
                + b"\r\n\r\n" + body)
        self.assertIn(b"403 Forbidden", await self._wire(handle_receipt, data))
        self.assertEqual(self.gate.policy_action(allowed_attrs()), REJECT)


if __name__ == "__main__":
    unittest.main()
