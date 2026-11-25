"""Validate Rspamd multipart fixture using Python's real MIME parser."""
from email import policy
from email.parser import BytesParser
from pathlib import Path
import sys

message = BytesParser(policy=policy.default).parsebytes(Path(sys.argv[1]).read_bytes())
assert message.is_multipart()
parts = list(message.walk())
assert len(parts) == 5, [part.get_content_type() for part in parts]
assert all(not part.defects for part in parts), [(p.get_content_type(), p.defects) for p in parts]
plain, html, attachment = parts[2:]
assert plain.get_content_type() == "text/plain"
assert html.get_content_type() == "text/html"
assert attachment.get_content_type() == "application/octet-stream"
assert plain.get_content().index("SIGNATURE") < plain.get_content().index("On Monday wrote:")
assert html.get_content().index("SIGNATURE") < html.get_content().index("gmail_quote")
assert html.get("Content-ID") == "<body-id>"
assert attachment.get_payload(decode=True) == b"\x00\x01\x02\xff"
assert attachment.get_filename() == "test.bin"
print("Rspamd MIME fixture passed: nested multipart, reply placement and preserved attachment")
