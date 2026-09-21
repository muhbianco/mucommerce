"""Who may see a store's catalog. Pure rules: no I/O, used by the API gate and the landing."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.exceptions import (
    AccessBlockedError,
    AccessPendingError,
    AccessRequiredError,
    LoginRequiredError,
)
from app.identity.models import AccessStatus


@dataclass(frozen=True, slots=True)
class Viewer:
    """A signed-in customer of this store: the session and their access row (None = no row)."""

    customer_id: str
    session_id: str
    access_status: str | None


def check_catalog_access(access_mode: str, viewer: Viewer | None) -> None:
    """Raise unless `viewer` may see the catalog of a store in `access_mode`.

    - `public`: anyone, signed in or not;
    - no session: 401 `login_required`;
    - blocked customers: 403 `access_blocked` in every non-public mode;
    - `login_required`: any signed-in customer;
    - `whitelist` (and any unknown mode, closed by default): approved only; pending → 403
      `access_pending`; no row or revoked → 403 `access_required` (they may ask again).
    """
    if access_mode == "public":
        return
    if viewer is None:
        raise LoginRequiredError()
    if viewer.access_status == AccessStatus.BLOCKED:
        raise AccessBlockedError()
    if access_mode == "login_required":
        return
    if viewer.access_status == AccessStatus.APPROVED:
        return
    if viewer.access_status == AccessStatus.PENDING:
        raise AccessPendingError()
    raise AccessRequiredError()
