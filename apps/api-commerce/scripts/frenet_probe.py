"""Sondagem da Frenet para o portão da etapa 4 (docs/14-pedidos-lunares.md §4).

A Frenet **não tem sandbox**: a doc diz que "toda a validação é realizada em ambiente de produção".
Então esta sonda só faz o que não custa nada — listar serviços, cotar, listar envios, rastrear um
código inventado — e, com `--etiqueta`, **cria** um envio sem pagar e o apaga em seguida. Pagar
etiqueta (o `checkout`) não está aqui de propósito: é dinheiro de verdade e pede um ok à parte.

Os tokens nunca passam pelo terminal nem pelo arquivo de saída: vêm de arquivos e só vão no
cabeçalho da requisição. A saída grava pedido e resposta de cada chamada, sem cabeçalhos, com CEPs
públicos de exemplo e sem dado pessoal.

Uso (PowerShell ou bash), dentro de apps/api-commerce:

    .venv\\Scripts\\python -m scripts.frenet_probe --out probe-frenet.json

Por padrão lê `~/.secrets/frenet-token` (o token do painel da loja na Frenet, em "Chaves de
acesso") e, se existir, `~/.secrets/frenet-partner-token` (o `x-partner-token`, que a Frenet só dá
a plataforma parceira — não aparece no painel do lojista). Sem o de parceiro, a sonda pula tudo
que é `whitelabel` e fica no que a loja sozinha alcança: serviços, cotação por itens e rastreio.
Dá para apontar outros arquivos com `FRENET_TOKEN_FILE` e `FRENET_PARTNER_TOKEN_FILE`.

O que cada teste responde:
  1. serviços: `GET api.frenet.com.br/shipping/info` — códigos, transportadoras, formato;
  2. cotação de parceiro com **volumes**: `POST whitelabel.frenet.com.br/v1/quotes` — 1 volume,
     2 volumes diferentes, e o mesmo pedido em camelCase (a doc mistura as grafias); unidades
     (kg/cm), seguro (`ShipmentItemValue`, `Services.DeclaredValue`), `SessionId`, preço em número
     ou texto, prazo;
  3. cotação pública com **itens**: `POST api.frenet.com.br/shipping/quote` — 1 item, 2 itens
     diferentes e 1 item com quantidade 2, para ver se a Frenet junta os itens numa caixa (o motor
     de embalagem é nosso: cada volume planejado iria como um item) e se o preço de 2 é a soma;
     e os limites (105 cm, 31 kg, CEP inválido) também por itens;
  4. limites: lado de 105 cm, 31 kg, CEP inválido;
  5. tokens de parceiro aceitos: `GET whitelabel.frenet.com.br/v1/shipments?page=1&per_page=1`;
  6. rastreio de um código inventado: formato do erro.

Com `--etiqueta partes.json` (remetente e destinatário no formato `From`/`To` da Frenet, com
`Address`), cria um envio a partir da cotação de 1 volume, lê a etiqueta (antes de pagar) e apaga
o envio. Os links e os dados pessoais ficam fora da saída.

Com `--so-lojista`, chama a API whitelabel **só com o token do lojista**, sem `x-partner-token`.
A referência da Frenet marca os dois cabeçalhos como obrigatórios, mas o bloco `security` de cada
operação os lista como alternativas (`[{api_key}, {api_partner_key}]`): só a chamada responde.
Também lê o saldo da carteira (`GET /v1/wallet`), que não custa nada.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

API = "https://api.frenet.com.br"
WHITELABEL = "https://whitelabel.frenet.com.br"
#: CEPs públicos (Praça da Sé, SP -> Centro, RJ): nenhum dado de cliente entra na sondagem.
ORIGEM = "01001000"
DESTINO = "20040020"
TIMEOUT = httpx.Timeout(20.0, connect=5.0)


def _secret(env: str, default: str, *, required: bool = True) -> str | None:
    caminho = Path(os.environ.get(env) or default).expanduser()
    if not caminho.exists():
        if not required:
            return None
        sys.exit(f"Arquivo do token não encontrado ({env} ou {default}).")
    valor = caminho.read_text(encoding="utf-8").strip()
    if not valor:
        sys.exit(f"Arquivo do token vazio ({env} ou {default}).")
    return valor


class Sonda:
    def __init__(self, token: str, partner: str | None, *, so_lojista: bool = False) -> None:
        comum = {"Accept": "application/json", "Content-Type": "application/json"}
        self._api = httpx.Client(base_url=API, timeout=TIMEOUT, headers=comum | {"token": token})
        wl_headers = comum | {"token": token}
        if partner and not so_lojista:
            wl_headers["x-partner-token"] = partner
        self._wl = (
            httpx.Client(base_url=WHITELABEL, timeout=TIMEOUT, headers=wl_headers)
            if partner or so_lojista
            else None
        )
        self.so_lojista = so_lojista
        self.registro: list[dict[str, Any]] = []

    @property
    def parceiro(self) -> bool:
        return self._wl is not None

    def close(self) -> None:
        self._api.close()
        if self._wl is not None:
            self._wl.close()

    def chama(
        self,
        teste: str,
        onde: str,
        metodo: str,
        caminho: str,
        corpo: Any = None,
        *,
        pessoal: bool = False,
    ) -> tuple[int, Any]:
        cliente = self._wl if onde == "whitelabel" else self._api
        if cliente is None:
            raise RuntimeError("chamada whitelabel sem token de parceiro")
        try:
            resposta = cliente.request(metodo, caminho, json=corpo)
            status, texto = resposta.status_code, resposta.text
        except httpx.HTTPError as exc:  # rede, timeout: registra o tipo, nunca o cabeçalho
            status, texto = -1, type(exc).__name__
        try:
            dados: Any = json.loads(texto) if texto else None
        except ValueError:
            dados = texto[:2000]
        self.registro.append(
            {
                "teste": teste,
                "onde": onde,
                "metodo": metodo,
                "caminho": caminho,
                "pedido": _sem_pessoais(corpo) if pessoal else corpo,
                "status": status,
                "resposta": _sem_pessoais(dados),
            }
        )
        return status, dados


#: Campos que identificam pessoa ou empresa: nunca vão para o arquivo de saída.
_PESSOAIS = frozenset(
    {
        "From",
        "To",
        "Name",
        "Email",
        "Phone",
        "Cellphone",
        "Document",
        "IE",
        "Address",
        "Street",
        "AddressNumber",
        "AddressComplement",
        "AddressQuarter",
    }
)
#: Links que abrem etiqueta ou declaração (mostram endereços).
_ACESSO = re.compile(r"url$", re.IGNORECASE)


def _sem_pessoais(dados: Any) -> Any:
    if isinstance(dados, dict):
        limpo: dict[str, Any] = {}
        for k, v in dados.items():
            if k in _PESSOAIS or k.lower() in {p.lower() for p in _PESSOAIS}:
                limpo[k] = "<omitido>"
            elif _ACESSO.search(k) and v:
                limpo[k] = "<link omitido>"
            else:
                limpo[k] = _sem_pessoais(v)
        return limpo
    if isinstance(dados, list):
        return [_sem_pessoais(v) for v in dados]
    return dados


VOLUME_A = {"Weight": 0.5, "Length": 20, "Height": 10, "Width": 15}
VOLUME_B = {"Weight": 2.0, "Length": 40, "Height": 25, "Width": 30}


def _cotacao(volumes: list[dict[str, Any]], valor: float = 150.0) -> dict[str, Any]:
    return {
        "SenderZipCode": ORIGEM,
        "RecipientZipCode": DESTINO,
        "RecipientCountry": "BR",
        "ShipmentItemValue": valor,
        "Services": {"DeclaredValue": True, "ReceiptNotification": False, "OwnHand": False},
        "Volumes": volumes,
    }


def _camel(dados: Any) -> Any:
    if isinstance(dados, dict):
        return {k[:1].lower() + k[1:]: _camel(v) for k, v in dados.items()}
    if isinstance(dados, list):
        return [_camel(v) for v in dados]
    return dados


def _itens(*itens: dict[str, Any], quantidade: int = 1, destino: str = DESTINO) -> dict[str, Any]:
    return {
        "SellerCEP": ORIGEM,
        "RecipientCEP": destino,
        "ShipmentInvoiceValue": 150.0,
        "RecipientCountry": "BR",
        "ShippingItemArray": [dict(item, Quantity=quantidade) for item in itens],
    }


def rodar(sonda: Sonda) -> dict[str, Any]:
    resultado: dict[str, Any] = {"whitelabel": sonda.parceiro, "so_lojista": sonda.so_lojista}
    if sonda.parceiro:
        status, _ = sonda.chama("0.saldo", "whitelabel", "GET", "/v1/wallet")
        resultado["saldo_status"] = status
    status, _ = sonda.chama("1.servicos", "api", "GET", "/shipping/info")
    resultado["servicos_status"] = status

    grande = {"Weight": 5.0, "Length": 105, "Height": 20, "Width": 20}
    pesado = {"Weight": 31.0, "Length": 40, "Height": 30, "Width": 30}

    # O que a loja alcança sozinha: cotação por itens.
    sonda.chama("3.itens-1", "api", "POST", "/shipping/quote", _itens(VOLUME_A))
    sonda.chama(
        "3.itens-2-diferentes", "api", "POST", "/shipping/quote", _itens(VOLUME_A, VOLUME_B)
    )
    sonda.chama(
        "3.itens-1-quantidade-2", "api", "POST", "/shipping/quote", _itens(VOLUME_A, quantidade=2)
    )
    sonda.chama("3.itens-lado-105cm", "api", "POST", "/shipping/quote", _itens(grande))
    sonda.chama("3.itens-peso-31kg", "api", "POST", "/shipping/quote", _itens(pesado))
    sonda.chama(
        "3.itens-cep-invalido",
        "api",
        "POST",
        "/shipping/quote",
        _itens(VOLUME_A, destino="00000000"),
    )

    um: Any = None
    if sonda.parceiro:
        s1, um = sonda.chama(
            "2.cotacao-1-volume", "whitelabel", "POST", "/v1/quotes", _cotacao([VOLUME_A])
        )
        s2, _ = sonda.chama(
            "2.cotacao-2-volumes",
            "whitelabel",
            "POST",
            "/v1/quotes",
            _cotacao([VOLUME_A, VOLUME_B]),
        )
        s3, _ = sonda.chama(
            "2.cotacao-1-volume-camelCase",
            "whitelabel",
            "POST",
            "/v1/quotes",
            _camel(_cotacao([VOLUME_A])),
        )
        resultado["cotacao_volumes_status"] = [s1, s2, s3]
        sonda.chama("4.lado-105cm", "whitelabel", "POST", "/v1/quotes", _cotacao([grande]))
        sonda.chama("4.peso-31kg", "whitelabel", "POST", "/v1/quotes", _cotacao([pesado]))
        invalido = _cotacao([VOLUME_A]) | {"RecipientZipCode": "00000000"}
        sonda.chama("4.cep-invalido", "whitelabel", "POST", "/v1/quotes", invalido)
        status, _ = sonda.chama("5.envios", "whitelabel", "GET", "/v1/shipments?page=1&per_page=1")
        resultado["parceiro_status"] = status

    sonda.chama(
        "6.rastreio-inventado",
        "api",
        "POST",
        "/tracking/trackinginfo",
        {"ShippingServiceCode": "03298", "TrackingNumber": "AA000000000BR"},
    )
    resultado["_cotacao_1_volume"] = um
    return resultado


def etiqueta(sonda: Sonda, partes: dict[str, Any], cotacao: Any) -> dict[str, Any]:
    """Cria um envio sem pagar, lê a etiqueta e apaga. Nada aqui gasta saldo."""
    opcoes = (cotacao or {}).get("Quotations") or (cotacao or {}).get("quotations") or []
    valida = next((o for o in opcoes if not (o.get("Error") or o.get("error"))), None)
    if valida is None:
        return {"etiqueta": "sem cotação válida para criar o envio"}
    envio = [
        {
            "Order": {
                "Id": f"sonda-{datetime.now(UTC):%Y%m%d%H%M%S}",
                "Value": 150.0,
                "Created": datetime.now(UTC).isoformat(),
                "Items": [
                    {
                        "ItemId": "1",
                        "ProductName": "Item de sondagem",
                        "SKU": "SONDA-1",
                        "Quantity": 1,
                        "Price": 150.0,
                        **VOLUME_A,
                    }
                ],
                "From": partes["From"],
                "To": partes["To"],
            },
            "Volumes": dict(VOLUME_A, VolumeId=1, Price=150.0, DeclaredValue=150.0),
            "Quotation": {
                k: valida.get(k)
                for k in (
                    "ShippingServiceCode",
                    "ShippingServiceName",
                    "Carrier",
                    "CarrierCode",
                    "ShippingPrice",
                    "PlatformShippingPrice",
                    "DeliveryTime",
                )
            },
        }
    ]
    status, criado = sonda.chama(
        "7.cria-envio", "whitelabel", "POST", "/v1/shipments", envio, pessoal=True
    )
    itens = (criado or {}).get("Items") or (criado or {}).get("items") or []
    ids = [i.get("ShipmentId") or i.get("shipmentId") for i in itens if isinstance(i, dict)]
    ids = [i for i in ids if i]
    for shipment_id in ids:
        sonda.chama(
            "7.etiqueta-antes-de-pagar", "whitelabel", "GET", f"/v1/shipments/{shipment_id}/label"
        )
        sonda.chama("7.apaga-envio", "whitelabel", "DELETE", f"/v1/shipments/{shipment_id}")
    return {"cria_status": status, "envios_criados": len(ids)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="probe-frenet.json")
    parser.add_argument("--etiqueta", help="arquivo com From/To (cria e apaga um envio, sem pagar)")
    parser.add_argument(
        "--so-lojista",
        action="store_true",
        help="chama a API whitelabel só com o token do lojista (sem x-partner-token)",
    )
    args = parser.parse_args()

    sonda = Sonda(
        _secret("FRENET_TOKEN_FILE", "~/.secrets/frenet-token"),
        None
        if args.so_lojista
        else _secret(
            "FRENET_PARTNER_TOKEN_FILE", "~/.secrets/frenet-partner-token", required=False
        ),
        so_lojista=args.so_lojista,
    )
    try:
        resultado = rodar(sonda)
        cotacao = resultado.pop("_cotacao_1_volume", None)
        if args.etiqueta and not sonda.parceiro:
            sys.exit("--etiqueta precisa da API whitelabel (token de parceiro ou --so-lojista).")
        if args.etiqueta:
            partes = json.loads(Path(args.etiqueta).read_text(encoding="utf-8"))
            resultado |= etiqueta(sonda, partes, cotacao)
    finally:
        sonda.close()
    saida = {
        "rodado_em": datetime.now(UTC).isoformat(),
        "resultado": resultado,
        "chamadas": sonda.registro,
    }
    Path(args.out).write_text(json.dumps(saida, ensure_ascii=False, indent=2), encoding="utf-8")
    erros = sum(1 for c in sonda.registro if not 200 <= int(c["status"]) < 300)
    print(f"{len(sonda.registro)} chamadas, {erros} fora de 2xx. Saída em {args.out}.")


if __name__ == "__main__":
    main()
