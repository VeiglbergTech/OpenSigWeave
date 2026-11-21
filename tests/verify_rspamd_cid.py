"""Verify CID MIME fixtures parse with the standard Python email parser."""
from email import policy
from email.parser import BytesParser
from pathlib import Path
import sys

message = BytesParser(policy=policy.default).parsebytes(Path(sys.argv[1]).read_bytes())
assert message.get_content_type() == "multipart/related"
assert not message.defects, message.defects
parts = list(message.iter_parts())
assert len(parts) == 2
assert parts[0].get_content_type() == "multipart/alternative"
plain, html = list(parts[0].iter_parts())
assert plain.get_content() == "Hello"
assert 'cid:osw-' in html.get_content()
assert parts[1].get_content_type() == "image/png"
assert parts[1].get_content_disposition() == "inline"
assert parts[1].get("Content-ID", "").startswith("<osw-")
assert parts[1].get_payload(decode=True) == b"\\x89PNG\\r\\n\\x1a\\ntest"
assert message["X-Trace"] == "intact"
print("CID wrapping passed: nested alternative, immutable image, original mail metadata")
