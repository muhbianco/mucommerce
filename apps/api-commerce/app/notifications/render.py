"""Rendering an e-mail: one layout (HTML and plain text) filled with a `Message`.

The content of each e-mail is built in Python (`messages.py`) and the layout only places it, so
the store's words live in one place. Jinja runs sandboxed with autoescape: a product name with
`<b>` or a quote in it cannot break the HTML, and a template can never reach into the objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from jinja2 import FileSystemLoader, select_autoescape
from jinja2.sandbox import SandboxedEnvironment

TEMPLATES = Path(__file__).parent / "templates"
DEFAULT_BRAND_COLOR = "#111111"


@dataclass(frozen=True, slots=True)
class Line:
    label: str
    value: str


@dataclass(frozen=True, slots=True)
class Message:
    """One e-mail, before the store's name and colours are put around it."""

    template_key: str
    subject: str
    heading: str
    paragraphs: list[str] = field(default_factory=list)
    items: list[Line] = field(default_factory=list)
    code: str | None = None
    code_label: str = "Código:"
    cta_url: str | None = None
    cta_label: str = "Ver pedido"
    note: str | None = None


@dataclass(frozen=True, slots=True)
class Rendered:
    subject: str
    html: str
    text: str


_environment = SandboxedEnvironment(
    loader=FileSystemLoader(TEMPLATES),
    autoescape=select_autoescape(default_for_string=True, default=True),
    trim_blocks=False,
    lstrip_blocks=False,
)


def render(message: Message, *, store_name: str, brand_color: str | None = None) -> Rendered:
    context = {
        "store_name": store_name,
        "brand_color": _colour(brand_color),
        "heading": message.heading,
        "paragraphs": message.paragraphs,
        "items": message.items,
        "code": message.code,
        "code_label": message.code_label,
        "cta_url": message.cta_url,
        "cta_label": message.cta_label,
        "note": message.note,
    }
    html = _environment.get_template("email.html.j2").render(**context)
    text = _environment.get_template("email.txt.j2").render(**context)
    return Rendered(subject=message.subject[:200], html=html, text=text)


def _colour(value: str | None) -> str:
    """Only a plain hex colour reaches the layout (it lands inside a style attribute)."""
    plain = bool(value) and len(value or "") in (4, 7) and (value or "").startswith("#")
    if plain and all(c in "0123456789abcdefABCDEF" for c in (value or "")[1:]):
        return value or DEFAULT_BRAND_COLOR
    return DEFAULT_BRAND_COLOR
