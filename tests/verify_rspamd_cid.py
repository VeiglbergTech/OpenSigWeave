"""Verify CID MIME fixtures parse with the standard Python email parser."""
from email import policy
from email.parser import BytesParser
from pathlib import Path
import sys

message = BytesParser(policy=policy.default).parsebytes(Path(sys.argv[1]).read_bytes())
assert message.get_content_type() == "multipart/related"
assert message.get_param("type") == "multipart/alternative"
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
assert parts[1].get_payload(decode=True) == b"\x89PNG\r\n\x1a\ntest"
assert message["X-Trace"] == "intact"
# Check other MIME layouts in addition to the original alternative fixture.
html_only = BytesParser(policy=policy.default).parsebytes(Path(sys.argv[1] + ".html").read_bytes())
assert not html_only.defects, html_only.defects
assert html_only.get_content_type() == "multipart/related"
assert html_only.get_param("type") == "text/html"
html_root, html_image = list(html_only.iter_parts())
assert html_root.get_content_type() == "text/html"
assert "cid:osw-" in html_root.get_content()
assert html_image.get_content_disposition() == "inline"

mixed = BytesParser(policy=policy.default).parsebytes(Path(sys.argv[1] + ".mixed").read_bytes())
assert not mixed.defects, mixed.defects
assert mixed.get_content_type() == "multipart/related"
assert mixed.get_param("type") == "multipart/mixed"
mixed_root, new_image = list(mixed.iter_parts())
assert mixed_root.get_content_type() == "multipart/mixed"
original_related, original_attachment = list(mixed_root.iter_parts())
assert original_related.get_content_type() == "multipart/related"
original_html, old_image = list(original_related.iter_parts())
assert "cid:brand@example.org" in original_html.get_content()
assert "cid:osw-" in original_html.get_content()
assert old_image.get("Content-ID") == "<brand@example.org>"
assert old_image.get_payload(decode=True) == b"old-picture"
assert original_attachment.get_content_disposition() == "attachment"
assert original_attachment.get_filename() == "report.dat"
assert original_attachment.get_payload(decode=True) == bytes(range(6))
assert new_image.get_payload(decode=True) == b"\x89PNG\r\n\x1a\n" + b"test" + b"ABC" * 40
cids = [part.get("Content-ID") for part in mixed.walk() if part.get("Content-ID")]
assert len(cids) == len(set(cids)), cids
# RFC 2045: transport base64 lines must not exceed 76 characters.
new_image_wire = Path(sys.argv[1] + ".mixed").read_bytes().split(
    b"Content-ID: <" + new_image["Content-ID"].strip("<>").encode() + b">\r\n\r\n", 1
)[1].split(b"\r\n--", 1)[0]
assert all(len(line) <= 76 for line in new_image_wire.splitlines()), "base64 lines exceed 76 characters"
print("CID wrapping passed: alternative, HTML-only, mixed/related, original CID/attachment preservation and 76-column base64")
