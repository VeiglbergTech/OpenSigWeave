"""Sandboxed rendering for user-managed email signature templates."""
from collections.abc import Mapping
from typing import Any

from jinja2 import TemplateError
from jinja2.sandbox import SandboxedEnvironment

_environment = SandboxedEnvironment(autoescape=True)
_environment.globals.clear()
# Signature values are text; templates must not disable automatic HTML escaping.
_environment.filters.pop("safe", None)


def render_signature_template(template_html: str, values: Mapping[str, Any]) -> str:
    """Render signature markup while escaping dynamic values and restricting Jinja access."""
    if not isinstance(template_html, str):
        raise TypeError("template_html must be a string")
    return _environment.from_string(template_html).render(**dict(values))
