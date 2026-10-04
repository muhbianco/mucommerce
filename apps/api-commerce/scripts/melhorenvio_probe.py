"""Sondagem do sandbox do Melhor Envio para o portão F2.5 do frete v2 (docs/13-frete-v2.md).

Roda na máquina do dono, com a conta **sandbox** dele. O token nunca passa pelo terminal nem pelo
arquivo de saída: vem de um arquivo (ou de uma variável de ambiente) e só vai no cabeçalho da
requisição. A saída grava pedido e resposta de cada teste, sem cabeçalhos, com CEPs públicos
de exemplo e nenhum dado pessoal.

Uso (PowerShell ou bash), dentro de apps/api-commerce:

    $env:MELHORENVIO_SANDBOX_TOKEN_FILE = "$HOME\\.secrets\\melhorenvio-sandbox-token"
    $env:MELHORENVIO_USER_AGENT = "MuhBianco (seu-email-de-contato)"
    .venv\\Scripts\\python -m scripts.melhorenvio_probe --out probe-melhorenvio.json

Opcional, para testar a compra multivolume (premissa 2) no carrinho do sandbox:
`--cart partes.json`, um arquivo com `{"from": {...}, "to": {...}}` no formato do `/me/cart`
(nome, telefone, e-mail, documento, endereço). O script apaga do carrinho o que inseriu.

Testes:
  1. seguro por volume: `volumes[].insurance`, `volumes[].insurance_value` e
     `options.insurance_value`, com 2 volumes de valores diferentes;
  2. multivolume x soma por volume na cotação (e, com `--cart`, na inserção no carrinho);
  3. limites: abaixo do mínimo dos Correios, lado de 75 cm (taxa de não mecanizável?), 105 cm;
  4. grafia dos campos: `height`/`length` x `heigth`/`lenght` (como está no OpenAPI);
  5. prazo: `delivery_time`, `delivery_range` e os `custom_*` de uma rota conhecida;
  6. lista de serviços: `GET /api/v2/me/shipment/services`.

Modo `--dce` (com `--cart`): o caminho inteiro da compra — carrinho, pagamento, geração e
impressão — para saber onde a nota fiscal ou a DC-e (declaração de conteúdo eletrônica,
obrigatória desde 06/04/2026) são cobradas. **Gasta saldo do sandbox** (dinheiro de mentira):
confira se a carteira do sandbox tem uns R$ 200 antes. Casos, todos como envio não comercial:
  A. remetente CPF, PAC, produtos item a item;
  B. remetente CPF, Jadlog .Package, produtos item a item;
  C. remetente CNPJ, PAC, produtos item a item;
  D. remetente CNPJ, Jadlog .Package, produtos item a item;
  E. remetente CPF, PAC, uma linha só ("Pedido 123"), como o despacho manda hoje.
O CPF de teste do remetente vai no arquivo do `--cart`, em `"remetente_cpf"`. Os links das
etiquetas ficam em `probe-melhorenvio-etiquetas.json` (local: a etiqueta mostra os endereços);
a saída principal não leva link, chave de documento fiscal nem dado pessoal.

A geração da etiqueta é assíncrona ("Envio encaminhado para geração"): depois de gerar, a sonda
consulta a etiqueta a cada 10 s, por até 3 min, até a fila terminar ou falhar. `--casos A,B,E`
roda só esses casos; `--status probe-melhorenvio-dce.json` consulta de novo as etiquetas de uma
rodada anterior (sem comprar nada).

    .venv\\Scripts\\python -m scripts.melhorenvio_probe --cart melhorenvio-partes.json `
        --dce --out probe-melhorenvio-dce.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

BASE = "https://sandbox.melhorenvio.com.br"
#: CEPs públicos (Praça da Sé, SP -> Centro, RJ): nenhum dado de cliente entra na sondagem.
ORIGEM = "01001000"
DESTINO = "20040020"
TIMEOUT = httpx.Timeout(20.0, connect=5.0)


def _token() -> str:
    caminho = os.environ.get("MELHORENVIO_SANDBOX_TOKEN_FILE")
    if caminho:
        return Path(caminho).expanduser().read_text(encoding="utf-8").strip()
    token = os.environ.get("MELHORENVIO_SANDBOX_TOKEN", "").strip()
    if not token:
        sys.exit("Defina MELHORENVIO_SANDBOX_TOKEN_FILE (preferido) ou MELHORENVIO_SANDBOX_TOKEN.")
    return token


def _user_agent() -> str:
    agente = os.environ.get("MELHORENVIO_USER_AGENT", "").strip()
    if "@" not in agente:
        sys.exit("Defina MELHORENVIO_USER_AGENT com nome e e-mail de contato (a API exige).")
    return agente


class Sonda:
    def __init__(self, token: str, user_agent: str) -> None:
        self._client = httpx.Client(
            base_url=BASE,
            timeout=TIMEOUT,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
                "User-Agent": user_agent,
            },
        )
        self.registro: list[dict[str, Any]] = []

    def close(self) -> None:
        self._client.close()

    def chama(
        self,
        teste: str,
        metodo: str,
        caminho: str,
        corpo: Any = None,
        *,
        pessoal: bool = False,
        registrar: bool = True,
    ) -> Any:
        try:
            resposta = self._client.request(metodo, caminho, json=corpo)
            status, texto = resposta.status_code, resposta.text
        except httpx.HTTPError as exc:  # rede, timeout: registra o tipo, nunca o cabeçalho
            status, texto = -1, type(exc).__name__
        try:
            dados: Any = json.loads(texto)
        except ValueError:
            dados = texto[:2000]
        if not registrar:
            return dados if 200 <= status < 300 else None
        # Carrinho leva nome, documento, endereço e contato: a saída grava sem eles.
        self.registro.append(
            {
                "teste": teste,
                "metodo": metodo,
                "caminho": caminho,
                "pedido": _sem_pessoais(corpo) if pessoal else corpo,
                "status": status,
                "resposta": _sem_pessoais(dados) if pessoal else dados,
            }
        )
        return dados if 200 <= status < 300 else None


#: Campos que identificam pessoa ou empresa: nunca vão para o arquivo de saída.
_PESSOAIS = frozenset(
    {
        "from",
        "to",
        "name",
        "document",
        "company_document",
        "state_register",
        "email",
        "phone",
        "address",
        "number",
        "complement",
        "district",
        "postal_code",
        "user",
        "additional_info",
    }
)


#: Chaves que dão acesso a documento ou etiqueta: link da etiqueta abre os endereços, e chave de
#: NF-e/DC-e carrega o CPF/CNPJ de quem emitiu.
_ACESSO = frozenset({"url", "key", "chave", "protocol", "self_tracking"})
_CHAVE_FISCAL = re.compile(r"^\d{44}$")


def _sem_pessoais(dados: Any) -> Any:
    if isinstance(dados, dict):
        return {
            k: (
                "<omitido>"
                if k in _PESSOAIS
                else "<acesso omitido>"
                if k in _ACESSO and dados.get(k)
                else _sem_pessoais(v)
            )
            for k, v in dados.items()
        }
    if isinstance(dados, list):
        return [_sem_pessoais(v) for v in dados]
    if isinstance(dados, str) and _CHAVE_FISCAL.match(dados):
        return "<chave fiscal omitida>"
    return dados


def _chaves(dados: Any, prefixo: str = "") -> list[str]:
    """Os caminhos de campo de uma resposta (para achar onde a DC-e aparece, sem os valores)."""
    caminhos: list[str] = []
    if isinstance(dados, dict):
        for k, v in dados.items():
            caminho = f"{prefixo}.{k}" if prefixo else str(k)
            caminhos.append(caminho)
            caminhos += _chaves(v, caminho)
    elif isinstance(dados, list) and dados:
        caminhos += _chaves(dados[0], f"{prefixo}[]")
    return caminhos


def _volume(
    cm: tuple[float, float, float], kg: float, *, grafia: str = "certa", **extra: Any
) -> dict[str, Any]:
    comprimento, largura, altura = cm
    if grafia == "openapi":
        base = {"width": largura, "heigth": altura, "lenght": comprimento, "weight": kg}
    else:
        base = {"width": largura, "height": altura, "length": comprimento, "weight": kg}
    return base | extra


def _cotacao(volumes: list[dict[str, Any]], **options: Any) -> dict[str, Any]:
    corpo: dict[str, Any] = {
        "from": {"postal_code": ORIGEM},
        "to": {"postal_code": DESTINO},
        "volumes": volumes,
    }
    if options:
        corpo["options"] = options
    return corpo


def _resumo(dados: Any) -> list[dict[str, Any]]:
    if not isinstance(dados, list):
        return []
    linhas = []
    for opcao in dados:
        if not isinstance(opcao, dict):
            continue
        linhas.append(
            {
                "id": opcao.get("id"),
                "servico": opcao.get("name"),
                "transportadora": (opcao.get("company") or {}).get("name"),
                "preco": opcao.get("custom_price") or opcao.get("price"),
                "prazo": opcao.get("custom_delivery_time") or opcao.get("delivery_time"),
                "faixa": opcao.get("custom_delivery_range") or opcao.get("delivery_range"),
                "erro": opcao.get("error"),
                "seguro_por_pacote": [
                    p.get("insurance_value")
                    for p in opcao.get("packages") or []
                    if isinstance(p, dict)
                ],
                "dimensoes_por_pacote": [
                    p.get("dimensions") for p in opcao.get("packages") or [] if isinstance(p, dict)
                ],
            }
        )
    return linhas


def rodar(sonda: Sonda, partes: dict[str, Any] | None) -> dict[str, Any]:
    caixa = (30.0, 20.0, 15.0)
    resultado: dict[str, Any] = {}

    # 6. serviços (sem token, mas vai com ele; a doc diz que não precisa)
    servicos = sonda.chama("6-servicos", "GET", "/api/v2/me/shipment/services")
    resultado["6_servicos"] = [
        {
            "id": s.get("id"),
            "nome": s.get("name"),
            "transportadora": (s.get("company") or {}).get("name"),
        }
        for s in servicos or []
        if isinstance(s, dict)
    ]

    # 4. grafia dos campos
    for grafia in ("certa", "openapi"):
        dados = sonda.chama(
            f"4-grafia-{grafia}",
            "POST",
            "/api/v2/me/shipment/calculate",
            _cotacao([_volume(caixa, 1.0, grafia=grafia)]),
        )
        resultado[f"4_grafia_{grafia}"] = _resumo(dados)

    # 1. seguro por volume (2 volumes, valores 50 e 150)
    variantes = {
        "insurance": (
            [_volume(caixa, 1.0, insurance=50.0), _volume(caixa, 1.0, insurance=150.0)],
            {},
        ),
        "insurance_value": (
            [_volume(caixa, 1.0, insurance_value=50.0), _volume(caixa, 1.0, insurance_value=150.0)],
            {},
        ),
        "options": ([_volume(caixa, 1.0), _volume(caixa, 1.0)], {"insurance_value": 200.0}),
    }
    for nome, (volumes, options) in variantes.items():
        dados = sonda.chama(
            f"1-seguro-{nome}",
            "POST",
            "/api/v2/me/shipment/calculate",
            _cotacao(volumes, **options),
        )
        resultado[f"1_seguro_{nome}"] = _resumo(dados)

    # 2. multivolume x soma por volume (cotação)
    um = sonda.chama(
        "2-um-volume",
        "POST",
        "/api/v2/me/shipment/calculate",
        _cotacao([_volume(caixa, 1.0)], insurance_value=100.0),
    )
    dois = sonda.chama(
        "2-dois-volumes",
        "POST",
        "/api/v2/me/shipment/calculate",
        _cotacao([_volume(caixa, 1.0), _volume(caixa, 1.0)], insurance_value=200.0),
    )
    resultado["2_um_volume"] = _resumo(um)
    resultado["2_dois_volumes"] = _resumo(dois)

    # 3. limites
    for nome, cm in (
        ("abaixo-do-minimo", (5.0, 5.0, 0.5)),
        ("lado-69", (69.0, 30.0, 30.0)),
        ("lado-75", (75.0, 30.0, 30.0)),
        ("lado-105", (105.0, 30.0, 30.0)),
    ):
        dados = sonda.chama(
            f"3-{nome}",
            "POST",
            "/api/v2/me/shipment/calculate",
            _cotacao([_volume(cm, 1.0)], insurance_value=100.0),
        )
        resultado[f"3_{nome}"] = _resumo(dados)

    # 5. prazo: já está nos resumos acima (prazo e faixa); repetido aqui para PAC e SEDEX
    resultado["5_prazo"] = [
        linha for linha in resultado["2_um_volume"] if str(linha.get("id")) in {"1", "2"}
    ]

    # 2b. carrinho (opcional): 2 volumes no PAC (deve recusar) e na Jadlog (deve aceitar)
    if partes is not None:
        resultado["2b_carrinho"] = _carrinho(sonda, partes, servicos or [])
    return resultado


def _carrinho(sonda: Sonda, partes: dict[str, Any], servicos: list[Any]) -> list[dict[str, Any]]:
    """Inserção no carrinho do sandbox (não compra nada; o que entrar é apagado em seguida).

    Responde a F7: os Correios recusam vários volumes numa inserção? E na compra o seguro vai em
    `volumes[].insurance` (como na cotação) ou em `options.insurance_value`?
    """
    jadlog = next(
        (
            s.get("id")
            for s in servicos
            if isinstance(s, dict)
            and "jadlog" in str((s.get("company") or {}).get("name", "")).lower()
            and ".package" in str(s.get("name", "")).lower()
        ),
        None,
    )
    caixa = {"height": 15, "width": 20, "length": 30, "weight": 1.0}
    casos: list[tuple[str, Any, list[dict[str, Any]], float | None]] = [
        ("pac-2-volumes", 1, [caixa, caixa], 100.0),
        ("pac-1-volume-seguro-no-volume", 1, [caixa | {"insurance": 100.0}], None),
        ("pac-1-volume-seguro-em-options", 1, [caixa], 100.0),
    ]
    if jadlog is not None:
        casos += [
            ("jadlog-2-volumes-seguro-em-options", jadlog, [caixa, caixa], 100.0),
            (
                "jadlog-2-volumes-seguro-no-volume",
                jadlog,
                [caixa | {"insurance": 50.0}, caixa | {"insurance": 50.0}],
                None,
            ),
        ]
    linhas = []
    for nome, servico, volumes, seguro_options in casos:
        opcoes: dict[str, Any] = {"receipt": False, "own_hand": False, "non_commercial": True}
        if seguro_options is not None:
            opcoes["insurance_value"] = seguro_options
        corpo = {
            "service": servico,
            "from": partes["from"],
            "to": partes["to"],
            "products": [{"name": "Teste de sondagem", "quantity": 2, "unitary_value": 50.0}],
            "volumes": volumes,
            "options": opcoes,
        }
        dados = sonda.chama(f"2b-{nome}", "POST", "/api/v2/me/cart", corpo, pessoal=True)
        resposta = sonda.registro[-1]["resposta"]
        linhas.append(
            {
                "caso": nome,
                "servico": servico,
                "aceitou": dados is not None,
                "status": sonda.registro[-1]["status"],
                "preco": resposta.get("price") if isinstance(resposta, dict) else None,
                "seguro": resposta.get("insurance_value") if isinstance(resposta, dict) else None,
                "volumes": len(resposta.get("volumes") or [])
                if isinstance(resposta, dict)
                else None,
                "erro": None if dados is not None else resposta,
            }
        )
        item = dados.get("id") if isinstance(dados, dict) else None
        if item:
            sonda.chama(f"2b-limpa-{nome}", "DELETE", f"/api/v2/me/cart/{item}", pessoal=True)
    return linhas


#: Produtos de exemplo da DC-e: nome, quantidade e valor unitário em texto, como na doc.
_ITENS = [
    {"name": "Rabiola 500 m", "quantity": "3", "unitary_value": "12.00"},
    {"name": "Carretel de linha 10", "quantity": "1", "unitary_value": "25.00"},
]
_LINHA_UNICA = [{"name": "Pedido 123", "quantity": "1", "unitary_value": "61.00"}]


def _remetentes(partes: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """O remetente do arquivo em duas versões: só CPF (`document`) e só CNPJ
    (`company_document`)."""
    base = {
        k: v
        for k, v in partes["from"].items()
        if k not in {"document", "company_document", "state_register"}
    }
    cpf = "".join(ch for ch in str(partes.get("remetente_cpf") or "") if ch.isdigit())
    cnpj = "".join(ch for ch in str(partes["from"].get("company_document") or "") if ch.isdigit())
    remetentes: dict[str, dict[str, Any]] = {}
    if len(cpf) == 11:
        remetentes["cpf"] = base | {"document": cpf}
    if len(cnpj) == 14:
        remetentes["cnpj"] = base | {"company_document": cnpj}
    return remetentes


#: Espera da geração assíncrona: a cada 10 s, por até 3 min.
_ESPERA_S = 10
_ESPERA_VEZES = 18


def _geracao_terminou(etiqueta: Any) -> bool:
    chave = etiqueta.get("generated_key") if isinstance(etiqueta, dict) else None
    if not isinstance(chave, dict):
        return bool(isinstance(etiqueta, dict) and etiqueta.get("generated_at"))
    return bool(chave.get("finished_at") or chave.get("failed_at") or etiqueta.get("generated_at"))


def _aguarda_etiqueta(sonda: Sonda, teste: str, pedido: str) -> Any:
    """Consulta a etiqueta até a geração terminar (ou o tempo acabar); registra só a última."""
    for _ in range(_ESPERA_VEZES):
        etiqueta = sonda.chama(
            teste, "GET", f"/api/v2/me/orders/{pedido}", pessoal=True, registrar=False
        )
        if _geracao_terminou(etiqueta):
            break
        time.sleep(_ESPERA_S)
    return sonda.chama(teste, "GET", f"/api/v2/me/orders/{pedido}", pessoal=True)


def _estado(etiqueta: Any) -> dict[str, Any]:
    """O que interessa da etiqueta, sem valores pessoais: a geração terminou? falhou? saiu
    rastreio? (só se existe, não o código)."""
    if not isinstance(etiqueta, dict):
        return {}
    bruta = etiqueta.get("generated_key")
    chave: dict[str, Any] = bruta if isinstance(bruta, dict) else {}
    return {
        "status": etiqueta.get("status"),
        "gerada_em": etiqueta.get("generated_at"),
        "tem_rastreio": bool(etiqueta.get("tracking") or etiqueta.get("self_tracking")),
        "fila": {
            "tentativas": chave.get("attempts"),
            "terminou_em": chave.get("finished_at"),
            "falhou_em": chave.get("failed_at"),
            "dados": _sem_pessoais(chave.get("data")),
        },
        "non_commercial": etiqueta.get("non_commercial"),
        "invoice": "preenchido" if etiqueta.get("invoice") else None,
        "produtos": len(etiqueta.get("products") or []),
    }


def _status(sonda: Sonda, anterior: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Consulta de novo as etiquetas compradas numa rodada anterior do `--dce`."""
    pedidos: dict[str, str] = {}
    for chamada in anterior.get("chamadas", []):
        teste = str(chamada.get("teste", ""))
        resposta = chamada.get("resposta")
        if teste.endswith("-etiqueta") and isinstance(resposta, dict) and resposta.get("id"):
            pedidos[teste.removeprefix("dce-").removesuffix("-etiqueta")] = str(resposta["id"])
    linhas: list[dict[str, Any]] = []
    etiquetas: dict[str, str] = {}
    for nome, pedido in pedidos.items():
        etiqueta = sonda.chama(f"status-{nome}", "GET", f"/api/v2/me/orders/{pedido}", pessoal=True)
        linhas.append(
            {
                "caso": nome,
                "estado": _estado(etiqueta),
                "campos": _passo(sonda, campos=True)["campos"],
            }
        )
        impresso = sonda.chama(
            f"status-{nome}-impressao",
            "POST",
            "/api/v2/me/shipment/print",
            {"mode": "private", "orders": [pedido]},
            pessoal=True,
        )
        if isinstance(impresso, dict) and impresso.get("url"):
            etiquetas[nome] = str(impresso["url"])
    return linhas, etiquetas


def _dce(
    sonda: Sonda, partes: dict[str, Any], so: set[str] | None = None
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Carrinho → pagamento → geração → impressão, por caso; para no primeiro passo recusado.

    Devolve o resumo (sem dado pessoal) e os links das etiquetas (só para o arquivo local).
    """
    servicos = sonda.chama("dce-servicos", "GET", "/api/v2/me/shipment/services") or []
    jadlog = next(
        (
            s.get("id")
            for s in servicos
            if isinstance(s, dict)
            and "jadlog" in str((s.get("company") or {}).get("name", "")).lower()
            and ".package" in str(s.get("name", "")).lower()
        ),
        None,
    )
    saldo = sonda.chama("dce-saldo", "GET", "/api/v2/me/balance")
    remetentes = _remetentes(partes)
    casos: list[tuple[str, str, Any, list[dict[str, str]]]] = [
        ("A-cpf-pac-itens", "cpf", 1, _ITENS),
        ("B-cpf-jadlog-itens", "cpf", jadlog, _ITENS),
        ("C-cnpj-pac-itens", "cnpj", 1, _ITENS),
        ("D-cnpj-jadlog-itens", "cnpj", jadlog, _ITENS),
        ("E-cpf-pac-linha-unica", "cpf", 1, _LINHA_UNICA),
    ]
    linhas: list[dict[str, Any]] = [
        {"saldo_sandbox": saldo.get("balance") if isinstance(saldo, dict) else None}
    ]
    etiquetas: dict[str, str] = {}
    caixa = {"height": 10, "width": 15, "length": 20, "weight": 0.68}
    for nome, quem, servico, produtos in casos:
        if so is not None and nome.split("-", 1)[0] not in so:
            continue
        linha: dict[str, Any] = {"caso": nome, "servico": servico, "passos": {}}
        linhas.append(linha)
        if servico is None or quem not in remetentes:
            linha["pulado"] = "sem Jadlog .Package na conta" if servico is None else f"sem {quem}"
            continue
        corpo = {
            "service": servico,
            "from": remetentes[quem],
            "to": partes["to"],
            "products": produtos,
            "volumes": [caixa],
            "options": {
                "insurance_value": 61.0,
                "receipt": False,
                "own_hand": False,
                "reverse": False,
                "non_commercial": True,
                "platform": "MuhBianco (sondagem)",
                "tags": [{"tag": f"sonda-{nome}"}],
            },
        }
        item = sonda.chama(f"dce-{nome}-carrinho", "POST", "/api/v2/me/cart", corpo, pessoal=True)
        linha["passos"]["carrinho"] = _passo(sonda)
        pedido = item.get("id") if isinstance(item, dict) else None
        if not pedido:
            continue
        sonda.chama(f"dce-{nome}-item", "GET", f"/api/v2/me/cart/{pedido}", pessoal=True)
        linha["passos"]["item_no_carrinho"] = _passo(sonda, campos=True)
        pago = sonda.chama(
            f"dce-{nome}-pagamento",
            "POST",
            "/api/v2/me/shipment/checkout",
            {"orders": [pedido]},
            pessoal=True,
        )
        linha["passos"]["pagamento"] = _passo(sonda)
        if pago is None:
            sonda.chama(f"dce-{nome}-limpa", "DELETE", f"/api/v2/me/cart/{pedido}", pessoal=True)
            continue
        gerado = sonda.chama(
            f"dce-{nome}-geracao",
            "POST",
            "/api/v2/me/shipment/generate",
            {"orders": [pedido]},
            pessoal=True,
        )
        linha["passos"]["geracao"] = _passo(sonda, campos=True)
        if gerado is None:
            continue
        etiqueta = _aguarda_etiqueta(sonda, f"dce-{nome}-etiqueta", pedido)
        linha["passos"]["etiqueta"] = _passo(sonda, campos=True)
        linha["estado_final"] = _estado(etiqueta)
        impresso = sonda.chama(
            f"dce-{nome}-impressao",
            "POST",
            "/api/v2/me/shipment/print",
            {"mode": "private", "orders": [pedido]},
            pessoal=True,
        )
        linha["passos"]["impressao"] = _passo(sonda, campos=True)
        if isinstance(impresso, dict) and impresso.get("url"):
            etiquetas[nome] = str(impresso["url"])
    return linhas, etiquetas


def _passo(sonda: Sonda, *, campos: bool = False) -> dict[str, Any]:
    """O último passo: status, a resposta (já sem dado pessoal) e, se pedido, os caminhos de
    campo da resposta — é por eles que se acha onde a DC-e aparece."""
    ultima = sonda.registro[-1]
    passo: dict[str, Any] = {"status": ultima["status"], "resposta": ultima["resposta"]}
    if campos:
        passo["campos"] = sorted(set(_chaves(ultima["resposta"])))
    return passo


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="probe-melhorenvio.json")
    parser.add_argument("--cart", help="JSON com {from, to} para testar a inserção no carrinho")
    parser.add_argument(
        "--only-cart", action="store_true", help="roda só os testes de carrinho (exige --cart)"
    )
    parser.add_argument(
        "--dce",
        action="store_true",
        help="compra de ponta a ponta (gasta saldo do sandbox) para a DC-e (exige --cart)",
    )
    parser.add_argument("--casos", help="com --dce: só estes casos, ex. A,B,E")
    parser.add_argument(
        "--status", help="consulta de novo as etiquetas de uma rodada anterior do --dce"
    )
    args = parser.parse_args()
    partes = json.loads(Path(args.cart).read_text(encoding="utf-8")) if args.cart else None
    if (args.only_cart or args.dce) and partes is None:
        sys.exit("--only-cart e --dce precisam de --cart <arquivo com from/to>.")
    so = {c.strip().upper() for c in args.casos.split(",")} if args.casos else None
    precisa_cpf = so is None or bool(so & {"A", "B", "E"})
    if args.dce and partes is not None and precisa_cpf:
        cpf = _remetentes(partes).get("cpf")
        destino = "".join(ch for ch in str(partes["to"].get("document") or "") if ch.isdigit())
        if not cpf:
            sys.exit('--dce precisa de "remetente_cpf" (11 dígitos) no arquivo do --cart.')
        if cpf.get("document") == destino:
            sys.exit('"remetente_cpf" não pode ser o mesmo CPF do destinatário ("to").')

    sonda = Sonda(_token(), _user_agent())
    try:
        etiquetas: dict[str, str] = {}
        if args.status:
            anterior = json.loads(Path(args.status).read_text(encoding="utf-8"))
            estados, etiquetas = _status(sonda, anterior)
            resultado: dict[str, Any] = {"status": estados}
        elif args.dce and partes is not None:
            casos, etiquetas = _dce(sonda, partes, so)
            resultado = {"dce": casos}
        elif args.only_cart and partes is not None:
            servicos = sonda.chama("6-servicos", "GET", "/api/v2/me/shipment/services") or []
            resultado = {"2b_carrinho": _carrinho(sonda, partes, servicos)}
        else:
            resultado = rodar(sonda, partes)
    finally:
        sonda.close()
    if etiquetas:
        # Links das etiquetas (mostram os endereços): ficam só na máquina do dono, para abrir o
        # PDF e ver se a DC-e sai junto. Este arquivo não é lido por ninguém além dele.
        Path("probe-melhorenvio-etiquetas.json").write_text(
            json.dumps(etiquetas, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("Links das etiquetas em probe-melhorenvio-etiquetas.json (só para você abrir).")
    saida = {
        "rodado_em": datetime.now(UTC).isoformat(timespec="seconds"),
        "base": BASE,
        "resultado": resultado,
        "chamadas": sonda.registro,
    }
    Path(args.out).write_text(json.dumps(saida, ensure_ascii=False, indent=2), encoding="utf-8")
    falhas = sum(1 for c in sonda.registro if not 200 <= c["status"] < 300)
    print(f"{len(sonda.registro)} chamadas, {falhas} com erro HTTP. Resultado em {args.out}.")


if __name__ == "__main__":
    main()
