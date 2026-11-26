#!/usr/bin/env python3
"""Independent, fail-closed Postfix submission gate for OpenSigWeave.

The Rspamd adapter must submit a one-time processing receipt for each
authenticated message. At END-OF-MESSAGE Postfix must obtain that receipt
from this independent process before SMTP 250. A running Rspamd with both
OpenSigWeave plugins missing cannot issue a receipt.

Uses only the Python standard library. Deliberately refuses to start without
an explicitly configured 32+ character secret. Intended for isolated staging
until live Postfix/Milter ordering has been verified.
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import os
import re
import signal
import time
from dataclasses import dataclass
from urllib.parse import parse_qs

LOG = logging.getLogger("opensigweave.strict_gate")
MAX_REQUEST = 8192
MAX_RECEIPTS = 10000
RECEIPT_LIFETIME = 90
QUEUE_ID_RE = re.compile(r"^[A-Za-z0-9]{4,64}$")
DECISIONS = frozenset({"signed", "policy_skip", "protected_skip", "already_signed"})
REJECT = (
    "451 4.7.1 OpenSigWeave: Signature processing could not be verified; "
    "please try again later."
)


@dataclass(frozen=True)
class Receipt:
    expires: float
    decision: str


class StrictGate:
    """In-memory one-time attestation cache, keyed by trusted SMTP metadata."""

    def __init__(self, secret: str, *, clock=time.monotonic):
        if len(secret) < 32 or secret.startswith("REPLACE_"):
            raise ValueError("Set a unique, unpredictable OSW_STRICT_RECEIPT_KEY (32+ chars)")
        self.secret = secret
        self.clock = clock
        self.receipts: dict[tuple[str, str, str], Receipt] = {}

    @staticmethod
    def receipt_key(queue_id: str, sender: str, username: str):
        if not QUEUE_ID_RE.fullmatch(queue_id):
            return None
        if not sender or not username or len(sender) > 320 or len(username) > 320:
            return None
        if any(ch in sender + username for ch in "\r\n\x00"):
            return None
        return (queue_id.upper(), sender.lower(), username.lower())

    def prune(self):
        now = self.clock()
        for key, value in list(self.receipts.items()):
            if value.expires <= now:
                del self.receipts[key]

    def record(self, params: dict[str, str], presented_key: str) -> bool:
        if not hmac.compare_digest(self.secret, presented_key):
            return False
        self.prune()
        key = self.receipt_key(
            params.get("queue_id", ""), params.get("sender", ""), params.get("username", "")
        )
        if key is None or params.get("decision") not in DECISIONS:
            return False
        if len(self.receipts) >= MAX_RECEIPTS and key not in self.receipts:
            LOG.error("receipt capacity exceeded; refusing new proof")
            return False
        self.receipts[key] = Receipt(self.clock() + RECEIPT_LIFETIME, params["decision"])
        return True

    def policy_action(self, attributes: dict[str, str]) -> str:
        # Explicitly scope this independent enforcement to authenticated SMTP.
        username = attributes.get("sasl_username", "")
        if not username:
            return "DUNNO"
        if attributes.get("protocol_state") != "END-OF-MESSAGE":
            return REJECT
        self.prune()
        key = self.receipt_key(
            attributes.get("queue_id", ""), attributes.get("sender", ""), username
        )
        if key is None:
            return REJECT
        # Never reuse an approval across submissions, including a failed retry.
        receipt = self.receipts.pop(key, None)
        if receipt is None:
            LOG.warning("strict submission denied: processing receipt missing")
            return REJECT
        LOG.info("strict submission permitted after one-time %s receipt", receipt.decision)
        return "DUNNO"


async def handle_receipt(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                         gate: StrictGate):
    status = "400 Bad Request"
    try:
        request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=4)
        if len(request) > MAX_REQUEST:
            raise ValueError("oversized headers")
        header, _ = request.split(b"\r\n\r\n", 1)
        lines = header.decode("ascii").split("\r\n")
        if lines[0] == "POST /receipt HTTP/1.1":
            pairs = [line.split(":", 1) for line in lines[1:] if ":" in line]
            fields = {name.lower().strip(): value.strip() for name, value in pairs}
            length = int(fields.get("content-length", "-1"))
            if 0 <= length <= 2048:
                body = await asyncio.wait_for(reader.readexactly(length), timeout=4)
                parsed = parse_qs(body.decode("ascii"), keep_blank_values=True,
                                  max_num_fields=6)
                params = {key: values[0] for key, values in parsed.items()
                          if len(values) == 1}
                if gate.record(params, fields.get("x-osw-receipt-key", "")):
                    status = "204 No Content"
                else:
                    status = "403 Forbidden"
        response = f"HTTP/1.1 {status}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
        writer.write(response.encode("ascii"))
        await writer.drain()
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, UnicodeError,
            ValueError, OverflowError, asyncio.LimitOverrunError):
        pass
    finally:
        writer.close()
        await writer.wait_closed()


async def handle_policy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                        gate: StrictGate):
    attributes = {}
    try:
        # Postfix policy protocol: ASCII-compatible key=value lines ending
        # in a blank line; cap input to avoid unbounded allocations.
        size = 0
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=5)
            size += len(line)
            if size > MAX_REQUEST or not line:
                raise ValueError("invalid policy request")
            if line in (b"\n", b"\r\n"):
                break
            decoded = line.decode("utf-8").rstrip("\r\n")
            if "=" not in decoded:
                raise ValueError("bad policy attribute")
            key, value = decoded.split("=", 1)
            attributes[key] = value
        action = gate.policy_action(attributes)
    except (asyncio.TimeoutError, UnicodeError, ValueError):
        action = REJECT
    try:
        writer.write(f"action={action}\n\n".encode("ascii"))
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


async def serve(gate: StrictGate, *, host: str, policy_port: int, receipt_port: int):
    async with await asyncio.start_server(
        lambda r, w: handle_policy(r, w, gate), host, policy_port, limit=MAX_REQUEST
    ) as policy, await asyncio.start_server(
        lambda r, w: handle_receipt(r, w, gate), host, receipt_port, limit=MAX_REQUEST
    ) as receipts:
        LOG.info("strict gate ready: policy=%s receipt=%s", policy_port, receipt_port)
        await asyncio.gather(policy.serve_forever(), receipts.serve_forever())


def main():
    logging.basicConfig(level=os.environ.get("OSW_STRICT_LOG_LEVEL", "INFO"))
    secret = os.environ.get("OSW_STRICT_RECEIPT_KEY", "")
    gate = StrictGate(secret)
    host = os.environ.get("OSW_STRICT_BIND", "127.0.0.1")
    asyncio.run(serve(
        gate, host=host,
        policy_port=int(os.environ.get("OSW_STRICT_POLICY_PORT", "10043")),
        receipt_port=int(os.environ.get("OSW_STRICT_RECEIPT_PORT", "10044")),
    ))


if __name__ == "__main__":
    main()
