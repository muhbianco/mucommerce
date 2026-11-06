"""O motor de identidade visual (docs/16), com a SGPipas como caso de prova.

A reclamação era "praticamente nada mudou". O diagnóstico achou um modelo cego (nenhuma imagem
no pedido), uma paleta que nunca foi extraída e, se fosse, escolheria o fundo escuro, o emblema
virando foto de destaque, fotos de produto fora do alcance, a marca fora do esquema de saída e
um publicar sem volta. Cada um desses vira um teste aqui, além do fluxo inteiro:

    material (logo escuro com ciano e amarelo, emblema sem papel, fotos de produto)
      → leitura da marca com visão (só o material novo)
      → proposta que muda marca, áreas e home
      → validação (emblema fora do hero, cor de fábrica recusada)
      → publicar guardando o anterior → restaurar.

As imagens são sintéticas, com as proporções medidas no emblema real da SGPipas: ~70% de
azul-noite, contorno ciano, traço amarelo.
"""

from __future__ import annotations

import io
import json
from typing import Any, get_args

import pytest
from httpx import AsyncClient
from PIL import Image, ImageDraw
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.storage import FakeStorage
from app.landing.brand import (
    MAX_VISION_IMAGES,
    BrandProfileV1,
    Material,
    PaletteProfile,
    brief_digest,
    deterministic_profile,
    plan_refresh,
)
from app.landing.color import _hue, brand_accents, extract_palette
from app.landing.design import AI_BRANDING_KEYS, BrandingProposal, DesignV1
from app.landing.design_rules import (
    check_brand_fidelity,
    describe_changes,
    enforce_brand_fidelity,
    filter_decisions,
    normalize,
    strip_logo_scenes,
)
from app.landing.gateway import FakeGateway, LlmUnavailableError
from app.landing.generation import generate_draft
from app.landing.inventory import usable_media
from app.landing.models import BrandProfile, DraftStatus, LandingDraft
from app.landing.prompt import build_compose_prompt
from app.landing.registry import AREAS, BLOCKS, TOKENS, catalogue_text
from app.landing.schema_export import proposal_generation_schema
from app.landing.schemas import BriefV1
from app.media.models import MediaAsset, MediaOwner, MediaStatus
from app.tenancy.context import bind_session_tenant
from app.tenancy.models import Tenant
from app.tenancy.service import Actor, TenantService
from app.tenancy.settings_schemas import BrandingV1, validate_setting
from tests.test_catalog import base, catalog_tenant, create_product, member_headers
from tests.test_landing_generation import _queued_draft, _tx, _with_brief

# ============================================================ imagens de prova


def _webp(image: Image.Image) -> bytes:
    saida = io.BytesIO()
    image.save(saida, format="WEBP", quality=90)
    return saida.getvalue()


def sgpipas_emblem() -> bytes:
    """Emblema escuro: fundo azul-noite, anel ciano neon, traço amarelo, letra branca."""
    img = Image.new("RGB", (320, 320), (4, 20, 26))
    desenho = ImageDraw.Draw(img)
    desenho.ellipse((20, 20, 300, 300), outline=(25, 211, 230), width=16)
    desenho.line((60, 250, 260, 230), fill=(245, 196, 0), width=12)
    desenho.rectangle((90, 120, 230, 170), fill=(240, 248, 250))
    return _webp(img)


def premium_logo() -> bytes:
    """Cosmético premium: papel branco, monograma ameixa, filete dourado."""
    img = Image.new("RGB", (320, 320), (252, 250, 248))
    desenho = ImageDraw.Draw(img)
    desenho.ellipse((100, 100, 220, 220), fill=(92, 38, 76))
    desenho.line((60, 260, 260, 260), fill=(201, 162, 39), width=6)
    return _webp(img)


def kite_photo() -> bytes:
    img = Image.new("RGB", (320, 320), (120, 180, 230))
    ImageDraw.Draw(img).polygon([(160, 30), (280, 160), (160, 290), (40, 160)], fill=(20, 150, 70))
    return _webp(img)


# ============================================================ cor


class TestCorDaMarca:
    def test_o_acento_vivo_ganha_do_fundo_escuro(self) -> None:
        """O extrator antigo devolvia `#042229` para o emblema da SGPipas: o fundo. A marca é o
        ciano e o amarelo."""
        paleta = extract_palette(sgpipas_emblem())
        assert paleta is not None
        assert 160 <= _hue(_rgb(paleta.primary)) <= 210, paleta.primary  # família do ciano
        assert paleta.secondary is not None
        assert 35 <= _hue(_rgb(paleta.secondary)) <= 65, paleta.secondary  # família do amarelo

    def test_a_cor_do_perfil_nao_e_escurecida(self) -> None:
        """Escurecer é para sugestão de texto. O tema deriva a tinta; a marca fica neon."""
        acentos = brand_accents(sgpipas_emblem())
        assert acentos
        r, g, b = _rgb(acentos[0])
        assert max(r, g, b) >= 180, acentos[0]


# ============================================================ perfil incremental


def _material(media_id: str, owner: str = "landing", checksum: str = "a", **extra: Any) -> Material:
    return Material(
        media_id=media_id,
        owner=owner,
        role=None,
        alt=None,
        filename=None,
        checksum=checksum,
        **extra,
    )


def _id(n: int) -> str:
    return f"00000000-0000-7000-8000-{n:012d}"


class TestPlanoDeLeitura:
    def test_sem_perfil_le_tudo_com_a_marca_primeiro(self) -> None:
        plano = plan_refresh(
            stored_materials={},
            materials=[_material(_id(2), "product"), _material(_id(1), "tenant_brand")],
            digest="x",
            stored_digest=None,
            has_profile=False,
        )
        assert not plano.reuse
        assert plano.analyse == [_id(1), _id(2)], "logotipo antes da foto de produto"

    def test_nada_novo_nao_chama_a_visao(self) -> None:
        guardado = {_id(1): {"checksum": "a"}}
        plano = plan_refresh(
            stored_materials=guardado,
            materials=[_material(_id(1))],
            digest="x",
            stored_digest="x",
            has_profile=True,
        )
        assert plano.reuse

    def test_so_o_material_novo_vai_ao_modelo(self) -> None:
        guardado = {_id(1): {"checksum": "a"}}
        plano = plan_refresh(
            stored_materials=guardado,
            materials=[_material(_id(1)), _material(_id(2))],
            digest="x",
            stored_digest="x",
            has_profile=True,
        )
        assert plano.analyse == [_id(2)]

    def test_arquivo_trocado_no_mesmo_lugar_e_relido(self) -> None:
        plano = plan_refresh(
            stored_materials={_id(1): {"checksum": "a"}},
            materials=[_material(_id(1), checksum="b")],
            digest="x",
            stored_digest="x",
            has_profile=True,
        )
        assert plano.analyse == [_id(1)]

    def test_brief_novo_revisa_mesmo_sem_arquivo_novo(self) -> None:
        plano = plan_refresh(
            stored_materials={_id(1): {"checksum": "a"}},
            materials=[_material(_id(1))],
            digest="novo",
            stored_digest="velho",
            has_profile=True,
        )
        assert not plano.reuse and plano.brief_changed and plano.analyse == []

    def test_apagado_sai_e_excesso_fica_para_a_proxima(self) -> None:
        novos = [_material(_id(n)) for n in range(2, 2 + MAX_VISION_IMAGES + 3)]
        plano = plan_refresh(
            stored_materials={_id(1): {"checksum": "a"}},
            materials=novos,
            digest="x",
            stored_digest="x",
            has_profile=True,
        )
        assert plano.removed == [_id(1)]
        assert len(plano.analyse) == MAX_VISION_IMAGES
        assert len(plano.pending) == 3


SGPIPAS_BRIEF = BriefV1(
    segment="outro",
    segment_other="Pipas e acessórios",
    sells="Pipas, rabiolas, linhas e carretilhas",
    voice="divertido",
    keywords=["Pipas", "Ofertas"],
    references=["Utilizar cores vibrantes, imagens atrativas e botões de destaque."],
)
PREMIUM_BRIEF = BriefV1(
    segment="beleza_cosmeticos", sells="Séruns e óleos faciais artesanais", voice="elegante"
)


def _with_colors(material: Material, data: bytes) -> Material:
    from dataclasses import replace

    from app.landing.color import extract_swatches

    return replace(
        material,
        swatches=tuple(s.as_dict() for s in extract_swatches(data)),
        accents=tuple(brand_accents(data)),
    )


class TestPerfilSemModelo:
    def test_duas_marcas_opostas_nao_saem_iguais(self) -> None:
        """O critério final do pedido: loja esportiva agressiva e cosmético premium não podem
        ser o mesmo modelo com outra cor — nem no perfil de reserva, sem modelo."""
        pipas = deterministic_profile(
            SGPIPAS_BRIEF,
            materials=[_with_colors(_material(_id(1), "tenant_brand"), sgpipas_emblem())],
        )
        beleza = deterministic_profile(
            PREMIUM_BRIEF,
            materials=[_with_colors(_material(_id(2), "tenant_brand"), premium_logo())],
        )
        assert pipas.palette.surface == "dark", "emblema dominado por azul-noite"
        assert beleza.palette.surface == "light"
        assert pipas.typography.headings == "display" and pipas.typography.case == "upper"
        assert beleza.typography.headings == "elegant" and beleza.typography.case == "normal"
        assert pipas.shapes.depth == "glow"
        assert beleza.shapes.depth == "none"
        assert pipas.personality.price != "premium"
        assert beleza.personality.price == "premium"
        assert pipas.palette.primary != beleza.palette.primary

    def test_nao_finge_ter_visto(self) -> None:
        perfil = deterministic_profile(SGPIPAS_BRIEF, materials=[])
        assert perfil.confidence == "baixa"
        assert perfil.inferred, "o que foi deduzido fica dito"


# ============================================================ o que pode virar foto


class TestFotoDeBloco:
    def test_material_sem_papel_nao_vira_mais_hero(self) -> None:
        """O emblema da SGPipas subiu como material da vitrine sem papel e virou o hero duas
        vezes."""
        itens = [{"id": "a", "role": None, "owner": "landing"}]
        assert usable_media(itens) == []

    def test_a_leitura_da_marca_decide(self) -> None:
        itens = [
            {"id": "a", "role": None, "owner": "landing", "kind": "emblem"},
            {"id": "b", "role": None, "owner": "landing", "kind": "lifestyle"},
            {"id": "c", "role": "banner", "owner": "landing", "kind": "logo"},
        ]
        assert [i["id"] for i in usable_media(itens)] == ["b"]

    def test_foto_de_produto_entra_e_marca_nunca(self) -> None:
        itens = [
            {"id": "p", "role": None, "owner": "product"},
            {"id": "m", "role": "banner", "owner": "tenant_brand"},
        ]
        assert [i["id"] for i in usable_media(itens)] == ["p"]

    def test_emblema_sai_do_bloco_e_o_bloco_fica(self) -> None:
        blocos, mexeu = strip_logo_scenes(
            [
                {"type": "hero", "title": "Oi", "media_id": "e", "variant": "image_background"},
                {"type": "gallery", "media_ids": ["e", "p"]},
            ],
            logo_ids={"e"},
        )
        assert mexeu
        assert "media_id" not in blocos[0] and blocos[0]["variant"] == "text_only"
        assert blocos[1]["media_ids"] == ["p"]


# ============================================================ regras da proposta


class TestRegrasDaProposta:
    def test_a_ia_nao_troca_o_logotipo(self) -> None:
        proposta = normalize(
            {"branding": {"logo_media_id": _id(9), "primary_color": "#19d3e6"}},
            current_branding={},
            current_design={},
        )
        assert proposta.branding == {"primary_color": "#19d3e6"}
        assert any(f.field == "branding.logo_media_id" for f in proposta.findings)

    def test_valor_invalido_cai_sozinho_e_o_resto_fica(self) -> None:
        proposta = normalize(
            {
                "branding": {"primary_color": "ciano", "radius": "square"},
                "design": {"header": {"layout": "flutuante"}, "catalog": {"card": "bold"}},
            },
            current_branding={},
            current_design={},
        )
        assert proposta.branding == {"radius": "square"}
        assert proposta.design["header"] == DesignV1().header.model_dump()
        assert proposta.design["catalog"]["card"] == "bold"

    def test_brilho_so_no_escuro(self) -> None:
        proposta = normalize(
            {"branding": {"shadow": "glow", "surface": "light"}},
            current_branding={},
            current_design={},
        )
        assert proposta.branding["shadow"] == "strong"

    def test_fonte_elegante_nao_grita(self) -> None:
        proposta = normalize(
            {"branding": {"heading_font": "elegant", "heading_case": "upper"}},
            current_branding={},
            current_design={},
        )
        assert proposta.branding["heading_case"] == "normal"

    def test_cor_de_fabrica_numa_marca_com_cor_e_erro_e_depois_conserto(self) -> None:
        """A SGPipas recebeu `#111111` com um emblema ciano e amarelo."""
        perfil = BrandProfileV1(palette=PaletteProfile(primary="#19d3e6", secondary="#f5c400"))
        proposta = normalize(
            {"branding": {}}, current_branding={"primary_color": "#111111"}, current_design={}
        )
        check_brand_fidelity(
            proposta, profile=perfil, current_branding={"primary_color": "#111111"}
        )
        assert [f.field for f in proposta.errors] == ["branding.primary_color"]
        enforce_brand_fidelity(proposta, profile=perfil)
        assert not proposta.errors
        assert proposta.branding["primary_color"] == "#19d3e6"
        assert proposta.branding["secondary_color"] == "#f5c400"

    def test_a_explicacao_so_fala_do_que_muda(self) -> None:
        atual = {"branding": {"primary_color": "#111111"}, "design": {}, "landing": {"blocks": []}}
        mudancas = describe_changes(
            current=atual,
            branding={"primary_color": "#19d3e6", "heading_font": "display"},
            design={"header": {"layout": "brand_bar", "nav": "strip"}},
            blocks=None,
        )
        rotulos = {m["label"]: (m["before"], m["after"]) for m in mudancas}
        assert rotulos["Cor principal"] == ("#111111", "#19d3e6")
        assert rotulos["Fonte dos títulos"][1] == "condensada forte"
        assert rotulos["Cabeçalho"] == ("clássico", "na cor da marca")
        decisoes = filter_decisions(
            [
                {"area": "marca", "why": "cores do emblema"},
                {"area": "checkout", "why": "checkout neutro"},  # não mudou: sai
            ],
            mudancas,
        )
        assert [d["area"] for d in decisoes] == ["marca"]


# ============================================================ catálogo e esquema


def test_campo_ausente_na_marca_antiga_nao_vira_mudanca() -> None:
    """Marca gravada antes do motor não tem `heading_case`; propor o padrão não é mudança."""
    mudancas = describe_changes(
        current={"branding": {"primary_color": "#111111"}, "design": {}, "landing": {}},
        branding={"heading_case": "normal", "shadow": "soft", "button_style": "solid"},
        design={},
        blocks=None,
    )
    assert mudancas == []


class TestCatalogo:
    def test_todo_valor_do_motor_esta_no_catalogo(self) -> None:
        """Opção que existe no motor e não está no catálogo é opção que o modelo nunca escolhe."""
        for area, modelo in DesignV1.model_fields.items():
            for campo, info in modelo.annotation.model_fields.items():  # type: ignore[union-attr]
                valores = set(get_args(info.annotation))
                assert valores == set(AREAS[area][campo].options), f"{area}.{campo}"
        for chave in AI_BRANDING_KEYS:
            if chave.endswith("_color"):
                continue
            anotacao = BrandingProposal.model_fields[chave].annotation
            literal = next(a for a in get_args(anotacao) if get_args(a))
            assert set(get_args(literal)) == set(TOKENS[chave].options), chave
        assert set(BLOCKS) >= {"hero", "featured_products", "gallery", "cta"}

    def test_a_marca_e_a_proposta_aceitam_os_mesmos_valores(self) -> None:
        for chave in AI_BRANDING_KEYS:
            if chave.endswith("_color"):
                continue
            for valor in TOKENS[chave].options:
                validate_setting("branding", {chave: valor})

    def test_o_esquema_nao_vai_duas_vezes(self) -> None:
        """Ia no texto e de novo pela api-agents: cinco mil tokens repetidos por proposta."""
        mensagens = build_compose_prompt(
            brief=SGPIPAS_BRIEF,
            inventory={"store_name": "SG Pipas", "catalog": True, "media": []},
            profile=None,
            current={"branding": {}, "design": {}, "landing": {}},
        )
        texto = " ".join(str(m["content"]) for m in mensagens)
        assert "$defs" not in texto and '"properties"' not in texto
        assert "brand_bar" in texto, "o catálogo vai em texto curto"
        assert len(catalogue_text()) < 6000

    def test_o_esquema_da_proposta_nao_pede_id(self) -> None:
        esquema = json.dumps(proposal_generation_schema())
        assert '"id"' not in esquema
        assert "brand_bar" in esquema and "primary_color" in esquema


# ============================================================ o fluxo inteiro


async def _media(
    session_factory: async_sessionmaker[AsyncSession],
    storage: FakeStorage,
    tenant: Tenant,
    data: bytes,
    *,
    owner: MediaOwner,
    owner_id: str | None = None,
    filename: str = "x.jpeg",
) -> str:
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        media = MediaAsset(
            tenant_id=tenant.id,
            owner_type=owner,
            owner_id=owner_id,
            status=MediaStatus.READY,
            role=None,
            alt=None,
            filename=filename,
            upload_key=f"incoming/{tenant.id}/{filename}",
            declared_mime="image/jpeg",
            declared_bytes=len(data),
            checksum_sha256=f"{len(data):064d}",
            renditions={
                "w320": {"key": f"t/{tenant.id}/{filename}.webp", "width": 320, "height": 320}
            },
            created_by_actor="system:tests",
            updated_by_actor="system:tests",
        )
        session.add(media)
        await session.commit()
        storage.upload(
            settings.storage_public_bucket, f"t/{tenant.id}/{filename}.webp", data, "image/webp"
        )
        return media.id


async def _sgpipas(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    storage: FakeStorage,
    slug: str,
) -> dict[str, Any]:
    """Uma loja montada como a SGPipas estava em 05/11: logotipo de marca sem paleta, emblema
    enviado como material da vitrine sem papel, produto publicado com foto, brief completo."""
    tenant = await catalog_tenant(session_factory, slug)
    headers = await member_headers(client, session_factory, tenant)
    await _with_brief(
        session_factory,
        tenant,
        voice="divertido",
        references=["Utilizar cores vibrantes, imagens atrativas e botões de destaque."],
    )
    marca = await _media(
        session_factory,
        storage,
        tenant,
        sgpipas_emblem(),
        owner=MediaOwner.TENANT_BRAND,
        filename="sg-branco.jpeg",
    )
    emblema = await _media(
        session_factory,
        storage,
        tenant,
        sgpipas_emblem(),
        owner=MediaOwner.LANDING,
        filename="logo.jpeg",
    )
    produto = await create_product(client, tenant, headers, name="Pipa Terremoto")
    foto = await _media(
        session_factory,
        storage,
        tenant,
        kite_photo(),
        owner=MediaOwner.PRODUCT,
        owner_id=produto["id"],
        filename="terremoto.jpeg",
    )
    publicado = await client.post(
        f"{base(tenant)}/products/{produto['id']}/publish", headers=headers
    )
    assert publicado.status_code == 200, publicado.text
    async with session_factory() as session:
        service = TenantService(session)
        linha = await service.get_or_404(tenant.id)
        await service.set_setting(
            linha,
            "branding",
            {"primary_color": "#111111", "logo_media_id": marca},
            Actor.system("tests"),
        )
        await session.commit()
    return {
        "tenant": tenant,
        "headers": headers,
        "marca": marca,
        "emblema": emblema,
        "foto": foto,
        "produto": produto["id"],
    }


def _brand_reply(loja: dict[str, Any]) -> str:
    return json.dumps(
        {
            "materials": [
                {
                    "media_id": loja["marca"],
                    "kind": "logo",
                    "relevance": "identity",
                    "description": "logotipo SG Pipas em letra pincelada",
                },
                {
                    "media_id": loja["emblema"],
                    "kind": "emblem",
                    "relevance": "identity",
                    "description": "emblema circular azul-noite com contorno ciano neon",
                },
                {
                    "media_id": loja["foto"],
                    "kind": "product_photo",
                    "relevance": "product",
                    "description": "pipa verde em céu azul",
                    "usable_on_page": True,
                },
            ],
            "profile": {
                "summary": "Marca urbana e noturna: azul-noite, ciano neon e amarelo.",
                "palette": {"primary": "#19d3e6", "secondary": "#f5c400", "surface": "dark"},
                "typography": {"headings": "display", "case": "upper"},
                "shapes": {"corners": "square", "depth": "glow"},
                "personality": {"expression": "expressiva", "age": "jovem", "price": "popular"},
                "preserve": ["emblema circular"],
                "confidence": "alta",
            },
            "changes": ["paleta passou a vir do emblema"],
        }
    )


def _proposal_reply(loja: dict[str, Any], **branding: Any) -> str:
    marca = {
        "primary_color": "#19d3e6",
        "secondary_color": "#f5c400",
        "surface": "dark",
        "heading_font": "display",
        "heading_case": "upper",
        "radius": "square",
        "shadow": "glow",
        "button_style": "block",
        "logo_media_id": loja["emblema"],  # fora do alcance: tem de cair
    } | branding
    return json.dumps(
        {
            "summary": "Levamos o azul-noite e o ciano do emblema para a loja inteira.",
            "decisions": [
                {"area": "marca", "why": "cores e letra do emblema"},
                {"area": "cabecalho", "why": "faixa na cor da marca"},
                {"area": "rodape", "why": "não mudou nada aqui"},
            ],
            "branding": marca,
            "design": {
                "header": {"layout": "brand_bar", "nav": "pills"},
                "catalog": {"card": "bold", "image_ratio": "square", "columns": "normal"},
                "product": {"layout": "split", "price": "emphasis"},
                "checkout": {"style": "neutral"},
            },
            "blocks": [
                {
                    "type": "hero",
                    "title": "Pipa no céu",
                    "media_id": loja["emblema"],
                    "variant": "image_background",
                    "cta_label": "Ver pipas",
                },
                {"type": "gallery", "title": "No ar", "media_ids": [loja["foto"], loja["emblema"]]},
                {"type": "cta", "title": "Bora soltar?", "cta_label": "Ver produtos"},
            ],
        }
    )


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()


async def _profile_row(
    session_factory: async_sessionmaker[AsyncSession], tenant: Tenant
) -> BrandProfile | None:
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        return (await session.execute(select(BrandProfile))).scalar_one_or_none()


async def _draft_row(
    session_factory: async_sessionmaker[AsyncSession], tenant: Tenant, draft_id: str
) -> LandingDraft:
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        draft = await session.get(LandingDraft, draft_id)
        assert draft is not None
        return draft


class TestFluxoSGPipas:
    async def test_le_a_marca_com_visao_e_propoe_a_loja_inteira(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
        storage: FakeStorage,
    ) -> None:
        loja = await _sgpipas(client, session_factory, storage, "sg-fluxo")
        tenant = loja["tenant"]
        draft_id, _ = await _queued_draft(session_factory, tenant)
        gateway = FakeGateway(replies=[_brand_reply(loja), _proposal_reply(loja)])

        status = await generate_draft(
            _tx(session_factory), gateway, tenant_id=tenant.id, draft_id=draft_id, storage=storage
        )

        assert status == DraftStatus.READY
        # 1) a leitura da marca foi com as imagens — o que nunca tinha acontecido
        leitura = gateway.calls[0]
        assert leitura["feature"] == "brand_profile"
        partes = leitura["messages"][1]["content"]
        imagens = [p for p in partes if p.get("type") == "image_url"]
        assert len(imagens) == 3
        assert all(p["image_url"]["url"].startswith("data:image/webp;base64,") for p in imagens)
        # 2) a proposta é só texto, com o perfil, e sem o esquema repetido
        composicao = gateway.calls[1]
        assert composicao["feature"] == "landing_draft"
        texto = composicao["messages"][1]["content"]
        assert "#19d3e6" in texto and "$defs" not in texto

        draft = await _draft_row(session_factory, tenant, draft_id)
        assert draft.branding is not None and draft.branding["primary_color"] == "#19d3e6"
        assert "logo_media_id" not in draft.branding, "a IA não troca o logotipo"
        assert draft.design is not None and draft.design["header"]["layout"] == "brand_bar"
        hero, galeria = (draft.blocks or [])[0], (draft.blocks or [])[1]
        assert hero.get("media_id") is None, "o emblema não vira foto de destaque"
        assert hero["variant"] == "text_only"
        assert galeria["media_ids"] == [loja["foto"]], "a foto de produto chega à página"

        resumo = draft.summary or {}
        rotulos = {m["label"] for m in resumo["changes"]}
        assert {"Cor principal", "Fundo da loja", "Cabeçalho", "Página inicial"} <= rotulos
        assert [d["area"] for d in resumo["decisions"]] == ["marca", "cabecalho"]
        assert resumo["profile"]["source"] == "llm"

        perfil = await _profile_row(session_factory, tenant)
        assert perfil is not None and perfil.version == 1 and perfil.source == "llm"
        assert perfil.materials[loja["emblema"]]["kind"] == "emblem"
        async with session_factory() as session:
            bind_session_tenant(session, tenant.id)
            logo = await session.get(MediaAsset, loja["marca"])
            assert logo is not None and logo.palette, "o backfill da paleta que faltava"

    async def test_sem_material_novo_nao_paga_visao_de_novo(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
        storage: FakeStorage,
    ) -> None:
        loja = await _sgpipas(client, session_factory, storage, "sg-cache")
        tenant = loja["tenant"]
        primeira, _ = await _queued_draft(session_factory, tenant)
        await generate_draft(
            _tx(session_factory),
            FakeGateway(replies=[_brand_reply(loja), _proposal_reply(loja)]),
            tenant_id=tenant.id,
            draft_id=primeira,
            storage=storage,
        )
        async with session_factory() as session:
            bind_session_tenant(session, tenant.id)
            draft = await session.get(LandingDraft, primeira)
            assert draft is not None
            draft.status = DraftStatus.DISCARDED
            await session.commit()

        segunda, _ = await _queued_draft(session_factory, tenant)
        gateway = FakeGateway(replies=[_proposal_reply(loja)])
        await generate_draft(
            _tx(session_factory), gateway, tenant_id=tenant.id, draft_id=segunda, storage=storage
        )
        assert [c["feature"] for c in gateway.calls] == ["landing_draft"]
        perfil = await _profile_row(session_factory, tenant)
        assert perfil is not None and perfil.version == 1, "perfil reaproveitado, não revisto"

        # Material novo: só ele vai à visão.
        novo = await _media(
            session_factory,
            storage,
            tenant,
            kite_photo(),
            owner=MediaOwner.LANDING,
            filename="banner-novo.jpeg",
        )
        async with session_factory() as session:
            bind_session_tenant(session, tenant.id)
            draft = await session.get(LandingDraft, segunda)
            assert draft is not None
            draft.status = DraftStatus.DISCARDED
            await session.commit()
        terceira, _ = await _queued_draft(session_factory, tenant)
        gateway = FakeGateway(replies=[_brand_reply(loja), _proposal_reply(loja)])
        await generate_draft(
            _tx(session_factory), gateway, tenant_id=tenant.id, draft_id=terceira, storage=storage
        )
        assert gateway.calls[0]["feature"] == "brand_profile"
        imagens = [
            p for p in gateway.calls[0]["messages"][1]["content"] if p.get("type") == "image_url"
        ]
        assert len(imagens) == 1
        assert novo in gateway.calls[0]["messages"][1]["content"][-3]["text"]

    async def test_visao_fora_do_ar_nao_derruba_a_proposta(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
        storage: FakeStorage,
    ) -> None:
        """A janela entre os deploys: a api-agents ainda não conhece `brand_profile`."""
        loja = await _sgpipas(client, session_factory, storage, "sg-sem-visao")
        tenant = loja["tenant"]
        draft_id, _ = await _queued_draft(session_factory, tenant)
        gateway = FakeGateway(
            replies=[LlmUnavailableError("Uso de modelo não reconhecido."), _proposal_reply(loja)]
        )
        status = await generate_draft(
            _tx(session_factory), gateway, tenant_id=tenant.id, draft_id=draft_id, storage=storage
        )
        assert status == DraftStatus.READY
        draft = await _draft_row(session_factory, tenant, draft_id)
        assert (draft.summary or {})["profile"]["note"]
        perfil = await _profile_row(session_factory, tenant)
        assert perfil is not None and perfil.source == "deterministic"
        assert perfil.data["palette"]["surface"] == "dark", "o Pillow viu o emblema escuro"
        # Sem a leitura, o emblema sem papel fica como "outro" — e não vira hero.
        assert perfil.materials[loja["emblema"]]["kind"] == "other"

    async def test_cor_de_fabrica_volta_ao_modelo_e_depois_e_consertada(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
        storage: FakeStorage,
    ) -> None:
        loja = await _sgpipas(client, session_factory, storage, "sg-fiel")
        tenant = loja["tenant"]
        draft_id, _ = await _queued_draft(session_factory, tenant)
        teimosa = _proposal_reply(loja, primary_color="#111111", secondary_color=None)
        gateway = FakeGateway(replies=[_brand_reply(loja), teimosa, teimosa])
        status = await generate_draft(
            _tx(session_factory), gateway, tenant_id=tenant.id, draft_id=draft_id, storage=storage
        )
        assert status == DraftStatus.READY
        assert len(gateway.calls) == 3
        assert "cor de fábrica" in gateway.calls[2]["messages"][-1]["content"]
        draft = await _draft_row(session_factory, tenant, draft_id)
        assert (draft.branding or {})["primary_color"] == "#19d3e6"


class TestPublicarERestaurar:
    async def test_publica_as_tres_chaves_e_restaura_o_que_estava(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
        storage: FakeStorage,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "landing_llm_enabled", True)
        loja = await _sgpipas(client, session_factory, storage, "sg-publica")
        tenant, headers = loja["tenant"], loja["headers"]
        draft_id, _ = await _queued_draft(session_factory, tenant)
        await generate_draft(
            _tx(session_factory),
            FakeGateway(replies=[_brand_reply(loja), _proposal_reply(loja)]),
            tenant_id=tenant.id,
            draft_id=draft_id,
            storage=storage,
        )
        antes = (await client.get(f"{base(tenant)}/settings", headers=headers)).json()

        aplicar = await client.post(
            f"{base(tenant)}/landing/drafts/{draft_id}/apply", headers=headers
        )
        assert aplicar.status_code == 200, aplicar.text
        agora = (await client.get(f"{base(tenant)}/settings", headers=headers)).json()
        assert agora["branding"]["primary_color"] == "#19d3e6"
        assert agora["branding"]["surface"] == "dark"
        assert agora["branding"]["logo_media_id"] == loja["marca"], "o logotipo é da lojista"
        assert agora["design"]["header"]["layout"] == "brand_bar"
        assert agora["design"]["checkout"]["style"] == "neutral"

        lista = (await client.get(f"{base(tenant)}/landing/drafts", headers=headers)).json()
        publicada = next(d for d in lista["drafts"] if d["id"] == draft_id)
        assert publicada["revertible"] is True

        restaurar = await client.post(
            f"{base(tenant)}/landing/drafts/{draft_id}/revert", headers=headers
        )
        assert restaurar.status_code == 200, restaurar.text
        assert restaurar.json()["status"] == "reverted"
        depois = (await client.get(f"{base(tenant)}/settings", headers=headers)).json()
        assert depois["branding"]["primary_color"] == antes["branding"]["primary_color"]
        assert depois["branding"]["logo_media_id"] == loja["marca"]
        assert depois["landing"] == antes["landing"]
        assert depois["design"] == DesignV1().model_dump(mode="json")

        # Restaurar duas vezes não existe: ela já não é a publicada.
        de_novo = await client.post(
            f"{base(tenant)}/landing/drafts/{draft_id}/revert", headers=headers
        )
        assert de_novo.status_code == 409

    async def test_edicao_a_mao_depois_da_proposta_e_avisada(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
        storage: FakeStorage,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A SGPipas editou a home às 15:57 e publicou por cima às 16:45 sem saber."""
        monkeypatch.setattr(settings, "landing_llm_enabled", True)
        loja = await _sgpipas(client, session_factory, storage, "sg-aviso")
        tenant, headers = loja["tenant"], loja["headers"]
        draft_id, _ = await _queued_draft(session_factory, tenant)
        await generate_draft(
            _tx(session_factory),
            FakeGateway(replies=[_brand_reply(loja), _proposal_reply(loja)]),
            tenant_id=tenant.id,
            draft_id=draft_id,
            storage=storage,
        )
        manual = await client.put(
            f"{base(tenant)}/settings/landing",
            headers=headers,
            json={"value": {"blocks": [{"type": "hero", "title": "COMPRE AGORA"}]}},
        )
        assert manual.status_code == 200, manual.text
        lista = (await client.get(f"{base(tenant)}/landing/drafts", headers=headers)).json()
        assert next(d for d in lista["drafts"] if d["id"] == draft_id)["edited_since"] is True

    async def test_perfil_da_marca_na_tela_e_releitura(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
        storage: FakeStorage,
    ) -> None:
        loja = await _sgpipas(client, session_factory, storage, "sg-perfil")
        tenant, headers = loja["tenant"], loja["headers"]
        vazio = (await client.get(f"{base(tenant)}/landing/brand", headers=headers)).json()
        assert vazio["profile"] is None

        draft_id, _ = await _queued_draft(session_factory, tenant)
        await generate_draft(
            _tx(session_factory),
            FakeGateway(replies=[_brand_reply(loja), _proposal_reply(loja)]),
            tenant_id=tenant.id,
            draft_id=draft_id,
            storage=storage,
        )
        perfil = (await client.get(f"{base(tenant)}/landing/brand", headers=headers)).json()
        assert perfil["profile"]["palette"]["primary"] == "#19d3e6"
        assert perfil["source"] == "llm"
        kinds = {m["media_id"]: m["kind"] for m in perfil["materials"]}
        assert kinds[loja["emblema"]] == "emblem"

        reler = await client.post(f"{base(tenant)}/landing/brand/reset", headers=headers)
        assert reler.status_code == 204
        depois = (await client.get(f"{base(tenant)}/landing/brand", headers=headers)).json()
        assert depois["materials"] == [] and depois["profile"] is not None


async def test_uma_loja_nao_ve_o_perfil_da_outra(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    storage: FakeStorage,
) -> None:
    loja = await _sgpipas(client, session_factory, storage, "sg-isola-a")
    tenant = loja["tenant"]
    draft_id, _ = await _queued_draft(session_factory, tenant)
    await generate_draft(
        _tx(session_factory),
        FakeGateway(replies=[_brand_reply(loja), _proposal_reply(loja)]),
        tenant_id=tenant.id,
        draft_id=draft_id,
        storage=storage,
    )
    outra = await catalog_tenant(session_factory, "sg-isola-b")
    vizinha = await member_headers(client, session_factory, outra)
    resposta = (await client.get(f"{base(outra)}/landing/brand", headers=vizinha)).json()
    assert resposta["profile"] is None and resposta["materials"] == []


def test_loja_sem_proposta_continua_com_o_desenho_de_antes() -> None:
    """Compatibilidade: os padrões novos são o desenho de hoje — nenhuma loja muda sozinha."""
    assert BrandingV1().model_dump()["shadow"] == "soft"
    assert BrandingV1().model_dump()["button_style"] == "solid"
    assert BrandingV1().model_dump()["heading_case"] == "normal"
    padrao = DesignV1().model_dump()
    assert padrao["header"]["layout"] == "classic"
    assert padrao["catalog"]["card"] == "standard"
    assert padrao["checkout"]["style"] == "branded"
    # Uma marca gravada antes do motor (só cor, fonte e logo) continua validando.
    validate_setting(
        "branding",
        {
            "primary_color": "#111111",
            "secondary_color": "#111111",
            "font": "system",
            "logo_url": None,
        },
    )


def test_o_digest_do_brief_muda_com_o_brief() -> None:
    assert brief_digest(SGPIPAS_BRIEF) != brief_digest(PREMIUM_BRIEF)
    assert brief_digest(SGPIPAS_BRIEF) == brief_digest(
        BriefV1.model_validate(SGPIPAS_BRIEF.model_dump())
    )


def _rgb(hex_color: str) -> tuple[int, int, int]:
    v = hex_color.lstrip("#")
    return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))
