"""CPF/CNPJ de quem recebe: a transportadora recusa a etiqueta sem ele.

Caso real: o pedido #9 da loja de teste travou no despacho com "CNPJ ou CPF do destinatário é
obrigatório" (422 do Melhor Envio no /me/cart), e o painel dizia "não respondeu, tente de novo".
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.br_document import mask_document, normalize_document
from app.core.exceptions import ShippingRefusedError, ValidationError
from app.customers.address_models import CustomerAddress
from app.models.base import utcnow
from app.orders.models import Order
from app.shipping.dispatch import ShipmentService
from app.shipping.models import OrderShipment, ShipmentStatus
from app.shipping.provider import ShippingProviderError
from app.shipping.providers import fake as fake_shipping
from app.shipping.providers.melhorenvio import refusal_reason
from app.tenancy.context import bind_session_tenant
from app.tenancy.resolver import TenantResolver
from app.tenancy.service import Actor
from tests.test_checkout_place import order_body, place
from tests.test_shipping_flow import (
    CART,
    ENDERECO,
    _aceitar,
    carrinho_com_frete,
    fake_provider,  # noqa: F401  (autouse: liga o provedor falso)
    loja,
    pedido_pronto_para_despacho,
)

SEM_DOCUMENTO = {k: v for k, v in ENDERECO.items() if k != "document"}


# --------------------------------------------------------------------------- documento


@pytest.mark.parametrize(
    ("raw", "digits"),
    [("123.456.789-09", "12345678909"), ("11.222.333/0001-81", "11222333000181")],
)
def test_documento_valido(raw: str, digits: str) -> None:
    assert normalize_document(raw) == digits


@pytest.mark.parametrize("raw", ["123.456.789-00", "111.111.111-11", "11.222.333/0001-80", "12"])
def test_documento_invalido(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_document(raw)


def test_mascara() -> None:
    assert mask_document("12345678909") == "***.456.789-**"
    assert mask_document("11222333000181") == "**.222.333/0001-**"


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (
            '{"error":"CNPJ ou CPF do destinat\\u00e1rio \\u00e9 obrigat\\u00f3rio"}',
            "CNPJ ou CPF do destinatário é obrigatório",
        ),
        (
            '{"message":"Dados inválidos","errors":{"to.postal_code":["CEP inválido"]}}',
            "Dados inválidos CEP inválido",
        ),
        ("<html>502</html>", None),
    ],
)
def test_motivo_da_recusa(body: str, reason: str | None) -> None:
    assert refusal_reason(body) == reason


# --------------------------------------------------------------------------- checkout


async def _carrinho_por_transportadora(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    endereco: dict[str, Any],
) -> tuple[Any, dict[str, str], dict[str, Any], str]:
    tenant, owner, me = await loja(client, session_factory)
    _, criado = await carrinho_com_frete(client, session_factory, tenant, owner, me)
    # O carrinho_com_frete usa o ENDERECO com documento; troca pelo endereço do teste.
    await client.delete(f"/api/v1/me/addresses/{criado['id']}", headers=me)
    novo = (await client.post("/api/v1/me/addresses", json=endereco, headers=me)).json()
    opcoes = (
        await client.post(f"{CART}/shipping/options", json={"address_id": novo["id"]}, headers=me)
    ).json()
    carrinho = (
        await client.put(
            f"{CART}/fulfillment",
            json={"type": "shipping", "address_id": novo["id"], "shipping": opcoes["options"][0]},
            headers=me,
        )
    ).json()
    return tenant, me, carrinho, novo["id"]


async def test_checkout_por_transportadora_pede_o_documento(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, me, carrinho, address_id = await _carrinho_por_transportadora(
        client, session_factory, SEM_DOCUMENTO
    )
    opcao = next(a for a in carrinho["options"]["addresses"] if a["id"] == address_id)
    assert opcao["has_document"] is False

    sem = await place(client, me, order_body(carrinho))
    assert sem.status_code == 422, sem.text
    assert sem.json()["error"]["code"] == "recipient_document_required"

    errado = await place(client, me, order_body(carrinho, recipient_document="123.456.789-00"))
    assert errado.status_code == 422
    assert errado.json()["error"]["code"] == "recipient_document_invalid"

    feito = await place(client, me, order_body(carrinho, recipient_document="123.456.789-09"))
    assert feito.status_code == 201, feito.text
    # A leitura do pedido nunca devolve o número inteiro.
    endereco = feito.json()["fulfillment"]["address"]
    assert "document" not in endereco
    assert endereco["document_masked"] == "***.456.789-**"

    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        salvo = await session.get(CustomerAddress, address_id)
        pedido = await session.get(Order, feito.json()["id"])
        assert salvo is not None and pedido is not None
        assert salvo.document == "12345678909"  # a próxima compra não pede de novo
        assert (pedido.fulfillment or {})["address"]["document"] == "12345678909"


async def test_endereco_guarda_documento_mascarado(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    _, _, me = await loja(client, session_factory)
    invalido = await client.post(
        "/api/v1/me/addresses", json=ENDERECO | {"document": "123"}, headers=me
    )
    assert invalido.status_code == 422
    criado = await client.post("/api/v1/me/addresses", json=ENDERECO, headers=me)
    assert criado.status_code == 201, criado.text
    assert criado.json()["document_masked"] == "***.456.789-**"
    assert "document" not in criado.json()


# --------------------------------------------------------------------------- despacho


async def _tirar_documento(
    session_factory: async_sessionmaker[AsyncSession], tenant: Any, order_id: str
) -> None:
    """Pedido de antes desta mudança (como o #9): o endereço do pedido não tem documento."""
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        pedido = await session.get(Order, order_id)
        assert pedido is not None
        endereco = {
            k: v for k, v in (pedido.fulfillment or {})["address"].items() if k != "document"
        }
        pedido.fulfillment = {**(pedido.fulfillment or {}), "address": endereco}
        await session.commit()


async def _despachar(
    session_factory: async_sessionmaker[AsyncSession],
    tenant: Any,
    order_id: str,
    documento: str | None = None,
) -> OrderShipment:
    async with session_factory() as session:
        contexto = await TenantResolver(session).resolve_by_id(tenant.id)
        bind_session_tenant(session, tenant.id)
        pedido = await session.get(Order, order_id)
        assert pedido is not None
        service = ShipmentService(session, contexto, Actor.system("teste"), utcnow())
        try:
            remessa = await service.dispatch(
                pedido, scopes=frozenset({"orders:transition"}), recipient_document=documento
            )
        except Exception:
            await session.rollback()
            raise
        await session.commit()
        return remessa


async def test_pedido_sem_documento_recebe_no_despacho(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner, me = await loja(client, session_factory)
    order_id = await pedido_pronto_para_despacho(client, session_factory, tenant, owner, me)
    await _tirar_documento(session_factory, tenant, order_id)

    async with session_factory() as session:
        contexto = await TenantResolver(session).resolve_by_id(tenant.id)
        bind_session_tenant(session, tenant.id)
        pedido = await session.get(Order, order_id)
        assert pedido is not None
        previa = await ShipmentService(session, contexto, Actor.system("teste"), utcnow()).preview(
            pedido
        )
    assert previa.recipient_document_missing is True

    with pytest.raises(ValidationError) as falta:
        await _despachar(session_factory, tenant, order_id)
    assert falta.value.details["reason"] == "recipient_document"
    assert fake_shipping.SHIP_REQUESTS == []  # nem chegou na transportadora

    with pytest.raises(ValidationError) as errado:
        await _despachar(session_factory, tenant, order_id, "123.456.789-00")
    assert errado.value.details["reason"] == "recipient_document_invalid"

    remessa = await _despachar(session_factory, tenant, order_id, "11.222.333/0001-81")
    assert remessa.status == ShipmentStatus.PURCHASED
    assert fake_shipping.SHIP_REQUESTS[-1].recipient.document == "11222333000181"
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        pedido = await session.get(Order, order_id)
        assert pedido is not None
        assert (pedido.fulfillment or {})["address"]["document"] == "11222333000181"


async def test_recusa_da_transportadora_fica_gravada_e_nao_pede_para_repetir(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant, owner, me = await loja(client, session_factory)
    order_id = await pedido_pronto_para_despacho(client, session_factory, tenant, owner, me)

    async def recusa(self: Any, credentials: Any, request: Any) -> Any:
        raise ShippingProviderError(
            '{"error":"CEP de destino não atendido"}',
            http_status=422,
            definitive=True,
            reason="CEP de destino não atendido",
        )

    monkeypatch.setattr(fake_shipping.FakeShippingProvider, "ship", recusa)
    with pytest.raises(ShippingRefusedError) as exc:
        await _despachar(session_factory, tenant, order_id)
    assert exc.value.error_code == "shipping_refused"

    # Antes, o rollback do pedido apagava a remessa e o motivo: o painel não tinha o que mostrar.
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        remessa = (
            await session.execute(select(OrderShipment).where(OrderShipment.order_id == order_id))
        ).scalar_one()
        assert remessa.status == ShipmentStatus.FAILED
        assert remessa.last_error == "A transportadora recusou: CEP de destino não atendido"


async def test_reaceitar_nao_e_preciso_para_novo_despacho(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Depois da recusa, despachar de novo com o documento usa a mesma remessa."""
    tenant, owner, me = await loja(client, session_factory)
    order_id = await pedido_pronto_para_despacho(client, session_factory, tenant, owner, me)
    await _tirar_documento(session_factory, tenant, order_id)
    # Pedido #9: sem documento, a transportadora recusava; agora nem chega lá.
    with pytest.raises(ValidationError):
        await _despachar(session_factory, tenant, order_id)
    await _aceitar(session_factory, tenant, order_id)  # continua aceito
    remessa = await _despachar(session_factory, tenant, order_id, "123.456.789-09")
    assert remessa.status == ShipmentStatus.PURCHASED
