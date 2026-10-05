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


class PackingInvalidError(ValidationError):
    """Configuração de embalagem que o motor não pode honrar (frete v2).

    `details["reason"]` diz qual regra quebrou — a tela traduz cada uma em frase do lojista.
    """

    error_code = "packing_invalid"
    message = "Configuração de embalagem inválida."


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


class IntegrationUnavailableError(DomainError):
    """Uma dependência nossa está fora do ar ou mal configurada.

    503 e não 500: o pedido de quem chamou estava certo, quem falhou foi a nossa ponta.
    A mensagem diz o que importa do outro lado — que nada foi gravado.
    """

    status_code = 503
    error_code = "integration_unavailable"
    message = "Serviço indisponível no momento. Nada foi salvo; tente de novo em alguns minutos."


class CredentialVaultUnavailableError(IntegrationUnavailableError):
    """A chave mestra das credenciais não está configurada (ou está inválida).

    Erro de plataforma, não da loja: o lojista não tem o que corrigir na tela dele.
    """

    error_code = "credential_vault_unavailable"
    message = (
        "O cofre de credenciais da plataforma está indisponível. "
        "Nada foi salvo — avise a MuhBianco."
    )


class ShippingUnavailableError(IntegrationUnavailableError):
    """A transportadora não respondeu. Nada foi comprado."""

    error_code = "shipping_unavailable"
    message = "A transportadora não respondeu agora. Nada foi comprado; tente de novo."


class ShippingRefusedError(DomainError):
    """A transportadora recusou a etiqueta por um motivo que repetir não muda.

    O motivo dela fica em `order_shipments.last_error` (o painel mostra no quadro do envio).
    """

    status_code = 422
    error_code = "shipping_refused"
    message = "A transportadora recusou a etiqueta. Nada foi comprado."


class ShippingBalanceError(DomainError):
    """Carteira do frete sem saldo: operação da loja, não defeito do pedido."""

    status_code = 422
    error_code = "shipping_insufficient_balance"
    message = (
        "Saldo insuficiente na carteira do frete. Recarregue e despache de novo — "
        "o pedido continua como estava."
    )


class ShipmentInProgressError(ConflictError):
    """Já existe um despacho em curso para este pedido (clique duplo, duas abas)."""

    error_code = "shipment_in_progress"
    message = "O despacho deste pedido já está em andamento."


class ModuleNotSelfServiceError(PermissionDeniedError):
    """Módulo que o painel não liga: ou cobra, ou é chave da plataforma."""

    error_code = "module_not_self_service"
    message = "Este módulo não é ligado por aqui."


class ModuleRequiresError(ValidationError):
    """Faltou ligar um módulo do qual este depende."""

    error_code = "module_requires"
    message = "Ligue antes os módulos de que este depende."


class ModuleInUseError(ConflictError):
    """Desligar quebraria outro módulo que está ligado."""

    error_code = "module_in_use"
    message = "Outro módulo depende deste; desligue-o primeiro."


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


# ----------------------------------------------------------------------------- cart / checkout
class ItemUnavailableError(ConflictError):
    error_code = "item_unavailable"
    message = "Este item não está à venda agora."


class OutOfStockError(ConflictError):
    error_code = "out_of_stock"
    message = "Estoque insuficiente para essa quantidade."


class LotNotOnSaleError(ConflictError):
    error_code = "lot_not_on_sale"
    message = "Este lote de ingressos não está à venda agora."


class InvalidModifiersError(ValidationError):
    error_code = "invalid_modifiers"
    message = "Escolha de adicionais inválida."


class InvalidQuantityError(ValidationError):
    error_code = "invalid_quantity"
    message = "Quantidade inválida para este item."


class CartLimitError(ConflictError):
    error_code = "cart_limit"
    message = "Limite do carrinho atingido."


class CartEmptyError(ConflictError):
    error_code = "cart_empty"
    message = "O carrinho está vazio."


class CartChangedError(ConflictError):
    """The cart (items, prices, stock or delivery) changed since the customer reviewed it."""

    error_code = "cart_changed"
    message = "O carrinho mudou desde a revisão. Confira de novo antes de finalizar."


class CartAlreadyConvertedError(ConflictError):
    error_code = "cart_already_converted"
    message = "Este carrinho já virou um pedido."


class CartProblemsError(ConflictError):
    error_code = "cart_problems"
    message = "Alguns itens do carrinho não podem ser comprados agora."


class FulfillmentInvalidError(ConflictError):
    error_code = "fulfillment_invalid"
    message = "Escolha de retirada ou entrega inválida para este pedido."


class RecipientDocumentRequiredError(ValidationError):
    """Envio por transportadora sem CPF/CNPJ de quem recebe: a etiqueta seria recusada."""

    error_code = "recipient_document_required"
    message = "Informe o CPF ou CNPJ de quem recebe: a transportadora exige para a etiqueta."


class RecipientDocumentInvalidError(ValidationError):
    error_code = "recipient_document_invalid"
    message = "CPF ou CNPJ de quem recebe inválido. Confira os números."


class WhatsappRequiredError(ValidationError):
    error_code = "whatsapp_required"
    message = "Informe seu WhatsApp: a loja usa para falar com você sobre o pedido."


class WhatsappInvalidError(ValidationError):
    error_code = "whatsapp_invalid"
    message = "WhatsApp inválido. Use DDD e número, como (11) 99999-8888."


class ConsentRequiredError(ValidationError):
    error_code = "consent_required"
    message = "Aceite os termos e a política de privacidade vigentes para finalizar."


class TooManyOpenOrdersError(ConflictError):
    error_code = "too_many_open_orders"
    message = "Você tem pedidos aguardando pagamento. Pague ou cancele antes de fazer outro."


class ReservedStockError(ConflictError):
    error_code = "reserved_stock"
    message = "Parte desse estoque está reservada para pedidos aguardando pagamento."


class CancelWindowClosedError(ConflictError):
    error_code = "cancel_window_closed"
    message = "Este pedido não pode mais ser cancelado por aqui. Fale com a loja."


# ----------------------------------------------------------------------------- payments
class OrderNotPayableError(ConflictError):
    error_code = "order_not_payable"
    message = "Este pedido não está aguardando pagamento."


class PaymentInProgressError(ConflictError):
    error_code = "payment_in_progress"
    message = "Já existe um pagamento em andamento para este pedido."


class ProviderNotEnabledError(ValidationError):
    error_code = "provider_not_enabled"
    message = "Este meio de pagamento não está disponível nesta loja."


class PaymentNotCancellableError(ConflictError):
    error_code = "payment_not_cancellable"
    message = "Este pagamento já foi concluído e não pode ser cancelado por aqui."


class PayloadTooLargeError(DomainError):
    status_code = 413
    error_code = "payload_too_large"
    message = "Corpo da requisição grande demais."


class StaleOrderError(ConflictError):
    error_code = "stale_order"
    message = "O pedido mudou enquanto a tela estava aberta. Recarregue e tente de novo."
