"""Payment provider webhooks (ADR 0011 §10): `POST /webhooks/{provider}/{tenant_key}`.

Only on the public API host; the store comes from the key in the URL. The delivery is stored in
the inbox and committed before anything else happens, then processed — inline when there is no
broker (dev, tests, E2E), otherwise by a task, with the beat's sweep behind both. Answers:
200 for anything stored (new, duplicate or not ours), 401 for a bad signature (stored too, so
the store's panel can say its webhook secret is wrong), 413 above 64 KB.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import JSONResponse

from app.api.deps import DbSession, WebhookStore, read_body_limited
from app.core.config import settings
from app.core.logging import get_logger
from app.core.rate_limit import rate_limit
from app.payments import webhooks
from app.payments.models import InboxStatus
from app.payments.provider import InboundWebhook
from app.workers.payments import process_webhook

logger = get_logger(__name__)
router = APIRouter(tags=["Webhooks de pagamento"])
MAX_BODY = 64 * 1024


def _store_key(request: Request) -> str:
    return str(request.path_params.get("tenant_key", ""))


@router.post(
    "/webhooks/{provider}/{tenant_key}",
    summary="Aviso do provedor de pagamento (só dispara uma consulta ao provedor)",
    dependencies=[Depends(rate_limit("payment_webhook", 300, 60, key_fn=_store_key))],
)
async def payment_webhook(
    request: Request,
    session: DbSession,
    tenant: WebhookStore,
    provider: Annotated[str, Path(min_length=2, max_length=24, pattern=r"^[a-z]+$")],
) -> JSONResponse:
    raw = await read_body_limited(request, MAX_BODY)
    inbound = InboundWebhook(
        headers={k.lower(): v for k, v in request.headers.items()},
        raw_body=raw,
        query=dict(request.query_params),
    )
    result = await webhooks.ingest(session, tenant, provider, inbound)
    await session.commit()
    if result.status == InboxStatus.INVALID:
        return JSONResponse(
            {"status": "invalid_signature", "success": False, "message": "invalid signature"},
            status_code=401,
        )
    if result.status == InboxStatus.RECEIVED and result.inbox_id:
        await _dispatch(session, result.inbox_id)
    # `success`/`message` is the acknowledgement InfinitePay documents; the others read the code.
    return JSONResponse({"status": result.status, "success": True, "message": None})


async def _dispatch(session: DbSession, inbox_id: str) -> None:
    """Hand the stored delivery on. Failing here is not the provider's problem: the row is
    committed and the sweep processes it within a minute."""
    try:
        if settings.celery_broker_url:
            # `.delay` is a blocking broker round trip: keep it off the event loop.
            await asyncio.to_thread(process_webhook.delay, inbox_id)
        else:
            await webhooks.process(session, inbox_id)
    except Exception:
        logger.exception("Payment webhook dispatch failed", extra={"inbox_id": inbox_id})
