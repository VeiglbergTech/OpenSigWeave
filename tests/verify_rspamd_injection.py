"""Verify the end-to-end Rspamd callback fixtures with a MIME parser."""
from email import policy
from email.parser import BytesParser
from pathlib import Path
import sys

root = Path(sys.argv[1])

def read(name):
    message = BytesParser(policy=policy.default).parsebytes((root / name).read_bytes())
    assert all(not part.defects for part in message.walk()), (name, [p.defects for p in message.walk()])
    return message

plain = read("flat_plain.eml")
assert plain.get_content_type() == "multipart/alternative"
alternatives = list(plain.iter_parts())
assert [p.get_content_type() for p in alternatives] == ["text/plain", "text/html"]
assert all(p.get_content_charset() == "utf-8" for p in alternatives)
assert "Grüße" in alternatives[0].get_content()
assert "Contact (https://example.test/contact)" in alternatives[0].get_content()
assert "Grüße" in alternatives[1].get_content()

html = read("flat_html.eml")
assert html.get_content_type() == "text/html"
assert html.get_content_charset() == "utf-8"
assert "Grüße und Test" in html.get_content()
assert html["X-Custom"] == "folded\tand intact"

already = read("flat_already_signed.eml")
assert already.get_content().count("OpenSigWeave Footer") == 1
reply = read("flat_reply.eml")
assert reply.get_content().count("OpenSigWeave Footer") == 2
assert reply.get_content().index("OpenSigWeave Footer") < reply.get_content().index("gmail_quote")
forward = read("flat_forward.eml")
assert forward.get_content().index("OpenSigWeave Footer") < forward.get_content().index("gmail_quote")
assert forward.get_content().count("OpenSigWeave Footer") == 1
print("Rspamd callback matrix passed: flat plain/HTML, charset, folded headers, links, idempotency, quoted reply")
