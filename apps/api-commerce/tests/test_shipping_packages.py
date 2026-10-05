"""Frete v2, F1: embalagens da loja, regras por produto e medidas por variação.

O que importa aqui é o que o motor vai precisar honrar depois: uma padrão por loja, medida por
dentro e por fora coerentes, e capacidade declarada que nunca deixa um produto rígido passar das
medidas físicas — nem um flexível encolher para menos da metade.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.scopes import TenantRole
from app.shipping.models import MAX_PACKAGES_PER_TENANT
from tests.shoppers import selling_store
from tests.test_catalog import base, create_product, member_headers

# Rabiola do exemplo do plano: 10 x 10 x 5 cm, 150 g. Caixa M: 30 x 20 x 15 cm por dentro.
RABIOLA = {"weight_grams": 150, "width_mm": 100, "height_mm": 50, "depth_mm": 100}
CAIXA_M = {
    "name": "Caixa M",
    "inner_length_mm": 300,
    "inner_width_mm": 200,
    "inner_height_mm": 150,
    "empty_weight_grams": 180,
}


def pkgs(tenant: Any) -> str:
    return f"{base(tenant)}/shipping/packages"


async def loja(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], slug: str = "alpha"
) -> tuple[Any, dict[str, str]]:
    tenant = await selling_store(session_factory, slug)
    return tenant, await member_headers(client, session_factory, tenant)


async def nova(
    client: AsyncClient, tenant: Any, headers: dict[str, str], **body: Any
) -> dict[str, Any]:
    resposta = await client.post(pkgs(tenant), json=CAIXA_M | body, headers=headers)
    assert resposta.status_code == 201, resposta.text
    return dict(resposta.json())


# ----------------------------------------------------------------------------- embalagens


async def test_a_primeira_embalagem_nasce_padrao_e_a_de_fora_e_derivada(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner = await loja(client, session_factory)
    m = await nova(client, tenant, owner)
    p = await nova(
        client,
        tenant,
        owner,
        name="Caixa P",
        inner_length_mm=200,
        inner_width_mm=150,
        inner_height_mm=100,
    )
    assert m["is_default"] is True
    assert p["is_default"] is False
    # Sem medida de fora, a cobrança usa a de dentro mais a parede da caixa (4 mm por lado).
    assert m["billed_outer_mm"] == [308, 208, 158]

    lista = (await client.get(pkgs(tenant), headers=owner)).json()
    assert [x["name"] for x in lista] == ["Caixa M", "Caixa P"], "a padrão vem primeiro"


async def test_nome_repetido_sem_diferenciar_maiuscula(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner = await loja(client, session_factory)
    await nova(client, tenant, owner)
    resposta = await client.post(pkgs(tenant), json=CAIXA_M | {"name": "caixa m"}, headers=owner)
    assert resposta.status_code == 409
    assert resposta.json()["error"]["code"] == "package_name_taken"


async def test_medidas_incoerentes_sao_recusadas(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner = await loja(client, session_factory)
    casos = [
        ({"kind": "tube", "inner_width_mm": 100, "inner_height_mm": 80}, "tube_diameter"),
        ({"outer_length_mm": 310}, "outer_partial"),
        (
            {"outer_length_mm": 290, "outer_width_mm": 210, "outer_height_mm": 160},
            "outer_smaller",
        ),
        ({"max_weight_grams": 150}, "weight_limit"),  # caixa vazia pesa 180 g
    ]
    for extra, motivo in casos:
        resposta = await client.post(pkgs(tenant), json=CAIXA_M | extra, headers=owner)
        assert resposta.status_code == 422, (motivo, resposta.text)
        assert resposta.json()["error"]["details"]["reason"] == motivo


async def test_teto_de_embalagens_por_loja(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner = await loja(client, session_factory)
    for i in range(MAX_PACKAGES_PER_TENANT):
        await nova(client, tenant, owner, name=f"Caixa {i}")
    resposta = await client.post(pkgs(tenant), json=CAIXA_M | {"name": "Uma a mais"}, headers=owner)
    assert resposta.status_code == 409
    assert resposta.json()["error"]["code"] == "package_limit"


async def test_padrao_nao_arquiva_e_trocar_a_padrao_e_atomico(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner = await loja(client, session_factory)
    m = await nova(client, tenant, owner)
    p = await nova(client, tenant, owner, name="Caixa P")

    arquivar = await client.patch(
        f"{pkgs(tenant)}/{m['id']}", json={"active": False}, headers=owner
    )
    assert arquivar.status_code == 409
    assert arquivar.json()["error"]["code"] == "default_package"
    apagar = await client.delete(f"{pkgs(tenant)}/{m['id']}", headers=owner)
    assert apagar.status_code == 409

    troca = await client.post(f"{pkgs(tenant)}/{p['id']}/make-default", headers=owner)
    assert troca.status_code == 200, troca.text
    lista = {
        x["name"]: x["is_default"] for x in (await client.get(pkgs(tenant), headers=owner)).json()
    }
    assert lista == {"Caixa M": False, "Caixa P": True}, "exatamente uma padrão"

    # A antiga padrão agora arquiva; e arquivada não pode voltar a ser padrão.
    assert (
        await client.patch(f"{pkgs(tenant)}/{m['id']}", json={"active": False}, headers=owner)
    ).status_code == 200
    volta = await client.post(f"{pkgs(tenant)}/{m['id']}/make-default", headers=owner)
    assert volta.status_code == 409


async def test_a_unica_padrao_sai_e_a_loja_volta_a_caixa_sob_medida(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Embalagem é opcional: a padrão, sendo a única ativa, arquiva (e perde a marca) e apaga;
    a próxima que a loja criar nasce padrão."""
    tenant, owner = await loja(client, session_factory)
    m = await nova(client, tenant, owner)

    arquivar = await client.patch(
        f"{pkgs(tenant)}/{m['id']}", json={"active": False}, headers=owner
    )
    assert arquivar.status_code == 200, arquivar.text
    assert arquivar.json()["is_default"] is False
    assert arquivar.json()["active"] is False

    p = await nova(client, tenant, owner, name="Caixa P")
    assert p["is_default"] is True, "a primeira ativa depois de ficar sem nenhuma nasce padrão"

    sozinha = await nova(client, tenant, owner, name="Só ela")
    assert sozinha["is_default"] is False
    # Com outra ativa, a padrão volta a não sair; sendo a única de novo, sai até apagando.
    assert (await client.delete(f"{pkgs(tenant)}/{p['id']}", headers=owner)).status_code == 409
    assert (
        await client.patch(f"{pkgs(tenant)}/{sozinha['id']}", json={"active": False}, headers=owner)
    ).status_code == 200
    apagar = await client.delete(f"{pkgs(tenant)}/{p['id']}", headers=owner)
    assert apagar.status_code == 204, apagar.text


async def test_apagar_so_o_que_nenhum_produto_usa(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner = await loja(client, session_factory)
    await nova(client, tenant, owner)
    p = await nova(client, tenant, owner, name="Caixa P")
    produto = await create_product(client, tenant, owner, **RABIOLA)
    regra = await client.patch(
        f"{base(tenant)}/products/{produto['id']}",
        json={"packing_mode": "restricted", "package_rules": [{"package_id": p["id"]}]},
        headers=owner,
    )
    assert regra.status_code == 200, regra.text

    usada = await client.delete(f"{pkgs(tenant)}/{p['id']}", headers=owner)
    assert usada.status_code == 409
    assert usada.json()["error"]["code"] == "package_in_use"
    impacto = (await client.get(f"{pkgs(tenant)}/{p['id']}/products", headers=owner)).json()
    assert [x["product_id"] for x in impacto] == [produto["id"]]

    solta = await nova(client, tenant, owner, name="Envelope", kind="envelope", inner_height_mm=30)
    assert (await client.delete(f"{pkgs(tenant)}/{solta['id']}", headers=owner)).status_code == 204


async def test_quem_embala_cadastra_e_o_suporte_so_le(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, _owner = await loja(client, session_factory)
    ops = await member_headers(client, session_factory, tenant, TenantRole.OPS)
    suporte = await member_headers(client, session_factory, tenant, TenantRole.SUPPORT)
    await nova(client, tenant, ops)  # ops tem catalog:write: quem embala conhece as caixas
    assert (await client.get(pkgs(tenant), headers=suporte)).status_code == 200
    negado = await client.post(pkgs(tenant), json=CAIXA_M | {"name": "X"}, headers=suporte)
    assert negado.status_code == 403


async def test_embalagem_de_outra_loja_nao_existe_aqui(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    alpha, dono_alpha = await loja(client, session_factory, "alpha")
    beta, dono_beta = await loja(client, session_factory, "beta")
    caixa_beta = await nova(client, beta, dono_beta)
    assert (
        await client.get(f"{pkgs(alpha)}/{caixa_beta['id']}", headers=dono_alpha)
    ).status_code == 404

    produto = await create_product(client, alpha, dono_alpha, **RABIOLA)
    await nova(client, alpha, dono_alpha)
    resposta = await client.patch(
        f"{base(alpha)}/products/{produto['id']}",
        json={"packing_mode": "restricted", "package_rules": [{"package_id": caixa_beta["id"]}]},
        headers=dono_alpha,
    )
    assert resposta.status_code == 422
    assert resposta.json()["error"]["details"]["reason"] == "unknown_package"


# ----------------------------------------------------------------------------- produto


async def _regra(
    client: AsyncClient,
    tenant: Any,
    headers: dict[str, str],
    product_id: str,
    package_id: str,
    max_units: int | None,
    **extra: Any,
) -> Any:
    return await client.patch(
        f"{base(tenant)}/products/{product_id}",
        json={
            "packing_mode": "restricted",
            "package_rules": [{"package_id": package_id, "max_units": max_units}],
        }
        | extra,
        headers=headers,
    )


async def test_rigido_com_capacidade_declarada_so_limita(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Rígido: "cabem 25" numa caixa onde cabem 18 é aceito, porque para ele o número só reduz
    — o motor usa min(geometria, 25) = 18. Nunca passa das medidas físicas."""
    tenant, owner = await loja(client, session_factory)
    m = await nova(client, tenant, owner)
    produto = await create_product(client, tenant, owner, **RABIOLA)
    resposta = await _regra(client, tenant, owner, produto["id"], m["id"], 25)
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["packing_mode"] == "restricted"
    assert corpo["packing_flexible"] is False
    assert corpo["package_rules"] == [{"package_id": m["id"], "max_units": 25}]


async def test_flexivel_declara_ate_o_dobro_do_espaco(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Flexível: 25 rabiolas = 12,5 L numa caixa de 9 L (139%) passa; 50 (278%) é recusado."""
    tenant, owner = await loja(client, session_factory)
    m = await nova(client, tenant, owner)
    produto = await create_product(client, tenant, owner, packing_flexible=True, **RABIOLA)

    ok = await _regra(client, tenant, owner, produto["id"], m["id"], 25)
    assert ok.status_code == 200, ok.text

    demais = await _regra(client, tenant, owner, produto["id"], m["id"], 50)
    assert demais.status_code == 422
    detalhe = demais.json()["error"]["details"]
    assert detalhe["reason"] == "too_compressed"
    assert detalhe["percent"] == 278


async def test_flexivel_tem_travas_de_encaixe_e_peso(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner = await loja(client, session_factory)
    envelope = await nova(
        client, tenant, owner, name="Envelope", kind="envelope", inner_height_mm=30
    )
    leve = await nova(client, tenant, owner, name="Leve", max_weight_grams=1000)
    produto = await create_product(client, tenant, owner, packing_flexible=True, **RABIOLA)

    # Nem uma rabiola de pé (5 cm) cabe num envelope de 3 cm... mas deitada também não: 5 > 3.
    nao_cabe = await _regra(client, tenant, owner, produto["id"], envelope["id"], 2)
    assert nao_cabe.json()["error"]["details"]["reason"] == "does_not_fit"
    # 10 x 150 g = 1,5 kg numa caixa de 1 kg com 180 g de tara.
    pesada = await _regra(client, tenant, owner, produto["id"], leve["id"], 10)
    assert pesada.json()["error"]["details"]["reason"] == "too_heavy"


async def test_desmarcar_flexivel_nao_apaga_a_declaracao(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner = await loja(client, session_factory)
    m = await nova(client, tenant, owner)
    produto = await create_product(client, tenant, owner, packing_flexible=True, **RABIOLA)
    assert (await _regra(client, tenant, owner, produto["id"], m["id"], 25)).status_code == 200
    rigido = await client.patch(
        f"{base(tenant)}/products/{produto['id']}",
        json={"packing_flexible": False},
        headers=owner,
    )
    assert rigido.status_code == 200
    assert rigido.json()["package_rules"] == [{"package_id": m["id"], "max_units": 25}]


async def test_modos_que_o_motor_nao_honraria(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner = await loja(client, session_factory)
    m = await nova(client, tenant, owner)
    sem_medida = await create_product(client, tenant, owner)
    medido = await create_product(client, tenant, owner, name="Pipa", **RABIOLA)

    restrito_vazio = await client.patch(
        f"{base(tenant)}/products/{medido['id']}",
        json={"packing_mode": "restricted", "package_rules": []},
        headers=owner,
    )
    assert restrito_vazio.json()["error"]["details"]["reason"] == "restricted_needs_packages"

    proprio = await client.patch(
        f"{base(tenant)}/products/{sem_medida['id']}",
        json={"packing_mode": "own_container"},
        headers=owner,
    )
    assert proprio.json()["error"]["details"]["reason"] == "own_container_needs_measures"

    # Com uma embalagem ativa o modo restrito passa; criar já restrito, sem regra, não.
    assert (await _regra(client, tenant, owner, medido["id"], m["id"], None)).status_code == 200
    criar_restrito = await client.post(
        f"{base(tenant)}/products",
        json={"name": "X", "base_price_cents": 100, "packing_mode": "restricted"},
        headers=owner,
    )
    assert criar_restrito.status_code == 422


async def test_editar_o_nome_nao_revalida_embalagem(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Uma declaração que ficou fora do limite (caixa encolhida depois) não trava a edição do
    nome: a validação só roda quando muda o que o motor usa. O motor aplica as travas sozinho."""
    tenant, owner = await loja(client, session_factory)
    m = await nova(client, tenant, owner)
    produto = await create_product(client, tenant, owner, packing_flexible=True, **RABIOLA)
    assert (await _regra(client, tenant, owner, produto["id"], m["id"], 30)).status_code == 200
    encolhe = await client.patch(
        f"{pkgs(tenant)}/{m['id']}", json={"inner_height_mm": 50}, headers=owner
    )
    assert encolhe.status_code == 200
    renomeia = await client.patch(
        f"{base(tenant)}/products/{produto['id']}", json={"name": "Rabiola 500 m"}, headers=owner
    )
    assert renomeia.status_code == 200, renomeia.text


async def test_caixa_legada_so_aceita_embalagem_da_loja(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner = await loja(client, session_factory)
    produto = await create_product(client, tenant, owner, **RABIOLA)
    resposta = await client.patch(
        f"{base(tenant)}/products/{produto['id']}",
        json={"shipping_box_id": "01a00000-0000-7000-8000-000000000000"},
        headers=owner,
    )
    assert resposta.status_code == 422
    caixa = await nova(client, tenant, owner)
    ok = await client.patch(
        f"{base(tenant)}/products/{produto['id']}",
        json={"shipping_box_id": caixa["id"]},
        headers=owner,
    )
    assert ok.status_code == 200, ok.text


# ----------------------------------------------------------------------------- variação


async def test_variacao_tem_medida_propria_ou_herda_as_tres(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner = await loja(client, session_factory)
    produto = await create_product(client, tenant, owner, **RABIOLA)
    variante = produto["variants"][0]
    rota = f"{base(tenant)}/products/{produto['id']}/variants/{variante['id']}"
    assert variante["weight_grams"] is None, "nasce herdando do produto"

    meia = await client.patch(rota, json={"width_mm": 120}, headers=owner)
    assert meia.status_code == 422

    inteira = await client.patch(
        rota,
        json={"weight_grams": 500, "width_mm": 120, "height_mm": 80, "depth_mm": 120},
        headers=owner,
    )
    assert inteira.status_code == 200, inteira.text
    lida = inteira.json()["variants"][0]
    assert (lida["weight_grams"], lida["width_mm"], lida["height_mm"], lida["depth_mm"]) == (
        500,
        120,
        80,
        120,
    )

    so_peso = await client.patch(
        rota, json={"width_mm": None, "height_mm": None, "depth_mm": None}, headers=owner
    )
    assert so_peso.status_code == 200
    assert so_peso.json()["variants"][0]["weight_grams"] == 500


# ----------------------------------------------------------------------------- prévia e simulador


async def _tres_caixas(client: AsyncClient, tenant: Any, owner: dict[str, str]) -> dict[str, str]:
    p = await nova(
        client,
        tenant,
        owner,
        name="Caixa P",
        inner_length_mm=200,
        inner_width_mm=150,
        inner_height_mm=100,
        empty_weight_grams=80,
    )
    m = await nova(client, tenant, owner, name="Caixa M")
    g = await nova(
        client,
        tenant,
        owner,
        name="Caixa G",
        inner_length_mm=400,
        inner_width_mm=300,
        inner_height_mm=200,
        empty_weight_grams=300,
    )
    return {"P": p["id"], "M": m["id"], "G": g["id"]}


async def test_previa_do_produto_mostra_calculada_e_declarada(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner = await loja(client, session_factory)
    ids = await _tres_caixas(client, tenant, owner)
    rota = f"{base(tenant)}/shipping/packing-preview/product"
    auto = (await client.post(rota, json=RABIOLA, headers=owner)).json()
    por_nome = {c["name"]: c for c in auto}
    assert {n: c["calculated"] for n, c in por_nome.items()} == {
        "Caixa P": 6,
        "Caixa M": 18,
        "Caixa G": 48,
    }
    assert all(c["allowed"] for c in auto), "no automático, todas as automáticas valem"

    flexivel = (
        await client.post(
            rota,
            json=RABIOLA
            | {
                "flexible": True,
                "mode": "restricted",
                "rules": [{"package_id": ids["M"], "max_units": 25}],
            },
            headers=owner,
        )
    ).json()
    m = next(c for c in flexivel if c["package_id"] == ids["M"])
    assert (m["calculated"], m["declared"], m["effective"]) == (18, 25, 25)
    assert (m["declared_check"], m["declared_percent"]) == ("over_physical", 139)
    assert [c["allowed"] for c in flexivel if c["package_id"] != ids["M"]] == [False, False]

    rigido = (
        await client.post(
            rota,
            json=RABIOLA
            | {"mode": "restricted", "rules": [{"package_id": ids["M"], "max_units": 25}]},
            headers=owner,
        )
    ).json()
    m = next(c for c in rigido if c["package_id"] == ids["M"])
    assert m["effective"] == 18, "rígido: a declaração só reduz"


async def test_previa_da_embalagem_avisa_e_diz_quanto_cabe(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner = await loja(client, session_factory)
    await create_product(client, tenant, owner, name="Rabiola", **RABIOLA)
    rota = f"{base(tenant)}/shipping/packing-preview/package"
    corpo = {k: v for k, v in CAIXA_M.items() if k != "name"}
    m = (await client.post(rota, json=corpo, headers=owner)).json()
    assert m["billed_outer_mm"] == [308, 208, 158]
    assert m["cubic_free"] is True
    assert m["warnings"] == []
    assert [(f["name"], f["units"]) for f in m["fits"]] == [("Rabiola", 18)]

    tubo = (
        await client.post(
            rota,
            json={
                "kind": "tube",
                "inner_length_mm": 800,
                "inner_width_mm": 100,
                "inner_height_mm": 100,
            },
            headers=owner,
        )
    ).json()
    assert set(tubo["warnings"]) == {"nonmech_shape", "nonmech_side"}


async def test_simulador_monta_os_planos_sem_cotar(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner = await loja(client, session_factory)
    ids = await _tres_caixas(client, tenant, owner)
    rabiola = await create_product(client, tenant, owner, name="Rabiola", **RABIOLA)
    sem_medida = await create_product(client, tenant, owner, name="Sem medida")
    servico = await create_product(client, tenant, owner, name="Montagem", kind="service")
    resposta = await client.post(
        f"{base(tenant)}/shipping/simulate",
        json={
            "lines": [
                {"variant_id": rabiola["variants"][0]["id"], "quantity_milli": 4000},
                {"variant_id": sem_medida["variants"][0]["id"], "quantity_milli": 1000},
                {"variant_id": servico["variants"][0]["id"], "quantity_milli": 1000},
            ]
        },
        headers=owner,
    )
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["missing"] == [sem_medida["variants"][0]["id"]]
    consolidar = next(p for p in corpo["plans"] if p["strategy"] == "consolidate")
    assert [v["package_id"] for v in consolidar["parcels"]] == [ids["P"]]
    volume = consolidar["parcels"][0]
    assert volume["items"] == [
        {"key": rabiola["variants"][0]["id"], "name": "Rabiola", "sku": rabiola["sku"], "units": 4}
    ]
    assert volume["gross_grams"] == 80 + 4 * 150
    assert any(p["quoted"] for p in corpo["plans"])

    demais = await client.post(
        f"{base(tenant)}/shipping/simulate",
        json={"lines": [{"variant_id": rabiola["variants"][0]["id"], "quantity_milli": 500_000}]},
        headers=owner,
    )
    assert demais.status_code == 200
    acima = await client.post(
        f"{base(tenant)}/shipping/simulate",
        json={
            "lines": [
                {"variant_id": rabiola["variants"][0]["id"], "quantity_milli": 400_000},
                {"variant_id": sem_medida["variants"][0]["id"], "quantity_milli": 200_000},
            ]
        },
        headers=owner,
    )
    assert acima.status_code == 422


async def test_vendido_a_peso_vira_pecas_inteiras_e_uma_parcial(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    from app.shipping.inputs import LineIn, load_packing_inputs
    from app.tenancy.context import bind_session_tenant
    from app.tenancy.settings_schemas import PackingSettings

    tenant, owner = await loja(client, session_factory)
    await nova(client, tenant, owner)
    queijo = await create_product(
        client,
        tenant,
        owner,
        name="Queijo",
        sold_by="weight",
        unit_label="kg",
        weight_grams=1000,
        width_mm=100,
        height_mm=100,
        depth_mm=100,
    )
    variante = queijo["variants"][0]["id"]
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        from app.models.base import utcnow

        entradas = await load_packing_inputs(
            session,
            [LineIn(variante, 2000), LineIn(variante, 500)],
            PackingSettings(),
            now=utcnow(),
        )
    pecas = {c.key: (c.units, c.weight_g) for c in entradas.classes}
    # 2,5 kg = 2 peças inteiras de 1 kg + 1 peça de 500 g (as duas linhas somam).
    assert pecas == {variante: (2, 1000), f"{variante}~500": (1, 500)}


async def test_status_do_envio_lista_o_que_falta_nas_embalagens(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant = await selling_store(session_factory)
    owner = await member_headers(client, session_factory, tenant)
    status_vazio = (await client.get(f"{base(tenant)}/shipping", headers=owner)).json()
    assert status_vazio["packing"]["has_default"] is False
    # Embalagem é opcional (sem ela, caixa sob medida): não é pendência para cotar.
    assert "config:embalagem" not in status_vazio["missing"]

    m = await nova(client, tenant, owner)
    extra = await nova(client, tenant, owner, name="Extra")
    from tests.test_pricing import product as publicado

    grande = await publicado(
        client,
        session_factory,
        tenant,
        owner,
        name="Bicicleta",
        weight_grams=12000,
        width_mm=800,
        height_mm=1500,
        depth_mm=300,
    )
    restrito = await create_product(client, tenant, owner, name="Rabiola", **RABIOLA)
    await _regra(client, tenant, owner, restrito["id"], extra["id"], None)
    await client.patch(f"{pkgs(tenant)}/{extra['id']}", json={"active": False}, headers=owner)

    status = (await client.get(f"{base(tenant)}/shipping", headers=owner)).json()
    assert status["packing"]["has_default"] is True
    assert status["has_box"] is True
    assert "config:embalagem" not in status["missing"]
    assert status["packing"]["orphans"] == [{"id": restrito["id"], "name": "Rabiola"}]
    assert status["packing"]["unfit"] == [{"id": grande["id"], "name": "Bicicleta"}]
    assert m["id"]  # a padrão segue ativa
