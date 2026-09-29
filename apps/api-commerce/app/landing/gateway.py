"""A única porta desta API para um modelo de linguagem.

**Nada mais no commerce fala com modelo.** Há uma regra em `test_architecture.py` que cobra
isso, e ela existe por dois motivos concretos: a chave mora do outro lado (api-agents), e o
livro-caixa só fecha se toda chamada passar pelo mesmo lugar. Um segundo caminho, mesmo
"temporário", significaria uma segunda cópia da chave na VPS e uma fatura que ninguém sabe
explicar.

O que este módulo **não** faz, e é de propósito:

- **não escolhe modelo.** O pedido diz para quê (`feature`); qual modelo atende é decisão da
  api-agents. Se o commerce pudesse escolher, um laço mal escrito daqui pegaria o caro e a conta
  triplicaria sem ninguém notar até o fim do mês;
- **não valida a saída.** Devolve o texto como veio. Quem transforma isso em blocos é o
  `generation`, que passa por `validate_setting` e por `check_setting_references` — confiar no
  `response_json_schema` seria confiar num pedido, não numa garantia.

`FakeGateway` existe porque o teste que importa não é "o modelo responde", é "o que fazemos com
o que ele responde": vazio, JSON quebrado, id inventado, lista comprida demais.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.core.config import settings
from app.core.exceptions import DomainError
from app.core.logging import get_logger

logger = get_logger(__name__)

COMPLETE_PATH = "/api/v1/internal/commerce/llm/complete"

#: As features que este serviço usa. Os nomes são o contrato com a api-agents, que resolve
#: modelo, teto e cota por eles — mudar um aqui sem mudar lá é uma chamada recusada.
FEATURE_DRAFT = "landing_draft"
FEATURE_REFINE = "landing_refine"


class LlmUnavailableError(DomainError):
    """Não deu para falar com o modelo. Erro nosso, não da loja."""

    status_code = 503
    error_code = "llm_unavailable"
    message = "A montagem com IA não está disponível agora."


@dataclass(frozen=True, slots=True)
class LlmReply:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    call_id: str


class LlmGateway(Protocol):
    """Uma ida ao modelo. `messages` é a lista `{role, content}` do formato de conversa."""

    async def complete(
        self,
        *,
        feature: str,
        messages: Sequence[dict[str, str]],
        response_json_schema: dict[str, Any] | None = None,
        tenant_ref: str | None = None,
        external_ref: str | None = None,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LlmReply: ...


class AgentsGateway:
    """Fala com a api-agents pela rede interna.

    O timeout é generoso (dezenas de segundos) porque quem espera é o worker, não a tela — mas
    existe: sem ele, uma api-agents pendurada seguraria um processo do worker até alguém
    reiniciar.

    **Sem retentativa aqui.** Uma geração custa dinheiro e a mesma chamada repetida pode entregar
    duas respostas boas pelo preço de duas. Quem decide tentar de novo é o `generation`, que sabe
    se a primeira resposta serviu.
    """

    def __init__(self, base_url: str | None = None, token: str | None = None) -> None:
        self._base_url = (base_url or settings.muhbianco_accounts_internal_url).rstrip("/")
        self._token = (
            token if token is not None else settings.internal_token_agents.get_secret_value()
        )

    async def complete(
        self,
        *,
        feature: str,
        messages: Sequence[dict[str, str]],
        response_json_schema: dict[str, Any] | None = None,
        tenant_ref: str | None = None,
        external_ref: str | None = None,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LlmReply:
        if not self._token:
            raise LlmUnavailableError()
        body: dict[str, Any] = {"feature": feature, "messages": list(messages)}
        for key, value in (
            ("response_json_schema", response_json_schema),
            ("tenant_ref", tenant_ref),
            ("external_ref", external_ref),
            ("max_output_tokens", max_output_tokens),
            ("temperature", temperature),
        ):
            if value is not None:
                body[key] = value

        try:
            async with httpx.AsyncClient(timeout=settings.landing_llm_timeout_seconds) as client:
                response = await client.post(
                    self._base_url + COMPLETE_PATH,
                    json=body,
                    headers={"X-Internal-Token": self._token},
                )
        except httpx.HTTPError as exc:
            logger.warning("api-agents unreachable (llm)", extra={"error": type(exc).__name__})
            raise LlmUnavailableError() from exc

        if response.status_code == 404:
            # O gateway ainda não foi implantado: o deploy é em dois repos, e esta é a janela
            # entre eles. Dizer isso no motivo da falha poupa uma caçada.
            logger.warning("llm gateway not deployed", extra={"url": self._base_url})
            raise LlmUnavailableError("O serviço de montagem ainda não está no ar.")
        if response.status_code != 200:
            logger.warning("llm gateway refused", extra={"status": response.status_code})
            raise LlmUnavailableError()

        try:
            data = response.json()
            return LlmReply(
                text=str(data["text"]),
                model=str(data["model"]),
                prompt_tokens=int(data["prompt_tokens"]),
                completion_tokens=int(data["completion_tokens"]),
                latency_ms=int(data["latency_ms"]),
                call_id=str(data["call_id"]),
            )
        except (ValueError, KeyError, TypeError) as exc:
            logger.warning("llm gateway answered in an unexpected shape")
            raise LlmUnavailableError() from exc


@dataclass
class FakeGateway:
    """Respostas combinadas, para os testes e para o desenvolvimento sem chave.

    `replies` é consumido em ordem; acabando, a última se repete. Cada item é o texto cru, como
    o modelo devolveria — inclusive quebrado, que é metade do que interessa testar.
    """

    replies: list[str | Exception] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    model: str = "fake-model"
    on_call: Callable[[dict[str, Any]], None] | None = None

    async def complete(
        self,
        *,
        feature: str,
        messages: Sequence[dict[str, str]],
        response_json_schema: dict[str, Any] | None = None,
        tenant_ref: str | None = None,
        external_ref: str | None = None,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LlmReply:
        call = {
            "feature": feature,
            "messages": list(messages),
            "response_json_schema": response_json_schema,
            "tenant_ref": tenant_ref,
            "external_ref": external_ref,
            "max_output_tokens": max_output_tokens,
            "temperature": temperature,
        }
        self.calls.append(call)
        # Gancho para o teste que confirma que nenhuma transação está aberta durante a chamada:
        # ele consulta o banco por outra conexão exatamente aqui.
        if self.on_call is not None:
            self.on_call(call)

        if not self.replies:
            raise LlmUnavailableError()
        reply = self.replies[0] if len(self.replies) == 1 else self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return LlmReply(
            text=reply,
            model=self.model,
            prompt_tokens=100,
            completion_tokens=200,
            latency_ms=1234,
            call_id=f"fake-{len(self.calls)}",
        )
