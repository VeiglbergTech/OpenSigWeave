"""Disposable Postfix integration runner for the real OpenSigWeave Milter class."""
import logging
import Milter

from adapters.postfix_milter import build_milter_class

logging.basicConfig(level="INFO")


def mock_engine(api_url, api_key, sender, *, mode=None, signature_name=None,
                signature_code=None, timeout=5.0):
    if sender != "sender@example.org":
        raise AssertionError(f"Unexpected envelope sender: {sender}")
    return {
        "html": "<p>OpenSigWeave integration signature</p>",
        "inject_on_replies": False,
        "trim_whitespace": True,
        "strip_device_signatures": False,
    }


Milter.factory = build_milter_class(
    Milter,
    "https://engine.invalid",
    "ci-only-key-that-is-not-a-real-secret-123456",
    timeout=2.0,
    signature_fetcher=mock_engine,
)
Milter.set_flags(Milter.ADDHDRS | Milter.CHGHDRS | Milter.MODBODY)
Milter.runmilter("opensigweave-ci", "inet:8899@127.0.0.1", 30)
