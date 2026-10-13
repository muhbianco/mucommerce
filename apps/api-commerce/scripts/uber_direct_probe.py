"""Sondagem do Uber Direct para o portão da F0 (docs/15-entrega-uber-direct.md).

Responde, com chamadas reais, o que a doc deixa em aberto antes de o provedor ser escrito:
formato do endereço aceito no Brasil (estruturado ou texto), se lat/long são exigidos, o que a
cotação devolve (preço, moeda, ETA, validade), os campos obrigatórios da criação, o link de
acompanhamento, a referência do nosso pedido (`external_id`/`manifest_reference`), a sequência de
estados do entregador simulado, a consulta e o cancelamento.

**Sem `--entrega-teste` nada é criado:** só token e cotações (de graça, sem efeito). Com a flag,
cria entregas **com o entregador simulado** (`test_specifications.robo_courier_specification`), e
só com `--sandbox` (as chaves de teste).

Toda criação leva o entregador simulado — `_post` recusa, antes da rede, qualquer `/deliveries`
sem ele. É isso que protege: com chave de produção a Uber responde 400 ("test_specifications not
allowed for production requests") e **não cria nada**. A F0 tinha um "canário" que tirava o
entregador simulado para descobrir o modo pelo `live_mode`; o `live_mode` mentiu (veio `false` em
produção), a entrega era real, e criar e cancelar no mesmo segundo custou R$ 12,40 ao dono. O
canário não existe mais.

As credenciais vêm de arquivos e só vão no corpo da requisição de token; o token só vai no
cabeçalho. Nada disso aparece no terminal nem no arquivo de saída. Telefones, nomes de entregador e
placas da resposta saem mascarados. Endereços são públicos de exemplo, em Osasco/SP.

Uso, dentro de apps/api-commerce:

    .venv\\Scripts\\python -m scripts.uber_direct_probe --out probe-uber.json
    .venv\\Scripts\\python -m scripts.uber_direct_probe --out probe-uber.json --entrega-teste

    .venv\\Scripts\\python -m scripts.uber_direct_probe --sandbox --entrega-teste \\
        --out probe-uber-sandbox.json

Lê `~/.secrets/uber-direct-customer-id`, `-client-id` e `-client-secret` (ou os caminhos em
`UBER_DIRECT_CUSTOMER_ID_FILE`, `UBER_DIRECT_CLIENT_ID_FILE`, `UBER_DIRECT_CLIENT_SECRET_FILE`);
com `--sandbox`, os mesmos nomes terminados em `-sandbox`.
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

AUTH_URL = "https://auth.uber.com/oauth/v2/token"
API = "https://api.uber.com/v1/customers"
TIMEOUT = httpx.Timeout(20.0, connect=5.0)

#: Coleta e entrega de exemplo, públicas, a ~2 km uma da outra em Osasco/SP.
COLETA = {
    "street_address": ["Rua Itabuna, 100"],
    "city": "Osasco",
    "state": "SP",
    "zip_code": "06010-120",
    "country": "BR",
}
ENTREGA = {
    "street_address": ["Avenida dos Autonomistas, 1400"],
    "city": "Osasco",
    "state": "SP",
    "zip_code": "06020-010",
    "country": "BR",
}
#: Longe demais (Rio de Janeiro), para ver o erro de fora de alcance.
LONGE = {
    "street_address": ["Praça Floriano, 1"],
    "city": "Rio de Janeiro",
    "state": "RJ",
    "zip_code": "20031-050",
    "country": "BR",
}
COLETA_TEXTO = "Rua Itabuna, 100, Centro, Osasco - SP, 06010-120, Brasil"
ENTREGA_TEXTO = "Avenida dos Autonomistas, 1400, Vila Yara, Osasco - SP, 06020-010, Brasil"
#: Coordenadas aproximadas dos dois endereços acima.
COLETA_LATLNG = (-23.5329, -46.7917)
ENTREGA_LATLNG = (-23.5405, -46.7726)

#: Chaves cujo valor nunca sai da sonda, em qualquer nível da resposta.
_MASCARAR = re.compile(
    r"(phone|telefone|token|secret|license|plate|vehicle_license|courier_name|signature)",
    re.IGNORECASE,
)


def _secret(env: str, nome: str) -> str:
    caminho = Path(os.environ.get(env) or f"~/.secrets/{nome}").expanduser()
    if not caminho.exists():
        sys.exit(f"Arquivo não encontrado ({env} ou ~/.secrets/{nome}).")
    valor = caminho.read_text(encoding="utf-8").strip()
    if not valor:
        sys.exit(f"Arquivo vazio ({env} ou ~/.secrets/{nome}).")
    return valor


def _limpo(valor: Any, chave: str = "") -> Any:
    """Copia a resposta escondendo telefones, tokens, placas e o nome do entregador."""
    if isinstance(valor, dict):
        saida: dict[str, Any] = {}
        for k, v in valor.items():
            if _MASCARAR.search(str(k)) and not isinstance(v, (dict, list)):
                saida[k] = "<mascarado>" if v not in (None, "") else v
            elif str(k) == "courier" and isinstance(v, dict):
                saida[k] = {
                    ck: (
                        "<mascarado>"
                        if ck in {"name", "phone_number", "img_href", "vehicle_type", "location"}
                        else _limpo(cv, ck)
                    )
                    for ck, cv in v.items()
                }
            else:
                saida[k] = _limpo(v, str(k))
        return saida
    if isinstance(valor, list):
        return [_limpo(v, chave) for v in valor]
    return valor


class Sonda:
    def __init__(self, customer_id: str, client_id: str, client_secret: str) -> None:
        self._customer = customer_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = httpx.Client(timeout=TIMEOUT)
        self._token: str | None = None
        self.registro: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ infra
    def _anota(
        self, passo: str, metodo: str, caminho: str, corpo: Any, resp: httpx.Response
    ) -> Any:
        try:
            dados = resp.json()
        except ValueError:
            dados = {"texto": resp.text[:500]}
        self.registro.append(
            {
                "passo": passo,
                "quando": datetime.now(UTC).isoformat(timespec="seconds"),
                "metodo": metodo,
                "caminho": caminho,
                "pedido": _limpo(corpo),
                "status": resp.status_code,
                "resposta": _limpo(dados),
            }
        )
        print(f"[{resp.status_code}] {passo}")
        return dados

    def _url(self, sufixo: str) -> str:
        return f"{API}/{self._customer}{sufixo}"

    def _headers(self) -> dict[str, str]:
        assert self._token
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    def _post(self, passo: str, sufixo: str, corpo: dict[str, Any]) -> tuple[int, Any]:
        if sufixo == "/deliveries" and "test_specifications" not in corpo:
            # A trava que faltou na F0: criação sem entregador simulado é entrega de verdade.
            sys.exit(f"Recusado antes da rede: '{passo}' criaria entrega sem entregador simulado.")
        resp = self._http.post(self._url(sufixo), json=corpo, headers=self._headers())
        return resp.status_code, self._anota(passo, "POST", "{customer}" + sufixo, corpo, resp)

    def _get(self, passo: str, sufixo: str) -> tuple[int, Any]:
        resp = self._http.get(self._url(sufixo), headers=self._headers())
        return resp.status_code, self._anota(passo, "GET", "{customer}" + sufixo, None, resp)

    # ------------------------------------------------------------------ passos
    def token(self) -> bool:
        resp = self._http.post(
            AUTH_URL,
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "grant_type": "client_credentials",
                "scope": "eats.deliveries",
            },
        )
        dados = self._anota(
            "token",
            "POST",
            "auth/oauth/v2/token",
            {"grant_type": "client_credentials", "scope": "eats.deliveries"},
            resp,
        )
        if resp.status_code == 200 and isinstance(dados, dict) and dados.get("access_token"):
            self._token = str(dados["access_token"])
            return True
        return False

    def cotacoes(self) -> str | None:
        """Devolve o id da cotação estruturada (para a criação), se houver."""
        status, estruturada = self._post(
            "cotação: endereço estruturado",
            "/delivery_quotes",
            {
                "pickup_address": json.dumps(COLETA, ensure_ascii=False),
                "dropoff_address": json.dumps(ENTREGA, ensure_ascii=False),
                "manifest_total_value": 5000,
                "external_store_id": "sonda-mucommerce",
            },
        )
        self._post(
            "cotação: endereço em texto",
            "/delivery_quotes",
            {"pickup_address": COLETA_TEXTO, "dropoff_address": ENTREGA_TEXTO},
        )
        self._post(
            "cotação: estruturado com lat/long",
            "/delivery_quotes",
            {
                "pickup_address": json.dumps(COLETA, ensure_ascii=False),
                "dropoff_address": json.dumps(ENTREGA, ensure_ascii=False),
                "pickup_latitude": COLETA_LATLNG[0],
                "pickup_longitude": COLETA_LATLNG[1],
                "dropoff_latitude": ENTREGA_LATLNG[0],
                "dropoff_longitude": ENTREGA_LATLNG[1],
            },
        )
        self._post(
            "cotação: fora de alcance (Osasco -> Rio)",
            "/delivery_quotes",
            {
                "pickup_address": json.dumps(COLETA, ensure_ascii=False),
                "dropoff_address": json.dumps(LONGE, ensure_ascii=False),
            },
        )
        if status == 200 and isinstance(estruturada, dict):
            return str(estruturada.get("id") or "") or None
        return None

    def _corpo_entrega(self, quote_id: str | None, referencia: str, **robo: Any) -> dict[str, Any]:
        corpo: dict[str, Any] = {
            "pickup_name": "Loja Teste MuhBianco",
            "pickup_business_name": "Loja Teste MuhBianco",
            "pickup_address": json.dumps(COLETA, ensure_ascii=False),
            "pickup_phone_number": "+5511900000001",
            "dropoff_name": "Cliente Teste",
            "dropoff_address": json.dumps(ENTREGA, ensure_ascii=False),
            "dropoff_phone_number": "+5511900000002",
            "dropoff_notes": "Sonda de integração — entrega simulada.",
            "manifest_items": [
                {"name": "Pedido de teste", "quantity": 1, "size": "small", "price": 5000}
            ],
            "manifest_reference": referencia,
            "manifest_total_value": 5000,
            "external_id": referencia,
            "external_store_id": "sonda-mucommerce",
            "idempotency_key": referencia,
            "test_specifications": {"robo_courier_specification": {"mode": "auto", **robo}},
        }
        if quote_id:
            corpo["quote_id"] = quote_id
        return corpo

    def _real(self, dados: Any) -> bool:
        return isinstance(dados, dict) and dados.get("live_mode") is True

    def entrega_completa(self, quote_id: str | None) -> bool:
        """Devolve False quando a Uber disse que as chaves são de produção (nada foi criado)."""
        referencia = f"sonda-{int(time.time())}"
        status, dados = self._post(
            "criar entrega (entregador simulado)",
            "/deliveries",
            self._corpo_entrega(quote_id, referencia),
        )
        if status not in (200, 201) or not isinstance(dados, dict) or not dados.get("id"):
            if "production" in json.dumps(dados).lower():
                print("A Uber diz que estas chaves são de PRODUÇÃO: nada foi criado. Sonda para.")
                return False
            return True
        entrega = str(dados["id"])
        if self._real(dados):
            self._post("CANCELAR: veio em modo real", f"/deliveries/{entrega}/cancel", {})
            sys.exit("A entrega veio em modo REAL (live_mode=true). Cancelada; sonda interrompida.")
        # O entregador simulado anda a cada ~30 s; acompanha até entregar (ou ~6 min).
        vistos: list[str] = [str(dados.get("status") or "")]
        for _ in range(24):
            time.sleep(15)
            _, atual = self._get("consultar entrega", f"/deliveries/{entrega}")
            situacao = str((atual or {}).get("status") or "") if isinstance(atual, dict) else ""
            if situacao and situacao != vistos[-1]:
                vistos.append(situacao)
                print(f"    status: {situacao}")
            if situacao in {"delivered", "canceled", "returned"}:
                break
        self.registro.append({"passo": "sequência de estados", "estados": vistos})
        return True

    def entrega_cancelada(self, quote_id: str | None) -> None:
        referencia = f"sonda-cancel-{int(time.time())}"
        status, dados = self._post(
            "criar entrega para cancelar", "/deliveries", self._corpo_entrega(None, referencia)
        )
        if status in (200, 201) and isinstance(dados, dict) and dados.get("id"):
            self._post("cancelar entrega", f"/deliveries/{dados['id']}/cancel", {})
            self._get("consultar entrega cancelada", f"/deliveries/{dados['id']}")
        # Idempotência: a mesma chave de novo não pode virar outra entrega.
        status2, dados2 = self._post(
            "repetir a criação com a mesma chave",
            "/deliveries",
            self._corpo_entrega(None, referencia),
        )
        if status2 in (200, 201) and isinstance(dados2, dict) and dados2.get("id"):
            mesma = isinstance(dados, dict) and dados2.get("id") == dados.get("id")
            self.registro.append({"passo": "idempotência", "mesma_entrega": mesma})
            if not mesma:
                self._post("cancelar a duplicada", f"/deliveries/{dados2['id']}/cancel", {})

    def lista(self) -> None:
        self._get("listar entregas", "/deliveries?limit=2")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--out", default="probe-uber.json")
    parser.add_argument(
        "--entrega-teste", action="store_true", help="cria entregas com o entregador simulado"
    )
    parser.add_argument("--sandbox", action="store_true", help="usa as chaves de teste (-sandbox)")
    args = parser.parse_args()
    if args.entrega_teste and not args.sandbox:
        sys.exit("--entrega-teste só com --sandbox: com chave de produção a sonda não cria nada.")

    fim = "-sandbox" if args.sandbox else ""
    sonda = Sonda(
        _secret("UBER_DIRECT_CUSTOMER_ID_FILE", f"uber-direct-customer-id{fim}"),
        _secret("UBER_DIRECT_CLIENT_ID_FILE", f"uber-direct-client-id{fim}"),
        _secret("UBER_DIRECT_CLIENT_SECRET_FILE", f"uber-direct-client-secret{fim}"),
    )
    try:
        if not sonda.token():
            print("Token recusado; veja o arquivo de saída.")
        else:
            quote_id = sonda.cotacoes()
            if args.entrega_teste and sonda.entrega_completa(quote_id):
                sonda.entrega_cancelada(quote_id)
                sonda.lista()
    finally:
        Path(args.out).write_text(
            json.dumps(sonda.registro, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Saída em {args.out}")


if __name__ == "__main__":
    main()
