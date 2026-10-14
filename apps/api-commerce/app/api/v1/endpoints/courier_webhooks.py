"""Webhook da Uber Direct da loja: `POST /webhooks/courier/uber/{tenant_key}` (docs/15, F3).

Cada loja cadastra este endereço no painel da Uber Direct dela (Developer → Webhooks) e cola aqui a
"Webhook Signing Key" que a Uber mostra. Só no host público da API; a loja vem da chave na URL,
nunca do corpo.

O aviso não decide nada: assinatura conferida, ele só **antecipa** a consulta da corrida, e quem
muda corrida e pedido é o `GET` do varredor. Respostas: 200 para tudo que foi aceito (inclusive
corrida que não é nossa — repetir não ajudaria), 401 para assinatura errada ou chave não
cadastrada (a Uber não repete 4xx, e a consulta a cada 2 min cobre), 413 acima de 64 KB.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.deps import DbSession, WebhookStore, read_body_limited
from app.core.logging import get_logger
from app.core.rate_limit import rate_limit
from app.courier import uber
from app.courier.service import CourierService
from app.tenancy.context import bind_session_tenant
from app.tenancy.service import Actor

logger = get_logger(__name__)
router = APIRouter(tags=["Webhooks de entrega"])
MAX_BODY = 64 * 1024


def _store_key(request: Request) -> str:
    return str(request.path_params.get("tenant_key", ""))


def _delivery_id(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    return str(payload.get("delivery_id") or (data or {}).get("id") or "").strip()[:64]


@router.post(
    "/webhooks/courier/uber/{tenant_key}",
    summary="Aviso da Uber Direct (só antecipa a consulta da corrida)",
    dependencies=[Depends(rate_limit("courier_webhook", 300, 60, key_fn=_store_key))],
)
async def uber_webhook(request: Request, session: DbSession, tenant: WebhookStore) -> JSONResponse:
    raw = await read_body_limited(request, MAX_BODY)
    bind_session_tenant(session, tenant.id)
    segredo = await uber.load_webhook_secret(session, tenant.id)
    assinatura = request.headers.get("x-uber-signature") or request.headers.get(
        "x-postmates-signature"
    )
    if segredo is None or not uber.signature_ok(segredo, raw, assinatura):
        logger.warning(
            "courier: webhook da Uber recusado",
            extra={"tenant_id": tenant.id, "chave_cadastrada": segredo is not None},
        )
        return JSONResponse({"status": "invalid_signature"}, status_code=401)
    try:
        payload: Any = json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JSONResponse({"status": "ignored"})
    delivery_id = _delivery_id(payload)
    if not delivery_id:
        return JSONResponse({"status": "ignored"})
    resultado = await CourierService(session, tenant.id, Actor.system("uber_webhook")).webhook_hint(
        delivery_id
    )
    await session.commit()
    logger.info(
        "courier: webhook da Uber",
        extra={
            "tenant_id": tenant.id,
            "kind": str((payload or {}).get("kind") or "")[:40],
            "status": str((payload or {}).get("status") or "")[:32],
            "resultado": resultado,
        },
    )
    return JSONResponse({"status": resultado})
