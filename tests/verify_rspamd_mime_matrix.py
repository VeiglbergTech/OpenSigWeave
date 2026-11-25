"""Cross-validate the Rspamd nested fixture with Python's MIME parser."""
from email import policy
from email.parser import BytesParser
from pathlib import Path
import sys

raw = Path(sys.argv[1]).read_bytes()
message = BytesParser(policy=policy.default).parsebytes(raw)
parts = list(message.walk())
assert all(not item.defects for item in parts), [
    (item.get_content_type(), item.defects) for item in parts
]
types = [item.get_content_type() for item in parts]
assert types == [
    "multipart/mixed",
    "multipart/alternative",
    "text/plain",
    "multipart/related",
    "text/html",
    "image/png",
    "multipart/alternative",
    "text/plain",
    "text/html",
    "application/octet-stream",
], types
assert len(message["Subject"]) > 1500, "folded long header lost"
assert message["X-Trace"] == "value"
assert parts[2].get_content().startswith("Hallo München 🌍")
assert "OSW_INSERT" in parts[2].get_content()
assert parts[4].get("Content-ID") == "<html-part>"
assert parts[4]["X-Body-Trace"] == "123"
assert "Grüße" in parts[4].get_content()
assert 'cid:logo1' in parts[4].get_content()
assert parts[5].get("Content-ID") == "<logo1>"
assert parts[5].get_payload(decode=True) == b"\x00\x01\x02\xff"
assert parts[5].get_filename() == "logo.png"
assert parts[7].get_content().index("OSW_INSERT") < parts[7].get_content().index("On Wednesday wrote:")
assert parts[8].get_content().index("OSW_INSERT") < parts[8].get_content().index("gmail_quote")
assert parts[9].get_payload(decode=True) == b"\x01\x02\x03\x04"
assert parts[9]["X-File-Trace"] == "intact"
assert parts[9].get_filename() == "document.bin"
print("Rspamd MIME matrix passed: nested siblings, inline CID, attachments, UTF-8, long headers, QP/base64 input, replies")
