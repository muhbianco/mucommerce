"""Os e-mails da loja saindo pela conta dela.

Antes havia um caminho só para todas as lojas — um workflow da plataforma —, e loja sem ele não
mandava e-mail nenhum: o pedido #8 da SG Pipas tem quatro e-mails gravados e nenhum entregue.

O que estes testes seguram: a escolha do transporte é por loja, a senha nunca volta para a tela,
e login recusado não vira fila de retentativa contra a conta do lojista no Google.
"""

from __future__ import annotations

import smtplib
from typing import Any, ClassVar

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.notifications.models import DeliveryStatus, NotificationDelivery
from app.notifications.resolver import account_for, transport_for
from app.notifications.smtp import SmtpAccount, SmtpTransport, build_message
from app.notifications.transport import N8nTransport, TransportError
from app.tenancy.context import bind_session_tenant
from app.tenancy.resolver import TenantResolver
from tests.shoppers import selling_store
from tests.test_catalog import member_headers

SENHA = "abcd efgh ijkl mnop"  # senha de app do Google tem esse formato
CONTA = SmtpAccount(
    host="smtp.gmail.com",
    port=587,
    username="loja@gmail.com",
    password=SENHA,
    from_name="SG Pipas",
)


def entrega(**extra: Any) -> NotificationDelivery:
    valores: dict[str, Any] = {
        "tenant_id": "t",
        "template_key": "order_paid",
        "recipient": "cliente@exemplo.test",
        "subject": "Pedido #8 confirmado",
        "body_text": "Obrigado!",
        "body_html": "<p>Obrigado!</p>",
        "status": DeliveryStatus.QUEUED,
    }
    return NotificationDelivery(**(valores | extra))


# ------------------------------------------------------------------------------ a mensagem


def test_a_mensagem_vai_em_texto_e_html_e_sai_da_conta_autenticada() -> None:
    message = build_message(CONTA, entrega())
    assert message["From"] == "SG Pipas <loja@gmail.com>"
    assert message["To"] == "cliente@exemplo.test"
    assert message["Subject"] == "Pedido #8 confirmado"
    # As duas versões: cliente que não abre HTML lê o texto, e só-HTML cai mais em spam.
    tipos = {parte.get_content_type() for parte in message.walk() if not parte.is_multipart()}
    assert tipos == {"text/plain", "text/html"}
    # Message-ID nosso: sem ele, a mesma entrega reenviada vira duas mensagens na caixa.
    assert message["Message-ID"].endswith("@gmail.com>")


def test_sem_corpo_html_vai_so_o_texto() -> None:
    message = build_message(CONTA, entrega(body_html=None))
    assert message.get_content_type() == "text/plain"


def test_o_remetente_nunca_e_diferente_da_conta() -> None:
    """O Gmail recusa um "De" que não seja a conta autenticada."""
    sem_nome = SmtpAccount("smtp.gmail.com", 587, "loja@gmail.com", SENHA, "")
    assert build_message(sem_nome, entrega())["From"] == "loja@gmail.com"


# ------------------------------------------------------------------------------- o envio


class FakeSMTP:
    """O suficiente de `smtplib` para os testes afirmarem alguma coisa."""

    instancias: ClassVar[list[FakeSMTP]] = []

    def __init__(self, host: str, port: int, timeout: int = 0, context: Any = None) -> None:
        self.host, self.port = host, port
        self.logins: list[tuple[str, str]] = []
        self.enviadas: list[Any] = []
        self.starttls_chamado = False
        self.erro: Exception | None = None
        FakeSMTP.instancias.append(self)

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def starttls(self, context: Any = None) -> None:
        self.starttls_chamado = True

    def login(self, user: str, password: str) -> None:
        if self.erro is not None:
            raise self.erro
        self.logins.append((user, password))

    def send_message(self, message: Any) -> None:
        self.enviadas.append(message)


@pytest.fixture
def smtp(monkeypatch: pytest.MonkeyPatch) -> type[FakeSMTP]:
    FakeSMTP.instancias = []
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    return FakeSMTP


async def test_envia_com_starttls_e_a_senha_de_app(smtp: type[FakeSMTP]) -> None:
    resultado = await SmtpTransport(CONTA).send(entrega())
    (servidor,) = smtp.instancias
    assert (servidor.host, servidor.port) == ("smtp.gmail.com", 587)
    assert servidor.starttls_chamado  # 587 é STARTTLS
    assert servidor.logins == [("loja@gmail.com", SENHA)]
    assert len(servidor.enviadas) == 1
    assert resultado and resultado.endswith("@gmail.com>")


async def test_porta_465_nao_faz_starttls(smtp: type[FakeSMTP]) -> None:
    """Em 465 o TLS já começa no aperto de mão; pedir STARTTLS ali quebra a conexão."""
    conta = SmtpAccount("smtp.gmail.com", 465, "loja@gmail.com", SENHA, "SG")
    await SmtpTransport(conta).send(entrega())
    (servidor,) = smtp.instancias
    assert servidor.port == 465
    assert not servidor.starttls_chamado


async def test_senha_recusada_nao_vira_fila_de_retentativa(
    smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tentar de novo não conserta senha errada, e cada tentativa conta contra a conta no Google."""

    class Recusa(FakeSMTP):
        def login(self, user: str, password: str) -> None:
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    monkeypatch.setattr(smtplib, "SMTP", Recusa)
    with pytest.raises(TransportError) as erro:
        await SmtpTransport(CONTA).send(entrega())
    assert erro.value.definitive is True
    assert "senha de app" in str(erro.value)


async def test_servidor_fora_do_ar_vale_tentar_de_novo(monkeypatch: pytest.MonkeyPatch) -> None:
    class Caido(FakeSMTP):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise TimeoutError("sem resposta")

    monkeypatch.setattr(smtplib, "SMTP", Caido)
    with pytest.raises(TransportError) as erro:
        await SmtpTransport(CONTA).send(entrega())
    assert erro.value.definitive is False


async def test_conta_incompleta_nao_tenta_enviar(smtp: type[FakeSMTP]) -> None:
    sem_senha = SmtpAccount("smtp.gmail.com", 587, "loja@gmail.com", "", "SG")
    with pytest.raises(TransportError):
        await SmtpTransport(sem_senha).send(entrega())
    assert smtp.instancias == []


# --------------------------------------------------------------------- a escolha por loja


async def test_loja_sem_smtp_cai_no_caminho_da_plataforma(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant = await selling_store(session_factory)
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        assert await account_for(session, tenant.id) is None
        padrao = N8nTransport()
        assert await transport_for(session, tenant.id, padrao) is padrao


async def test_loja_com_smtp_manda_pela_conta_dela(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant = await selling_store(session_factory)
    owner = await member_headers(client, session_factory, tenant)
    salvo = await client.put(
        f"/api/v1/admin/tenants/{tenant.id}/email",
        json={
            "enabled": True,
            "host": "smtp.gmail.com",
            "port": 587,
            "username": "loja@gmail.com",
            "from_name": "SG Pipas",
            "password": SENHA,
        },
        headers=owner,
    )
    assert salvo.status_code == 200, salvo.text
    # A senha nunca volta inteira para a tela.
    assert SENHA not in salvo.text
    assert salvo.json()["password_masked"].startswith("****")

    async with session_factory() as session:
        contexto = await TenantResolver(session).resolve_by_id(tenant.id)
        assert contexto.settings["email"]["username"] == "loja@gmail.com"
        conta = await account_for(session, tenant.id)
    assert conta is not None
    assert (conta.username, conta.password, conta.from_name) == (
        "loja@gmail.com",
        SENHA,
        "SG Pipas",
    )


async def test_ligar_o_envio_sem_senha_e_recusado(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Ligado sem senha seria uma loja que acha que manda e-mail e não manda."""
    tenant = await selling_store(session_factory)
    owner = await member_headers(client, session_factory, tenant)
    recusado = await client.put(
        f"/api/v1/admin/tenants/{tenant.id}/email",
        json={"enabled": True, "username": "loja@gmail.com"},
        headers=owner,
    )
    assert recusado.status_code == 422


async def test_desligado_nao_manda_mesmo_com_senha_guardada(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant = await selling_store(session_factory)
    owner = await member_headers(client, session_factory, tenant)
    await client.put(
        f"/api/v1/admin/tenants/{tenant.id}/email",
        json={"enabled": True, "username": "loja@gmail.com", "password": SENHA},
        headers=owner,
    )
    await client.put(
        f"/api/v1/admin/tenants/{tenant.id}/email",
        json={"enabled": False, "username": "loja@gmail.com"},
        headers=owner,
    )
    async with session_factory() as session:
        assert await account_for(session, tenant.id) is None
