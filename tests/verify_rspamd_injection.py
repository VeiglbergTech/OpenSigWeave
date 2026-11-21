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
# SOGo's German attribution header is itself quoted history, even when a
# previous message contains a rendered OpenSigWeave signature.
sogo = read("flat_sogo_nested_reply.eml")
sogo_html = sogo.get_content()
attribution = "Am Samstag, November 21, 2026 16:20 CET, schrieb"
assert sogo_html.count("OpenSigWeave Footer") == 2, "quoted signature lost or duplicated"
assert sogo_html.index("test 4") < sogo_html.index("OpenSigWeave Footer") < sogo_html.index(attribution), (
    "new signature inserted after SOGo quote attribution instead of before it"
)
assert sogo_html.index(attribution) < sogo_html.index("hi martin"), "quoted reply was damaged"

sogo_paragraph = read("flat_sogo_paragraph_reply.eml")
paragraph_html = sogo_paragraph.get_content()
assert paragraph_html.count("OpenSigWeave Footer") == 1
assert paragraph_html.index("OpenSigWeave Footer") < paragraph_html.index(attribution), (
    "paragraph-wrapped SOGo attribution must remain behind the new signature"
)

multipart_sogo = read("multipart_sogo_nested_reply.eml")
assert multipart_sogo.get_content_type() == "multipart/alternative"
sogo_alternatives = list(multipart_sogo.iter_parts())
assert [part.get_content_type() for part in sogo_alternatives] == ["text/plain", "text/html"]
for part in sogo_alternatives:
    body = part.get_content()
    assert body.count("OpenSigWeave Footer") == 2, (
        "current plus quoted signature must each appear once in both MIME alternatives"
    )
    assert body.index("test 4") < body.index("OpenSigWeave Footer") < body.index(attribution), (
        f"{part.get_content_type()}: signature placed after first SOGo attribution"
    )
    assert body.index(attribution) < body.rindex("OpenSigWeave Footer"), (
        f"{part.get_content_type()}: prior signature moved outside quoted history"
    )
    assert "hi martin" in body

forward = read("flat_forward.eml")
assert forward.get_content().index("OpenSigWeave Footer") < forward.get_content().index("gmail_quote")
assert forward.get_content().count("OpenSigWeave Footer") == 1
print("Rspamd callback matrix passed: flat plain/HTML, charset, folded headers, links, idempotency, quoted reply")
