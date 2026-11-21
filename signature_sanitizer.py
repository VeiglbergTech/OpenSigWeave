"""Allowlist-based HTML sanitization for untrusted signature templates."""
import nh3
import re
import secrets


_ALLOWED_TAGS = {
    "a", "b", "blockquote", "br", "center", "code", "div", "em", "font",
    "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "li", "ol",
    "p", "pre", "s", "small", "span", "strong", "sub", "sup", "table",
    "tbody", "td", "tfoot", "th", "thead", "tr", "u", "ul",
}
_ALLOWED_ATTRIBUTES = {
    "a": {"href", "title", "target"},
    "img": {"src", "alt", "title", "width", "height", "border"},
    "font": {"color", "face", "size"},
    "table": {"align", "bgcolor", "border", "cellpadding", "cellspacing", "width", "height"},
    "td": {"align", "valign", "bgcolor", "colspan", "rowspan", "width", "height"},
    "th": {"align", "valign", "bgcolor", "colspan", "rowspan", "width", "height"},
    "tr": {"align", "valign", "bgcolor"},
    "div": {"align"},
    "p": {"align"},
    "ol": {"start", "type"},
    "li": {"value"},
    "*": {"style", "class"},
}
_ALLOWED_STYLE_PROPERTIES = {
    "background-color", "border", "border-bottom", "border-collapse",
    "border-color", "border-left", "border-radius", "border-right",
    "border-spacing", "border-style", "border-top", "border-width",
    "color", "display", "font-family", "font-size", "font-style",
    "font-weight", "height", "line-height", "margin", "margin-bottom",
    "margin-left", "margin-right", "margin-top", "max-width", "padding",
    "padding-bottom", "padding-left", "padding-right", "padding-top",
    "text-align", "text-decoration", "vertical-align", "white-space",
    "width",
}
_CLEANER = nh3.Cleaner(
    tags=_ALLOWED_TAGS,
    clean_content_tags={"script", "style", "iframe", "object", "embed", "svg", "math", "form"},
    attributes=_ALLOWED_ATTRIBUTES,
    url_schemes={"http", "https", "mailto", "tel"},
    url_relative="deny",
    filter_style_properties=_ALLOWED_STYLE_PROPERTIES,
    strip_comments=True,
    link_rel="noopener noreferrer",
)


_RESIZED_FIGURE = re.compile(r'<figure\b(?P<attrs>[^>]*)>\s*(?P<img><img\b[^>]*>)\s*</figure>', re.I | re.S)
_RESIZE_WIDTH = re.compile(r'(?:^|;)\s*width\s*:\s*(\d+(?:\.\d+)?)(px|%)(?=\s|;|$)', re.I)


def _flatten_resized_figure(match: re.Match) -> str:
    """Mirror CKEditor figure sizing onto <img> for email clients."""
    attrs = match.group("attrs")
    if not re.search(r'image_resized', attrs, re.I):
        return match.group(0)
    style = re.search(r"""\bstyle\s*=\s*(["'])(.*?)\1""", attrs, re.I | re.S)
    width = _RESIZE_WIDTH.search(style.group(2)) if style else None
    if not width:
        return match.group(0)
    size = float(width.group(1))
    unit = width.group(2).lower()
    if not (1 <= size <= (2000 if unit == "px" else 100)):
        return match.group(0)
    value = f"{size:g}{unit}"
    attribute_width = f"{size:g}" if unit == "px" else value
    img = match.group("img")
    img = re.sub(r"""\s+(?:style|width)\s*=\s*(["']).*?\1""", "", img, flags=re.I | re.S)
    img = img.rstrip()
    img = img[:-1].rstrip().removesuffix("/").rstrip()
    return img + f' width="{attribute_width}" style="width: {value}; height: auto;">'



# Only recognized, plain profile variables are protected in already-safe URL schemes.
# An opaque URL-safe placeholder prevents nh3 from discarding e.g.
# href="https://{{ domain_name }}" before Jinja can render the domain.
_LINK_VARIABLE = re.compile(r"\{\{\s*(?:first_name|last_name|title|phone|email|domain_name)\s*\}\}")
_HREF_ATTRIBUTE = re.compile(
    r"""(?P<prefix>\bhref\s*=\s*)(?P<quote>["'])(?P<value>.*?)(?P=quote)""",
    re.I | re.S,
)
_SAFE_LINK_PREFIX = re.compile(r"^(?:https?://|mailto:|tel:)", re.I)


def _mask_templated_link_urls(source: str) -> tuple[str, dict[str, str]]:
    replacements: dict[str, str] = {}

    def replace_attribute(match: re.Match[str]) -> str:
        value = match.group("value")
        # Never protect a dynamic scheme, relative URL or forbidden scheme.
        # nh3 must remain responsible for rejecting those links.
        if not _SAFE_LINK_PREFIX.match(value):
            return match.group(0)

        def replace_variable(variable: re.Match[str]) -> str:
            token = "oswlinkvar" + secrets.token_hex(16)
            replacements[token] = variable.group(0)
            return token

        masked = _LINK_VARIABLE.sub(replace_variable, value)
        if masked == value:
            return match.group(0)
        return match.group("prefix") + match.group("quote") + masked + match.group("quote")

    return _HREF_ATTRIBUTE.sub(replace_attribute, source), replacements


# Inline link styling is required: email clients do not load portal.css.
# Run this only after nh3 has sanitized the markup and style properties.
_ANCHOR_OPEN = re.compile(r"<a\b[^>]*>", re.I)
_ANCHOR_STYLE = re.compile(r'\bstyle="([^"]*)"', re.I)
_TEXT_DECORATION = re.compile(r"(?:^|;)\s*text-decoration\s*:[^;]*", re.I)


def _without_underlined_links(html_content: str) -> str:
    def strip_underline(match: re.Match[str]) -> str:
        tag = match.group(0)
        if not re.search(r"\bhref=", tag, re.I):
            return tag
        style = _ANCHOR_STYLE.search(tag)
        if style:
            previous = _TEXT_DECORATION.sub("", style.group(1)).strip().strip(";").strip()
            normalized = f"{previous}; text-decoration: none;" if previous else "text-decoration: none;"
            return tag[:style.start(1)] + normalized + tag[style.end(1):]
        # nh3 appends rel at the end. Place style before it so the next
        # sanitization pass preserves the same attribute order.
        rel = re.search(r'\\s+rel="[^"]*"', tag, re.I)
        if rel:
            return tag[:rel.start()] + ' style="text-decoration: none;"' + tag[rel.start():]
        return tag[:-1] + ' style="text-decoration: none;">'

    return _ANCHOR_OPEN.sub(strip_underline, html_content)


def sanitize_signature_html(html_content: str) -> str:
    """Strip unsafe markup while preserving common email-signature layout markup."""
    if not isinstance(html_content, str):
        raise TypeError("html_content must be a string")
    source, placeholders = _mask_templated_link_urls(
        _RESIZED_FIGURE.sub(_flatten_resized_figure, html_content)
    )
    sanitized = _CLEANER.clean(source)
    for placeholder, variable in placeholders.items():
        sanitized = sanitized.replace(placeholder, variable)
    return _without_underlined_links(sanitized)
