"""Entregar um e-mail pelo SMTP da própria loja.

Existe porque o caminho de antes era um só para todas as lojas: um workflow do n8n da
plataforma. Loja sem ele configurado não mandava e-mail nenhum — a tela do pedido dizia "NÃO
ENVIADO (SEM E-MAIL CONFIGURADO)" e ninguém tinha como resolver do próprio painel.

Aqui a loja põe a conta dela e os e-mails saem de lá. A senha é **senha de app**, guardada
cifrada no mesmo cofre das chaves de pagamento, e nunca volta para a tela.

`smtplib` numa thread em vez de uma biblioteca assíncrona: é I/O de rede curto, roda no worker
e não no caminho do pedido, e uma dependência nova para isso não se paga. O que não pode é
bloquear o laço de eventos — e é o que `asyncio.to_thread` resolve.
"""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from app.core.logging import get_logger
from app.notifications.models import NotificationDelivery
from app.notifications.transport import TransportError

logger = get_logger(__name__)

#: O servidor pode demorar; o worker não pode ficar pendurado nele para sempre.
TIMEOUT_SECONDS = 20


@dataclass(frozen=True, slots=True)
class SmtpAccount:
    host: str
    port: int
    username: str
    password: str
    from_name: str

    @property
    def sender(self) -> str:
        """O "De" é sempre a conta autenticada: o Gmail recusa qualquer outro."""
        return formataddr((self.from_name or None, self.username))


def build_message(account: SmtpAccount, delivery: NotificationDelivery) -> EmailMessage:
    """O e-mail pronto para sair, em texto e HTML.

    As duas versões vão juntas: cliente que não abre HTML lê o texto, e mensagem só-HTML tem
    mais chance de cair em spam.
    """
    message = EmailMessage()
    message["Subject"] = delivery.subject
    message["From"] = account.sender
    message["To"] = delivery.recipient
    # `Message-ID` nosso, com o domínio de quem envia: sem ele alguns servidores inventam um,
    # e aí a mesma entrega reenviada vira duas mensagens diferentes na caixa do cliente.
    message["Message-ID"] = make_msgid(domain=account.username.rpartition("@")[2] or None)
    message.set_content(delivery.body_text or delivery.subject)
    if delivery.body_html:
        message.add_alternative(delivery.body_html, subtype="html")
    return message


def _send_blocking(account: SmtpAccount, message: EmailMessage) -> None:
    contexto = ssl.create_default_context()
    if account.port == 465:
        with smtplib.SMTP_SSL(
            account.host, account.port, timeout=TIMEOUT_SECONDS, context=contexto
        ) as server:
            server.login(account.username, account.password)
            server.send_message(message)
        return
    with smtplib.SMTP(account.host, account.port, timeout=TIMEOUT_SECONDS) as server:
        server.starttls(context=contexto)
        server.login(account.username, account.password)
        server.send_message(message)


class SmtpTransport:
    """O SMTP de uma loja. Uma instância por loja, criada na hora de enviar."""

    def __init__(self, account: SmtpAccount) -> None:
        self.account = account

    @property
    def configured(self) -> bool:
        return bool(self.account.host and self.account.username and self.account.password)

    async def send(self, delivery: NotificationDelivery) -> str | None:
        if not self.configured:
            raise TransportError("SMTP da loja incompleto", definitive=True)
        message = build_message(self.account, delivery)
        try:
            await asyncio.to_thread(_send_blocking, self.account, message)
        except smtplib.SMTPAuthenticationError as exc:
            # Senha de app errada ou revogada: tentar de novo não conserta, e cada tentativa
            # conta contra a conta no Google.
            raise TransportError(
                "Login recusado pelo servidor de e-mail. Confira a senha de app.",
                definitive=True,
            ) from exc
        except smtplib.SMTPRecipientsRefused as exc:
            raise TransportError("Endereço do cliente recusado.", definitive=True) from exc
        except smtplib.SMTPResponseException as exc:
            # 4xx é temporário (caixa cheia, limite por hora); 5xx é recusa.
            definitive = 500 <= int(exc.smtp_code) < 600
            raise TransportError(
                f"Servidor de e-mail respondeu {exc.smtp_code}", definitive=definitive
            ) from exc
        except (OSError, smtplib.SMTPException) as exc:
            raise TransportError("Servidor de e-mail não respondeu", definitive=False) from exc
        return str(message["Message-ID"])[:64]
