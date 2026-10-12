"""Conta com e-mail e senha, por loja (docs/14-pedidos-lunares.md §2).

O cadastro só vira conta depois do clique no link do e-mail. Até lá, a senha escolhida espera no
desafio (já em hash) e nada é escrito no `Customer`. É o que fecha o sequestro prévio: sem provar
que o e-mail é seu, ninguém amarra uma senha a ele. O Google associa pelo e-mail verificado, e as
duas portas levam à mesma pessoa, porque as duas provaram o mesmo e-mail.

Nenhuma resposta conta se o e-mail tem conta. O cadastro sempre diz "enviamos um link" (quem já
tem conta recebe um e-mail dizendo isso), o "esqueci a senha" também, e o login erra igual para
e-mail desconhecido e senha errada, gastando o mesmo Argon2 nos dois casos.

O token do link nunca é guardado: `HMAC(chave, propósito:id)`. A fila de e-mail guarda o corpo
com um marcador no lugar do token, e o envio põe o token na hora (`notifications/jobs.py`).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.writer import audit
from app.core.exceptions import (
    CustomerUnavailableError,
    FeatureDisabledError,
    InvalidCredentialsError,
    InvalidEmailLinkError,
    RateLimitedError,
    WeakPasswordError,
)
from app.core.ids import new_id
from app.core.rate_limit import rate_limiter
from app.core.security import check_password, hash_password
from app.customers.auth_service import (
    LOGIN_FLAG,
    Completed,
    finish_sign_in,
    mask_email,
    safe_return_to,
)
from app.customers.email_links import TOKEN_SLOT, token_matches
from app.customers.models import CustomerEmailChallenge, CustomerPassword, EmailChallengePurpose
from app.customers.repository import CustomerSessionRepository
from app.identity.models import Customer
from app.models.base import utcnow
from app.notifications.models import DeliveryStatus, NotificationDelivery
from app.notifications.render import Message, render
from app.tenancy.context import TenantContext

PROVIDER = "password"
SIGNUP_TTL = timedelta(hours=24)
RESET_TTL = timedelta(hours=1)
MIN_PASSWORD = 8
MAX_PASSWORD = 128
#: Links por e-mail por hora: acima disso a resposta é a mesma, mas nenhum e-mail sai (ninguém
#: usa a loja para encher a caixa de outra pessoa).
LINKS_PER_HOUR = 3
LOGIN_ATTEMPTS = 10
LOGIN_WINDOW_SECONDS = 15 * 60
# As senhas mais tentadas em vazamentos, em português e inglês. Não é lista de vazamento (seria
# rede no caminho do cadastro); é o mínimo para "12345678" não virar senha de ninguém.
_COMMON = frozenset(
    {
        "12345678",
        "123456789",
        "1234567890",
        "87654321",
        "11111111",
        "00000000",
        "12341234",
        "password",
        "password1",
        "senha123",
        "senha1234",
        "senhasenha",
        "qwerty123",
        "abcd1234",
        "mudar123",
        "iloveyou",
        "brasil123",
    }
)


def normalize_email(raw: str) -> str:
    return raw.strip().lower()


def check_strength(password: str, email: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise WeakPasswordError(
            f"A senha precisa de pelo menos {MIN_PASSWORD} caracteres.", reason="too_short"
        )
    if len(password) > MAX_PASSWORD:
        raise WeakPasswordError("Senha longa demais.", reason="too_long")
    lowered = password.lower()
    if lowered in _COMMON or len(set(password)) < 4:
        raise WeakPasswordError("Essa senha é fácil de adivinhar. Escolha outra.", reason="common")
    if lowered == email or lowered == email.partition("@")[0]:
        raise WeakPasswordError("A senha não pode ser o seu e-mail.", reason="is_email")


class PasswordAuthService:
    def __init__(self, session: AsyncSession, tenant: TenantContext) -> None:
        self.session = session
        self.tenant = tenant

    # ------------------------------------------------------------------ cadastro
    async def signup(
        self,
        *,
        email: str,
        name: str,
        password: str,
        return_to: str,
        terms_version: int | None,
        privacy_version: int | None,
        ip: str | None,
    ) -> None:
        self._check_store()
        email = normalize_email(email)
        check_strength(password, email)
        # O hash sai antes de saber se a conta existe: o tempo de resposta é o mesmo nos dois
        # caminhos, e o tempo não conta o que a mensagem esconde.
        password_hash = await asyncio.to_thread(hash_password, password)
        if not await self._may_send(email):
            return
        now = utcnow()
        if await self._password_of(email) is not None:
            await self._mail(self._exists_message(), email, ref=None, event_id=new_id())
            return
        challenge = CustomerEmailChallenge(
            purpose=EmailChallengePurpose.SIGNUP,
            email_normalized=email,
            full_name=name.strip()[:200] or None,
            password_hash=password_hash,
            return_to=safe_return_to(return_to),
            terms_version=str(terms_version) if terms_version else None,
            privacy_version=str(privacy_version) if privacy_version else None,
            expires_at=now + SIGNUP_TTL,
            ip=ip,
        )
        self.session.add(challenge)
        await self.session.flush()
        link = self._link("/entrar/confirmar", challenge.id)
        await self._mail(
            self._confirm_message(link), email, ref=challenge.id, event_id=challenge.id
        )
        await audit(
            self.session,
            actor="anonymous",
            action="customer.signup_requested",
            entity_type="customer_email_challenge",
            entity_id=challenge.id,
            tenant_id=self.tenant.id,
            after={"email": mask_email(email)},
            ip=ip,
        )

    async def confirm_signup(
        self,
        *,
        challenge_id: str,
        token: str,
        previous_session: str | None,
        ip: str | None,
        user_agent: str | None,
    ) -> Completed:
        self._check_store()
        now = utcnow()
        challenge = await self._consume(EmailChallengePurpose.SIGNUP, challenge_id, token, now)
        assert challenge.password_hash is not None
        customer = await self._customer_for(challenge.email_normalized, challenge.full_name, now)
        await self._set_password(customer.id, challenge.password_hash, now)
        return await finish_sign_in(
            self.session,
            self.tenant,
            customer,
            provider=PROVIDER,
            return_to=challenge.return_to,
            previous_session=previous_session,
            terms_version=challenge.terms_version,
            privacy_version=challenge.privacy_version,
            now=now,
            ip=ip,
            user_agent=user_agent,
        )

    # ------------------------------------------------------------------ login
    async def login(
        self,
        *,
        email: str,
        password: str,
        return_to: str,
        previous_session: str | None,
        ip: str | None,
        user_agent: str | None,
    ) -> Completed:
        self._check_store()
        email = normalize_email(email)
        # Por conta, além do limite por IP da rota: quem tenta senhas de uma conta de vários
        # endereços esbarra aqui.
        attempt = await rate_limiter.hit(
            f"customer_password:{self.tenant.id}:{email}", LOGIN_ATTEMPTS, LOGIN_WINDOW_SECONDS
        )
        if not attempt.allowed:
            raise RateLimitedError(retry_after_seconds=attempt.retry_after_seconds)
        found = await self._password_of(email)
        stored = found[1].password_hash if found else None
        if not await check_password(password[:MAX_PASSWORD], stored) or found is None:
            raise InvalidCredentialsError()
        customer, _ = found
        if customer.status != "active":
            raise CustomerUnavailableError()
        return await finish_sign_in(
            self.session,
            self.tenant,
            customer,
            provider=PROVIDER,
            return_to=safe_return_to(return_to),
            previous_session=previous_session,
            terms_version=None,
            privacy_version=None,
            now=utcnow(),
            ip=ip,
            user_agent=user_agent,
        )

    # ------------------------------------------------------------------ esqueci a senha
    async def request_reset(self, *, email: str, ip: str | None) -> None:
        self._check_store()
        email = normalize_email(email)
        if not await self._may_send(email) or await self._password_of(email) is None:
            return
        challenge = CustomerEmailChallenge(
            purpose=EmailChallengePurpose.RESET,
            email_normalized=email,
            return_to="/",
            expires_at=utcnow() + RESET_TTL,
            ip=ip,
        )
        self.session.add(challenge)
        await self.session.flush()
        link = self._link("/entrar/nova-senha", challenge.id)
        await self._mail(self._reset_message(link), email, ref=challenge.id, event_id=challenge.id)

    async def confirm_reset(
        self,
        *,
        challenge_id: str,
        token: str,
        password: str,
        previous_session: str | None,
        ip: str | None,
        user_agent: str | None,
    ) -> Completed:
        self._check_store()
        now = utcnow()
        pending = await self._challenge(EmailChallengePurpose.RESET, challenge_id, token, now)
        # A força da senha antes de gastar o link: senha recusada não queima o e-mail.
        check_strength(password, pending.email_normalized)
        password_hash = await asyncio.to_thread(hash_password, password)
        challenge = await self._consume(EmailChallengePurpose.RESET, challenge_id, token, now)
        found = await self._password_of(challenge.email_normalized)
        if found is None:
            raise InvalidEmailLinkError()
        customer, _ = found
        if customer.status != "active":
            raise CustomerUnavailableError()
        await self._set_password(customer.id, password_hash, now)
        # Quem pediu para trocar a senha pode estar fugindo de alguém que entrou: todas as
        # sessões desta loja caem, e só a nova fica.
        await CustomerSessionRepository(self.session).revoke_all(
            self.tenant.id, customer.id, reason="password_reset", now=now
        )
        await audit(
            self.session,
            actor=f"customer:{customer.id}",
            action="customer.password_reset",
            entity_type="customer",
            entity_id=customer.id,
            tenant_id=self.tenant.id,
            ip=ip,
            user_agent=user_agent,
        )
        return await finish_sign_in(
            self.session,
            self.tenant,
            customer,
            provider=PROVIDER,
            return_to="/",
            previous_session=None,
            terms_version=None,
            privacy_version=None,
            now=now,
            ip=ip,
            user_agent=user_agent,
        )

    # ------------------------------------------------------------------ helpers
    def _check_store(self) -> None:
        # Como no Google: loja suspensa além da carência já respondeu 503 antes de chegar aqui.
        if not self.tenant.feature(LOGIN_FLAG):
            raise FeatureDisabledError(features=[LOGIN_FLAG])

    async def _may_send(self, email: str) -> bool:
        since = utcnow() - timedelta(hours=1)
        sent = await self.session.scalar(
            select(func.count())
            .select_from(CustomerEmailChallenge)
            .where(CustomerEmailChallenge.email_normalized == email)
            .where(CustomerEmailChallenge.created_at >= since)
        )
        if int(sent or 0) >= LINKS_PER_HOUR:
            return False
        # O aviso "você já tem conta" não cria desafio: conta pelo limitador.
        hit = await rate_limiter.hit(
            f"customer_email_link:{self.tenant.id}:{email}", LINKS_PER_HOUR, 3600
        )
        return hit.allowed

    async def _password_of(self, email: str) -> tuple[Customer, CustomerPassword] | None:
        row = (
            await self.session.execute(
                select(Customer, CustomerPassword)
                .join(CustomerPassword, CustomerPassword.customer_id == Customer.id)
                .where(Customer.email_normalized == email)
            )
        ).first()
        return (row[0], row[1]) if row else None

    async def _challenge(
        self, purpose: str, challenge_id: str, token: str, now: datetime
    ) -> CustomerEmailChallenge:
        if not token_matches(purpose, challenge_id, token):
            raise InvalidEmailLinkError()
        challenge = await self.session.get(CustomerEmailChallenge, challenge_id)
        if (
            challenge is None
            or challenge.tenant_id != self.tenant.id
            or challenge.purpose != purpose
            or challenge.consumed_at is not None
            or challenge.expires_at <= now
        ):
            raise InvalidEmailLinkError()
        return challenge

    async def _consume(
        self, purpose: str, challenge_id: str, token: str, now: datetime
    ) -> CustomerEmailChallenge:
        """Confere e gasta o link de uma vez: UPDATE condicional, então dois cliques (ou dois
        workers) não abrem duas contas nem trocam a senha duas vezes."""
        challenge = await self._challenge(purpose, challenge_id, token, now)
        consumed = await self.session.execute(
            update(CustomerEmailChallenge)
            .where(CustomerEmailChallenge.id == challenge.id)
            .where(CustomerEmailChallenge.consumed_at.is_(None))
            .values(consumed_at=now, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        if consumed.rowcount != 1:  # type: ignore[attr-defined]
            raise InvalidEmailLinkError()
        return challenge

    async def _customer_for(self, email: str, name: str | None, now: datetime) -> Customer:
        """A pessoa deste e-mail (já verificado pelo clique): a que existe, ou uma nova."""
        existing = (
            await self.session.execute(select(Customer).where(Customer.email_normalized == email))
        ).scalar_one_or_none()
        if existing is not None:
            if existing.status != "active":
                raise CustomerUnavailableError()
            if existing.email_verified_at is None:
                existing.email_verified_at = now
            if existing.full_name is None and name:
                existing.full_name = name
            return existing
        try:
            async with self.session.begin_nested():
                customer = Customer(
                    email_normalized=email,
                    email_verified_at=now,
                    full_name=name,
                    status="active",
                )
                self.session.add(customer)
                await self.session.flush()
        except IntegrityError:
            # O mesmo e-mail entrando pelo Google no mesmo instante: a outra porta criou.
            again = (
                await self.session.execute(
                    select(Customer).where(Customer.email_normalized == email)
                )
            ).scalar_one()
            return again
        return customer

    async def _set_password(self, customer_id: str, password_hash: str, now: datetime) -> None:
        row = (
            await self.session.execute(
                select(CustomerPassword).where(CustomerPassword.customer_id == customer_id)
            )
        ).scalar_one_or_none()
        if row is None:
            self.session.add(
                CustomerPassword(
                    customer_id=customer_id, password_hash=password_hash, changed_at=now
                )
            )
        else:
            row.password_hash = password_hash
            row.changed_at = now
        await self.session.flush()

    def _link(self, path: str, challenge_id: str) -> str:
        # Um parâmetro só (`id.token`): o modelo de texto escapa `&`, e um link quebrado no
        # e-mail em texto puro é conta que não se confirma.
        return f"https://{self.tenant.host}{path}?k={challenge_id}.{TOKEN_SLOT}"

    async def _mail(self, message: Message, email: str, *, ref: str | None, event_id: str) -> None:
        rendered = render(
            message,
            store_name=self.tenant.name,
            brand_color=str((self.tenant.settings.get("branding") or {}).get("primary_color") or "")
            or None,
        )
        self.session.add(
            NotificationDelivery(
                event_id=event_id,
                template_key=message.template_key,
                channel="email",
                recipient=email[:320],
                secret_ref=ref,
                subject=rendered.subject,
                body_html=rendered.html,
                body_text=rendered.text,
                status=DeliveryStatus.QUEUED,
                next_attempt_at=utcnow(),
            )
        )
        await self.session.flush()

    def _confirm_message(self, link: str) -> Message:
        return Message(
            template_key="account_confirm",
            subject=f"Confirme seu e-mail na {self.tenant.name}",
            heading="Falta só confirmar o seu e-mail",
            paragraphs=[
                f"Recebemos um pedido para criar uma conta na {self.tenant.name} com este e-mail.",
                "Confirme pelo botão abaixo e você já entra na loja. O link vale por 24 horas.",
            ],
            cta_url=link,
            cta_label="Confirmar e entrar",
            note="Se não foi você, ignore este e-mail: nenhuma conta é criada sem a confirmação.",
        )

    def _exists_message(self) -> Message:
        return Message(
            template_key="account_exists",
            subject=f"Você já tem conta na {self.tenant.name}",
            heading="Este e-mail já tem conta",
            paragraphs=[
                "Alguém tentou criar uma conta com este e-mail, mas ele já está cadastrado.",
                "Se foi você, entre com a sua senha. Se esqueceu, troque por uma nova.",
            ],
            cta_url=f"https://{self.tenant.host}/entrar/esqueci",
            cta_label="Trocar a senha",
            note="Se não foi você, ignore este e-mail: nada mudou na sua conta.",
        )

    def _reset_message(self, link: str) -> Message:
        return Message(
            template_key="password_reset",
            subject=f"Trocar a senha na {self.tenant.name}",
            heading="Escolha uma senha nova",
            paragraphs=[
                "Recebemos um pedido para trocar a senha da sua conta. O link vale por 1 hora.",
                "Ao trocar, as sessões abertas em outros aparelhos são encerradas.",
            ],
            cta_url=link,
            cta_label="Escolher nova senha",
            note="Se não foi você, ignore este e-mail: a senha atual continua valendo.",
        )
