"""Tarefas da montagem da vitrine.

Sem retentativa do Celery, de propósito. Uma tarefa que morre no meio deixa o rascunho em
`running`, e quem o resolve é o varredor — que também **devolve a cota**. Retentativa automática
aqui chamaria o modelo de novo sem saber se a primeira chamada chegou a responder, e cada ida
custa dinheiro.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.landing.gateway import AgentsGateway
from app.landing.generation import generate_draft
from app.landing.sweeper import sweep_stuck_drafts
from app.workers.celery_app import celery_app
from app.workers.runtime import run_async, with_session

logger = get_logger(__name__)


@celery_app.task(name="app.workers.landing.generate_landing_draft")
def generate_landing_draft_task(tenant_id: str, draft_id: str) -> str:
    return run_async(
        generate_draft(with_session, AgentsGateway(), tenant_id=tenant_id, draft_id=draft_id)
    )


@celery_app.task(name="app.workers.landing.sweep_landing_drafts")
def sweep_landing_drafts() -> int:
    """Rascunho preso em `running` vira `failed` e a loja recebe a unidade de volta.

    Sem isto, um worker morto no meio de uma geração custaria à lojista uma proposta que ela
    nunca viu — e a tela ficaria "montando…" para sempre.
    """
    stuck = run_async(with_session(sweep_stuck_drafts))
    if stuck:
        logger.warning("Stuck landing drafts released", extra={"count": stuck})
    return stuck
