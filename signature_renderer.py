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


import re

_GROUP_PLACEHOLDER = re.compile(r"{{\s*group:([a-z0-9-]{1,64})\s*}}")


def prepare_group_placeholders(template_html: str) -> tuple[str, dict[str, str]]:
    """Replace group tags with safe Jinja identifiers and return slug-to-key mapping."""
    slots: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        slug = match.group(1)
        key = slots.setdefault(slug, f"group_slot_{len(slots)}")
        return "{{ " + key + " }}"

    return _GROUP_PLACEHOLDER.sub(replace, template_html), slots


def strip_group_placeholders(template_html: str) -> str:
    """Remove group tags from member templates to enforce one-level expansion."""
    return _GROUP_PLACEHOLDER.sub("", template_html)
