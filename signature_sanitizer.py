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



# CKEditor can leave an image's original intrinsic dimensions (for example,
# 1920x2560) in its HTML width/height attributes while recording the intended
# displayed size (for example, 245px) in inline CSS. Classic Outlook prioritizes
# HTML dimensions: the resulting embedded CID image appears enormous.
_IMG_TAG = re.compile(r"<img\b[^>]*>", re.I | re.S)
_IMG_STYLE = re.compile(r"""(\sstyle\s*=\s*)(["'])(.*?)\2""", re.I | re.S)
_IMG_WIDTH = re.compile(r"""(\swidth\s*=\s*)(["'])(\d+)\2""", re.I)
_IMG_HEIGHT = re.compile(r"""(\sheight\s*=\s*)(["'])(\d+)\2""", re.I)
_CSS_PIXEL_WIDTH = re.compile(r"(?:^|;)\s*width\s*:\s*(\d+(?:\.\d+)?)px(?=\s|;|$)", re.I)
_CSS_PIXEL_HEIGHT = re.compile(r"(?:^|;)\s*height\s*:\s*(\d+(?:\.\d+)?)px(?=\s|;|$)", re.I)
_CSS_AUTO_HEIGHT = re.compile(r"(?<=;)\s*height\s*:\s*auto(?=\s|;|$)", re.I)
_CSS_ASPECT = re.compile(r"(?:^|;)\s*aspect-ratio\s*:\s*(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)(?=\s|;|$)", re.I)


def _normalize_img_pixel_dimensions(match: re.Match) -> str:
    """Keep numeric img dimensions consistent with CKEditor's explicit px size."""
    tag = match.group(0)
    style_match = _IMG_STYLE.search(tag)
    if not style_match:
        return tag
    css = style_match.group(3)
    width_match = _CSS_PIXEL_WIDTH.search(css)
    if not width_match:
        return tag  # Do not guess dimensions for percentages or max-width.
    width_value = float(width_match.group(1))
    if not 1 <= width_value <= 2000:
        return tag
    display_width = int(width_value + 0.5)

    css_height = _CSS_PIXEL_HEIGHT.search(css)
    height = None
    if css_height:
        value = float(css_height.group(1))
        if 1 <= value <= 8192:
            height = int(value + 0.5)
    else:
        # Prefer CKEditor's original aspect-ratio, then intrinsic dimensions.
        aspect = _CSS_ASPECT.search(css)
        if aspect:
            ratio_width, ratio_height = map(float, aspect.groups())
        else:
            old_w, old_h = _IMG_WIDTH.search(tag), _IMG_HEIGHT.search(tag)
            ratio_width = float(old_w.group(3)) if old_w else 0
            ratio_height = float(old_h.group(3)) if old_h else 0
        if ratio_width > 0 and ratio_height > 0:
            candidate = int(display_width * ratio_height / ratio_width + 0.5)
            if 1 <= candidate <= 8192:
                height = candidate

    # Retain the original CSS width and other allowed styles. Set a fixed height
    # when known instead of relying on Outlook's inconsistent height:auto.
    if height is not None and not css_height:
        if _CSS_AUTO_HEIGHT.search(css):
            css = _CSS_AUTO_HEIGHT.sub(f"; height: {height}px", css, count=1)
        else:
            css = css.rstrip().rstrip(";") + f"; height: {height}px;"
    elif height is None:
        # An obsolete intrinsic height attribute would override CSS width in
        # Outlook, so remove it if the aspect ratio cannot be verified.
        pass

    tag = _IMG_STYLE.sub(
        lambda m: m.group(1) + m.group(2) + css + m.group(2),
        tag, count=1
    )
    tag = _IMG_WIDTH.sub("", tag, count=1)
    tag = _IMG_HEIGHT.sub("", tag, count=1)
    attr = f' width="{display_width}"'
    if height is not None:
        attr += f' height="{height}"'
    before_end = tag[:-2] if tag.endswith("/>") else tag[:-1]
    close = "/>" if tag.endswith("/>") else ">"
    return before_end.rstrip() + attr + close


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
        rel = re.search(r'\s+rel="[^"]*"', tag, re.I)
        if rel:
            return tag[:rel.start()] + ' style="text-decoration: none;"' + tag[rel.start():]
        return tag[:-1] + ' style="text-decoration: none;">'

    return _ANCHOR_OPEN.sub(strip_underline, html_content)


def sanitize_signature_html(html_content: str) -> str:
    """Strip unsafe markup while preserving common email-signature layout markup."""
    if not isinstance(html_content, str):
        raise TypeError("html_content must be a string")
    normalized = _RESIZED_FIGURE.sub(_flatten_resized_figure, html_content)
    normalized = _IMG_TAG.sub(_normalize_img_pixel_dimensions, normalized)
    source, placeholders = _mask_templated_link_urls(normalized)
    sanitized = _CLEANER.clean(source)
    for placeholder, variable in placeholders.items():
        sanitized = sanitized.replace(placeholder, variable)
    return _without_underlined_links(sanitized)
