"""Allowlist-based HTML sanitization for untrusted signature templates."""
import nh3


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


def sanitize_signature_html(html_content: str) -> str:
    """Strip unsafe markup while preserving common email-signature layout markup."""
    if not isinstance(html_content, str):
        raise TypeError("html_content must be a string")
    return _CLEANER.clean(html_content)
