#!/usr/bin/env python3
"""Independent last-in-chain Postfix Milter for OpenSigWeave strict mode.

The Rspamd Milter must run before this one. It sends an authenticated per-mail
receipt to a separate gate. This Milter rejects at EOM unless that one-time
receipt exists. Do NOT place this in the global/inbound port-25 chain.
Requires optional pymilter / libmilter, not included in OpenSigWeave's app.
"""
from __future__ import annotations

import logging
import os
import socket

import Milter

LOG = logging.getLogger("opensigweave.strict_milter")
REPLY = "OpenSigWeave: Signature processing could not be verified; please try again later."
MAX_POLICY_REPLY = 1024


def verify_receipt(queue_id: str, sender: str, username: str, *,
                   host: str, port: int, timeout: float = 4.0) -> bool:
    """Check and consume a per-message receipt via trusted internal TCP."""
    if not queue_id or not sender or not username:
        return False
    if any(c in queue_id + sender + username for c in "\r\n\x00"):
        return False
    if len(queue_id) > 64 or len(sender) > 320 or len(username) > 320:
        return False
    attrs = (
        "request=smtpd_access_policy\n"
        "protocol_state=END-OF-MESSAGE\n"
        f"queue_id={queue_id}\n"
        f"sender={sender}\n"
        f"sasl_username={username}\n"
        "\n"
    ).encode("utf-8")
    try:
        with socket.create_connection((host, port), timeout=timeout) as conn:
            conn.settimeout(timeout)
            conn.sendall(attrs)
            reply = bytearray()
            while len(reply) <= MAX_POLICY_REPLY:
                data = conn.recv(256)
                if not data:
                    break
                reply.extend(data)
                if b"\n\n" in reply:
                    break
            # Require an exact, well-formed allow response, not a prefix.
            return bytes(reply).strip() == b"action=DUNNO"
    except (OSError, TimeoutError):
        LOG.warning("strict receipt verification unavailable")
        return False


class StrictReceiptMilter(Milter.Base):
    def __init__(self):
        super().__init__()
        self.sender = ""
        self.username = ""

    def envfrom(self, f, *args):
        self.sender = f.strip("<>").strip().lower()
        self.username = self.getsymval("{auth_authen}") or ""
        return Milter.CONTINUE

    def eom(self):
        # The Milter is wired only to authenticated submission. Never impose
        # an outbound signature policy on unauthenticated incoming messages.
        username = self.getsymval("{auth_authen}") or self.username
        if not username:
            return Milter.CONTINUE
        queue_id = self.getsymval("i") or ""
        if verify_receipt(
            queue_id, self.sender, username,
            host=os.environ.get("OSW_STRICT_GATE_HOST", "127.0.0.1"),
            port=int(os.environ.get("OSW_STRICT_POLICY_PORT", "10043")),
        ):
            return Milter.CONTINUE
        LOG.warning("strict EOM rejection: no matching processing proof")
        self.setreply("451", "4.7.1", REPLY)
        return Milter.TEMPFAIL


def main():
    logging.basicConfig(level=os.environ.get("OSW_STRICT_LOG_LEVEL", "INFO"))
    socket_address = os.environ.get(
        "OSW_STRICT_MILTER_SOCKET", "inet:10045@127.0.0.1"
    )
    Milter.factory = StrictReceiptMilter
    Milter.set_flags(0)
    LOG.info("starting isolated strict receipt milter at %s", socket_address)
    Milter.runmilter("opensigweave-strict", socket_address, 30)


if __name__ == "__main__":
    main()
