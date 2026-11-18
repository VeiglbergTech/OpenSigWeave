"""Adapter-neutral detection of reply/quote boundaries."""
from __future__ import annotations

import re
from html.parser import HTMLParser

_PLAIN = (
    re.compile(r"(?im)^[ \t]*[-_]{2,}\s*(?:original message|forwarded message)\s*[-_]{2,}\s*$"),
    re.compile(r"(?im)^[ \t]*On .{1,240}\bwrote:\s*$"),
    re.compile(r"(?im)^[ \t]*Am .{1,240}\bschrieb .{1,160}:\s*$"),
    re.compile(r"(?im)^[ \t]*Le .{1,240}\bécrit\s*:\s*$"),
    re.compile(r"(?im)^[ \t]*El .{1,240}\bescribió:\s*$"),
    re.compile(r"(?im)^[ \t]*(?:From|Von):\s*.+\n(?:Sent|Gesendet|Date|Datum):\s*.+\n(?:To|An):\s*.+"),
    re.compile(r"(?im)^[ \t]*_{5,}\s*\n\s*(?:From|Von):\s*.+"),
    re.compile(r"(?m)^[ \t]*>"),
)
_HTML = (
    re.compile(r'''(?is)<div\b[^>]*class\s*=\s*['"][^'"]*gmail_quote'''),
    re.compile(r'''(?is)<div\b[^>]*class\s*=\s*['"][^'"]*yahoo_quoted'''),
    re.compile(r'''(?is)<div\b[^>]*id\s*=\s*['"]divRplyFwdMsg['"]'''),
    re.compile(r'''(?is)<div\b[^>]*class\s*=\s*['"][^'"]*moz-cite-prefix'''),
    re.compile(r'''(?is)<div\b[^>]*data-marker\s*=\s*['"]__QUOTED_TEXT__'''),
    re.compile(r'''(?is)<span\b[^>]*id\s*=\s*['"]OLK_SRC_BODY_SECTION['"]'''),
    re.compile(r"(?is)<blockquote\b"),
    re.compile(r"(?is)<hr\b"),
    re.compile(r'''(?is)<div\b[^>]*class\s*=\s*['"][^'"]*(?:quote|quoted|reply-forward)'''),
)


class _QuoteParser(HTMLParser):
    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=False)
        self.line_offsets = [0]
        self.line_offsets.extend(match.end() for match in re.finditer("\n", source))
        self.boundaries: list[int] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.casefold(): (value or "").casefold() for key, value in attrs}
        classes = set(values.get("class", "").split())
        if (tag.casefold() == "blockquote" or
            classes.intersection({"gmail_quote", "yahoo_quoted", "quote", "moz-cite-prefix"}) or
            values.get("id") in {"divrplyfwdmsg", "olk_src_body_section"} or
            values.get("data-marker") == "__quoted_text__"):
            line, column = self.getpos()
            if line - 1 < len(self.line_offsets):
                self.boundaries.append(self.line_offsets[line - 1] + column)


def find_plain_quote_boundary(body: str) -> int | None:
    positions = [m.start() for pattern in _PLAIN if (m := pattern.search(body))]
    return min(positions) if positions else None


def find_html_quote_boundary(body: str) -> int | None:
    positions = [m.start() for pattern in _HTML if (m := pattern.search(body))]
    try:
        parser = _QuoteParser(body)
        parser.feed(body)
        parser.close()
        positions.extend(parser.boundaries)
    except (ValueError, AssertionError):
        pass
    return min(positions) if positions else None


def insert_signature(
    body: str, signature: str, *, html: bool, trim_whitespace: bool = True
) -> tuple[str, bool]:
    """Insert before quoted history, optionally preserving existing whitespace."""
    boundary = find_html_quote_boundary(body) if html else find_plain_quote_boundary(body)
    if boundary is None:
        if html:
            match = re.search(r"</body\\s*>", body, re.I)
            fragment = "<br><br>" + signature
            if match:
                before = body[:match.start()]
                if trim_whitespace:
                    before = before.rstrip()
                return before + fragment + body[match.start():], False
            before = body.rstrip() if trim_whitespace else body
            return before + fragment, False
        before = body.rstrip() if trim_whitespace else body
        return before + "\\n\\n" + signature, False
    before, after = body[:boundary], body[boundary:]
    if trim_whitespace:
        before, after = before.rstrip(), after.lstrip()
    if html:
        return before + "<br><br>" + signature + "<br><br>" + after, True
    return before + "\\n\\n" + signature + "\\n\\n" + after, True
