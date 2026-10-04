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
"""

from __future__ import annotations

import argparse
import json
import os
import sys
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
        self, teste: str, metodo: str, caminho: str, corpo: Any = None, *, pessoal: bool = False
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
    }
)


def _sem_pessoais(dados: Any) -> Any:
    if isinstance(dados, dict):
        return {k: ("<omitido>" if k in _PESSOAIS else _sem_pessoais(v)) for k, v in dados.items()}
    if isinstance(dados, list):
        return [_sem_pessoais(v) for v in dados]
    return dados


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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="probe-melhorenvio.json")
    parser.add_argument("--cart", help="JSON com {from, to} para testar a inserção no carrinho")
    parser.add_argument(
        "--only-cart", action="store_true", help="roda só os testes de carrinho (exige --cart)"
    )
    args = parser.parse_args()
    partes = json.loads(Path(args.cart).read_text(encoding="utf-8")) if args.cart else None
    if args.only_cart and partes is None:
        sys.exit("--only-cart precisa de --cart <arquivo com from/to>.")

    sonda = Sonda(_token(), _user_agent())
    try:
        if args.only_cart and partes is not None:
            servicos = sonda.chama("6-servicos", "GET", "/api/v2/me/shipment/services") or []
            resultado = {"2b_carrinho": _carrinho(sonda, partes, servicos)}
        else:
            resultado = rodar(sonda, partes)
    finally:
        sonda.close()
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
