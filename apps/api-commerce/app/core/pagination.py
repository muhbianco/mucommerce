"""Keyset pagination: bounded pages and opaque cursors.

Lists never use OFFSET: the repository fetches `limit + 1` rows after the cursor and the
extra row only says whether there is a next page.
"""

from __future__ import annotations

import base64
import json

from pydantic import BaseModel

from app.core.exceptions import ValidationError

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 50


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None = None


def encode_cursor(**values: str) -> str:
    raw = json.dumps(values, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str, *keys: str) -> dict[str, str]:
    """Inverse of `encode_cursor`; anything else (tampered, wrong shape) is a 422."""
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except ValueError as exc:  # binascii.Error, JSONDecodeError and UnicodeDecodeError
        raise ValidationError("Cursor inválido.") from exc
    if (
        not isinstance(data, dict)
        or set(data) != set(keys)
        or not all(isinstance(v, str) and len(v) <= 64 for v in data.values())
    ):
        raise ValidationError("Cursor inválido.")
    return data
