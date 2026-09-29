"""A ida à api-agents, e o que volta quando ela recusa.

O que se cobra aqui é o caminho do **motivo**. Em 29/09/2026 a montagem de vitrine passou um dia
fora do ar: o Gemini dizia exatamente o que estava errado, a api-agents registrava, e daqui para
frente virava um 502 mudo e a frase "A montagem com IA não está disponível agora" no histórico da
loja. Foram dois repositórios e dois `docker service logs` para recuperar uma informação que o
sistema já tinha na mão.

A lojista não precisa entender `INVALID_ARGUMENT`. Precisa poder ler o código para alguém que
entenda — e é isso que estes testes garantem que chega até ela.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from app.landing.gateway import COMPLETE_PATH, AgentsGateway, LlmUnavailableError

BASE = "http://agents.test"
URL = BASE + COMPLETE_PATH

PERGUNTA: list[dict[str, str]] = [{"role": "user", "content": "monte"}]


def _gateway() -> AgentsGateway:
    return AgentsGateway(base_url=BASE, token="token-de-teste")


async def _pedir(gateway: AgentsGateway) -> Any:
    return await gateway.complete(feature="landing_draft", messages=PERGUNTA)


class TestQuandoDaCerto:
    async def test_a_resposta_vira_reply(self) -> None:
        with respx.mock(assert_all_called=True) as router:
            router.post(URL).mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "text": '{"blocks": []}',
                        "model": "gemini-3.5-flash",
                        "prompt_tokens": 100,
                        "completion_tokens": 20,
                        "latency_ms": 900,
                        "call_id": "abc",
                    },
                )
            )
            reply = await _pedir(_gateway())
        assert reply.text == '{"blocks": []}'
        assert reply.model == "gemini-3.5-flash"
        assert reply.call_id == "abc"

    async def test_o_token_interno_vai_no_cabecalho(self) -> None:
        with respx.mock(assert_all_called=True) as router:
            rota = router.post(URL).mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "text": "{}",
                        "model": "m",
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "latency_ms": 1,
                        "call_id": "c",
                    },
                )
            )
            await _pedir(_gateway())
        assert rota.calls.last.request.headers["X-Internal-Token"] == "token-de-teste"


class TestOMotivoChega:
    async def test_o_codigo_do_provedor_e_o_rastro_vem_na_mensagem(self) -> None:
        """O caso real: 502 porque o Gemini recusou o esquema com `INVALID_ARGUMENT`."""
        with respx.mock(assert_all_called=True) as router:
            router.post(URL).mock(
                return_value=httpx.Response(
                    502,
                    json={
                        "error": "llm_error",
                        "message": "O modelo recusou a requisição.",
                        "details": {
                            "provider": "gemini",
                            "provider_status": 400,
                            "provider_reason": "INVALID_ARGUMENT",
                        },
                        "request_id": "7a1945f3-50f6-4d70-98a9-3b86415d0976",
                    },
                )
            )
            with pytest.raises(LlmUnavailableError) as capturado:
                await _pedir(_gateway())

        mensagem = capturado.value.message
        assert "INVALID_ARGUMENT" in mensagem
        # O request_id é o que liga esta linha do histórico à linha do log da api-agents, sem
        # ninguém ter de cruzar horário entre dois serviços.
        assert "7a1945f3-50f6-4d70-98a9-3b86415d0976" in mensagem
        # E cabe na coluna que guarda isso.
        assert len(mensagem) <= 300

    async def test_sem_detalhe_nenhum_a_frase_continua_inteira(self) -> None:
        with respx.mock(assert_all_called=True) as router:
            router.post(URL).mock(return_value=httpx.Response(500, text="pane"))
            with pytest.raises(LlmUnavailableError) as capturado:
                await _pedir(_gateway())
        assert capturado.value.message == LlmUnavailableError.message

    async def test_corpo_que_nao_e_json_nao_derruba_o_tratamento(self) -> None:
        """Um 502 do Traefik vem em HTML. O erro que interessa é o de cima, não um de parsing."""
        with respx.mock(assert_all_called=True) as router:
            router.post(URL).mock(return_value=httpx.Response(502, text="<html>502</html>"))
            with pytest.raises(LlmUnavailableError):
                await _pedir(_gateway())

    async def test_o_gateway_ainda_nao_implantado_diz_isso(self) -> None:
        """O deploy é em dois repositórios; esta é a janela entre eles."""
        with respx.mock(assert_all_called=True) as router:
            router.post(URL).mock(return_value=httpx.Response(404))
            with pytest.raises(LlmUnavailableError) as capturado:
                await _pedir(_gateway())
        assert "ainda não está no ar" in capturado.value.message

    async def test_api_agents_fora_do_ar_nao_vaza_excecao_de_rede(self) -> None:
        with respx.mock(assert_all_called=True) as router:
            router.post(URL).mock(side_effect=httpx.ConnectError("sem rota"))
            with pytest.raises(LlmUnavailableError):
                await _pedir(_gateway())

    async def test_resposta_200_com_formato_inesperado_e_indisponibilidade(self) -> None:
        with respx.mock(assert_all_called=True) as router:
            router.post(URL).mock(return_value=httpx.Response(200, json={"text": "só isso"}))
            with pytest.raises(LlmUnavailableError):
                await _pedir(_gateway())

    async def test_sem_token_nao_sai_chamada(self) -> None:
        """Erro nosso de configuração não vira uma ida à rede para descobrir o óbvio."""
        with respx.mock(assert_all_called=False) as router:
            rota = router.post(URL)
            with pytest.raises(LlmUnavailableError):
                await _pedir(AgentsGateway(base_url=BASE, token=""))
        assert not rota.called
