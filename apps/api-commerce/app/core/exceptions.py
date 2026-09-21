from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """Business error. The API layer maps it to HTTP; services never import HTTP."""

    status_code: int = 400
    error_code: str = "domain_error"
    message: str = "Não foi possível concluir a operação."

    def __init__(self, message: str | None = None, **details: Any) -> None:
        self.message = message or self.message
        self.details = details
        super().__init__(self.message)


class NotFoundError(DomainError):
    status_code = 404
    error_code = "not_found"
    message = "Recurso não encontrado."


class TenantNotFoundError(NotFoundError):
    error_code = "tenant_not_found"
    message = "Loja não encontrada para este endereço."


class TenantSuspendedError(DomainError):
    status_code = 503
    error_code = "tenant_suspended"
    message = "Esta loja está temporariamente indisponível."


class TenantContextMissingError(DomainError):
    """Raised when a tenant-scoped query runs without a tenant in the session.

    This is a programming error surfaced loudly on purpose: silently returning
    rows from every tenant would be a data leak.
    """

    status_code = 500
    error_code = "tenant_context_missing"
    message = "Consulta a dados de tenant sem contexto de tenant."


class TenantMismatchError(DomainError):
    status_code = 500
    error_code = "tenant_mismatch"
    message = "Objeto pertence a outro tenant."


class ConflictError(DomainError):
    status_code = 409
    error_code = "conflict"
    message = "Conflito com o estado atual do recurso."


class InvalidTransitionError(ConflictError):
    error_code = "invalid_transition"
    message = "Transição de estado não permitida."


class ValidationError(DomainError):
    status_code = 422
    error_code = "validation_error"
    message = "Dados inválidos."


class AuthenticationError(DomainError):
    status_code = 401
    error_code = "authentication_failed"
    message = "Credenciais inválidas."


class InactiveUserError(AuthenticationError):
    error_code = "user_inactive"
    message = "Usuário desativado."


class PermissionDeniedError(DomainError):
    status_code = 403
    error_code = "permission_denied"
    message = "Permissão insuficiente para esta operação."


class FeatureDisabledError(PermissionDeniedError):
    error_code = "feature_disabled"
    message = "Este recurso não está habilitado para a loja."


class LoginRequiredError(AuthenticationError):
    error_code = "login_required"
    message = "Entre na loja para ver este conteúdo."


class AccessRequiredError(PermissionDeniedError):
    """Signed in, but this store only shows its catalog to approved customers."""

    error_code = "access_required"
    message = "Esta loja libera o catálogo para clientes aprovados. Solicite acesso."


class AccessPendingError(PermissionDeniedError):
    error_code = "access_pending"
    message = "Seu pedido de acesso está em análise pela loja."


class AccessBlockedError(PermissionDeniedError):
    error_code = "access_blocked"
    message = "Seu acesso a esta loja não está liberado."


class RateLimitedError(DomainError):
    status_code = 429
    error_code = "rate_limited"
    message = "Tentativas em excesso. Tente novamente mais tarde."


class IdempotencyKeyRequiredError(ValidationError):
    error_code = "idempotency_key_required"
    message = "Header Idempotency-Key é obrigatório nesta operação."


class IdempotencyKeyReusedError(ValidationError):
    error_code = "idempotency_key_reused"
    message = "Idempotency-Key já usado com um payload diferente."


class IdempotencyInProgressError(ConflictError):
    error_code = "idempotency_in_progress"
    message = "Requisição com este Idempotency-Key ainda está em processamento."


class ExternalServiceError(DomainError):
    status_code = 502
    error_code = "external_service_error"
    message = "Falha ao falar com um serviço externo."


class CustomerLoginUnavailableError(DomainError):
    status_code = 503
    error_code = "login_unavailable"
    message = "O login de clientes não está disponível nesta loja agora."


class InvalidLoginStateError(DomainError):
    """The Google callback came with an unknown, reused or expired state."""

    status_code = 400
    error_code = "invalid_state"
    message = "Este link de login expirou. Volte à loja e entre de novo."


class InvalidHandoffError(DomainError):
    status_code = 400
    error_code = "invalid_handoff"
    message = "Login expirado ou já usado. Entre de novo."


class CsrfOriginError(PermissionDeniedError):
    error_code = "csrf_origin"
    message = "Requisição recusada: origem diferente da loja."
