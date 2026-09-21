from __future__ import annotations

from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria

from app.core.exceptions import TenantContextMissingError, TenantMismatchError
from app.models.base import TenantScoped
from app.tenancy.context import CROSS_TENANT_OPTION, SESSION_TENANT_KEY

_registered = False


def _touches_tenant_scoped(state: ORMExecuteState) -> bool:
    try:
        mappers = state.all_mappers
    except Exception:
        # Fail closed: a statement we cannot inspect is treated as tenant-scoped, so it
        # needs a bound tenant or an explicit cross_tenant opt-out.
        return True
    return any(issubclass(mapper.class_, TenantScoped) for mapper in mappers)


def _apply_tenant_filter(state: ORMExecuteState) -> None:
    """Scope ORM SELECT and bulk UPDATE/DELETE to the session's tenant.

    `with_loader_criteria` also lands in the WHERE of ORM-enabled `update()`/`delete()`,
    so a bulk write can no longer touch another tenant's rows. Core/`text()` statements
    carry no mappers and are not filtered: keep tenant SQL in repositories, on the ORM.
    """
    if state.is_column_load or state.is_relationship_load:
        return
    if not (state.is_select or state.is_update or state.is_delete):
        return
    if state.execution_options.get(CROSS_TENANT_OPTION):
        return
    tenant_id = state.session.info.get(SESSION_TENANT_KEY)
    if tenant_id is None:
        if _touches_tenant_scoped(state):
            raise TenantContextMissingError()
        return
    state.statement = state.statement.options(
        with_loader_criteria(
            TenantScoped,
            lambda cls: cls.tenant_id == tenant_id,
            include_aliases=True,
        )
    )


def _stamp_tenant_on_flush(session: Session, flush_context: Any, instances: Any) -> None:
    del flush_context, instances
    tenant_id = session.info.get(SESSION_TENANT_KEY)
    cross_tenant = bool(session.info.get(CROSS_TENANT_OPTION))
    for obj in session.new:
        if not isinstance(obj, TenantScoped):
            continue
        current = getattr(obj, "tenant_id", None)
        if current is None:
            if tenant_id is None:
                raise TenantContextMissingError()
            obj.tenant_id = tenant_id
        elif tenant_id is not None and current != tenant_id and not cross_tenant:
            raise TenantMismatchError()
    for obj in session.dirty:
        if (
            isinstance(obj, TenantScoped)
            and tenant_id is not None
            and not cross_tenant
            and getattr(obj, "tenant_id", None) != tenant_id
        ):
            raise TenantMismatchError()


def register_tenant_filter() -> None:
    """Idempotent: attach the listeners to the sync `Session` class (AsyncSession delegates)."""
    global _registered
    if _registered:
        return
    event.listen(Session, "do_orm_execute", _apply_tenant_filter)
    event.listen(Session, "before_flush", _stamp_tenant_on_flush)
    _registered = True
