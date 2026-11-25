#!/usr/bin/env python3
"""Manual, single-message live SMTP gate for a DISPOSABLE authenticated test mailbox.

Run once with --expect tempfail while the signature engine/Rspamd is deliberately
broken, and once with --expect accepted after recovery. No automatic retry.
Receiving a 4xx outside SMTP DATA does NOT count as passing fail-closed.
"""
from __future__ import annotations

import argparse
from email.message import EmailMessage
from email.utils import make_msgid
import os
import smtplib
import ssl
import sys


def submit_message(host: str, port: int, username: str, password: str,
                   sender: str, recipient: str, ssl_context=None,
                   factory=None) -> tuple[str, int | None]:
    if port not in (465, 587):
        raise ValueError("Only verified STARTTLS 587 or TLS 465 are supported")
    context = ssl_context or ssl.create_default_context()
    smtp_factory = factory or (smtplib.SMTP_SSL if port == 465 else smtplib.SMTP)
    mail = EmailMessage()
    mail["From"] = sender
    mail["To"] = recipient
    mail["Subject"] = "OpenSigWeave disposable SMTP fail-closed probe"
    mail["Message-ID"] = make_msgid(domain="opensigweave.test")
    mail.set_content("OpenSigWeave controlled submission probe. No user data.")
    with smtp_factory(host, port, timeout=15, context=context) if port == 465 else smtp_factory(host, port, timeout=15) as connection:
        if port == 587:
            connection.ehlo()
            connection.starttls(context=context)
            connection.ehlo()
        connection.login(username, password)
        try:
            # Only a DATA-stage 4xx proves the Milter rejected before 250 queued.
            connection.send_message(mail, from_addr=sender, to_addrs=[recipient])
        except smtplib.SMTPDataError as exc:
            return ("data_rejected", exc.smtp_code)
        # RCPT rejections raise SMTPRecipientsRefused and intentionally do not pass.
    return ("accepted", None)


def main_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=587)
    parser.add_argument("--username", required=True)
    parser.add_argument("--sender", required=True)
    parser.add_argument("--recipient", required=True)
    parser.add_argument("--expect", choices=("tempfail", "accepted"), required=True)
    parser.add_argument("--confirm-disposable-test", action="store_true",
                        help="Explicitly confirm recipient is an isolated test mailbox")
    args = parser.parse_args(argv)
    password = os.environ.get("OSW_TEST_SMTP_PASSWORD", "")
    if not args.confirm_disposable_test or not password:
        parser.error("Require --confirm-disposable-test and OSW_TEST_SMTP_PASSWORD")
    try:
        status, code = submit_message(
            args.host, args.port, args.username, password, args.sender, args.recipient,
        )
    except (OSError, smtplib.SMTPException, ssl.SSLError, ValueError) as exc:
        print(f"FAIL: SMTP probe did not reach an accepted/DATA-rejected decision ({type(exc).__name__})")
        return 2
    if args.expect == "tempfail":
        passed = status == "data_rejected" and code is not None and 400 <= code < 500
    else:
        passed = status == "accepted"
    print(f"{'PASS' if passed else 'FAIL'}: SMTP result={status}, code={code or '250/accepted'}; expected={args.expect}")
    if status == "accepted":
        print("WARNING: The test message was accepted by the SMTP server; inspect its delivered raw EML.")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main_cli())
