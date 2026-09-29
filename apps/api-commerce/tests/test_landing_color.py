"""Contraste e cores dominantes de um logotipo.

A tabela de `on_color` é a mesma de `apps/web/lib/theme.test.ts`. Os dois lados precisam
concordar: o servidor sugere uma cor de marca, o front a usa para pintar, e divergir aqui é a
loja receber uma sugestão que o tema depois corrige sozinho — ela escolhe uma coisa e vê outra.
"""

from __future__ import annotations

import io

import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.landing.color import (
    contrast_ratio,
    darken_until,
    extract_palette,
    on_color,
    relative_luminance,
    to_hex,
)
from app.media.models import MediaAsset, MediaOwner, MediaRole, MediaStatus
from app.tenancy.context import bind_session_tenant
from app.tenancy.models import Tenant
from tests.test_catalog import base, catalog_tenant, member_headers

#: Os mesmos casos do teste do front.
CASOS_ON_COLOR = [
    ("#111111", "#ffffff"),
    ("#ffd400", "#000000"),
    ("#2e7d32", "#ffffff"),
    ("#ffffff", "#000000"),
    ("#000000", "#ffffff"),
]


def _rgb(hexa: str) -> tuple[int, int, int]:
    return (int(hexa[1:3], 16), int(hexa[3:5], 16), int(hexa[5:7], 16))


def _png(
    *, fundo: tuple[int, int, int] | None, blocos: list[tuple[tuple[int, int, int], int]]
) -> bytes:
    """Uma imagem sintética: fundo (ou transparência) e blocos de cor de tamanho dado."""
    tamanho = 120
    modo = "RGBA"
    base = (*fundo, 255) if fundo else (0, 0, 0, 0)
    image = Image.new(modo, (tamanho, tamanho), base)
    x = 0
    for cor, largura in blocos:
        for i in range(x, min(x + largura, tamanho)):
            for j in range(tamanho):
                image.putpixel((i, j), (*cor, 255))
        x += largura
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class TestContraste:
    @pytest.mark.parametrize(("cor", "esperado"), CASOS_ON_COLOR)
    def test_a_conta_bate_com_a_do_front(self, cor: str, esperado: str) -> None:
        assert on_color(_rgb(cor)) == esperado

    def test_os_extremos_da_escala(self) -> None:
        assert contrast_ratio((0, 0, 0), (255, 255, 255)) == pytest.approx(21, abs=1e-6)
        assert contrast_ratio((119, 119, 119), (119, 119, 119)) == pytest.approx(1, abs=1e-6)
        assert relative_luminance((255, 255, 255)) == pytest.approx(1, abs=1e-6)

    def test_escurece_so_o_necessario(self) -> None:
        # Amarelo puro sobre branco dá 1,3:1; volta daqui escuro o bastante para ler.
        ajustado = darken_until(_rgb("#ffd400"), against=(255, 255, 255), ratio=4.5)
        assert contrast_ratio(ajustado, (255, 255, 255)) >= 4.5

    def test_quem_ja_tem_contraste_nao_e_mexido(self) -> None:
        preto = darken_until((0, 0, 0), against=(255, 255, 255), ratio=4.5)
        assert preto == (0, 0, 0)


class TestPaleta:
    def test_ignora_o_fundo_branco_e_devolve_o_acento(self) -> None:
        # É este descarte que faz um logotipo preto-no-branco devolver a cor da marca em vez
        # de `#000000` — que é a sugestão inútil.
        dados = _png(fundo=(255, 255, 255), blocos=[((46, 125, 50), 20)])
        paleta = extract_palette(dados)
        assert paleta is not None
        assert paleta.primary != "#ffffff"
        vermelho, verde, azul = _rgb(paleta.primary)
        assert verde > vermelho and verde > azul, paleta.primary

    def test_ignora_transparencia(self) -> None:
        dados = _png(fundo=None, blocos=[((200, 30, 40), 24)])
        paleta = extract_palette(dados)
        assert paleta is not None
        vermelho, verde, azul = _rgb(paleta.primary)
        assert vermelho > verde and vermelho > azul

    def test_a_sugestao_ja_nasce_legivel(self) -> None:
        # Sugerir uma cor que o tema vai ter de corrigir é sugerir mal.
        dados = _png(fundo=(255, 255, 255), blocos=[((255, 212, 0), 30)])
        paleta = extract_palette(dados)
        assert paleta is not None
        assert contrast_ratio(_rgb(paleta.primary), (255, 255, 255)) >= 4.5

    def test_a_segunda_cor_e_de_outra_familia(self) -> None:
        # Dois tons do mesmo azul não são duas sugestões.
        dados = _png(fundo=(255, 255, 255), blocos=[((20, 60, 200), 30), ((220, 120, 10), 30)])
        paleta = extract_palette(dados)
        assert paleta is not None
        assert paleta.secondary is not None
        assert paleta.secondary != paleta.primary

    def test_imagem_so_de_cinza_nao_sugere_nada(self) -> None:
        # Sem cor não há o que sugerir, e inventar seria pior do que ficar calado.
        dados = _png(fundo=(255, 255, 255), blocos=[((128, 128, 128), 40)])
        assert extract_palette(dados) is None

    def test_arquivo_quebrado_nao_derruba_o_processamento(self) -> None:
        # O worker de imagem segue a vida: uma paleta a menos não vale uma foto não publicada.
        assert extract_palette(b"isto nao e uma imagem") is None

    def test_o_texto_da_cor_e_hexadecimal(self) -> None:
        assert to_hex((46, 125, 50)) == "#2e7d32"
        assert to_hex((300, -5, 0)) == "#ff0000", "valores fora da faixa são presos nela"


class TestSugestoesNoPainel:
    """A paleta vira sugestão na tela, e **só** sugestão.

    Aplicar é a lojista apertando um botão: uma loja que já escolheu a cor dela não pode perdê-la
    porque trocou o logotipo.
    """

    async def test_logo_processado_aparece_como_sugestao(
        self, client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        tenant = await catalog_tenant(session_factory, "sugere")
        headers = await member_headers(client, session_factory, tenant)
        await _brand_media(
            session_factory,
            tenant,
            palette={"primary": "#2e7d32", "on_primary": "#ffffff", "secondary": "#7d3227"},
        )

        resposta = await client.get(f"{base(tenant)}/branding/suggestions", headers=headers)
        assert resposta.status_code == 200
        [sugestao] = resposta.json()
        assert sugestao["primary"] == "#2e7d32"
        # Preto ou branco vem calculado pela mesma conta do tema, nunca escolhido.
        assert sugestao["on_primary"] == "#ffffff"
        assert sugestao["secondary"] == "#7d3227"

    async def test_imagem_sem_paleta_nao_vira_sugestao(
        self, client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        # Foto de produto não gera paleta (a cor dominante de um brownie é marrom), e logotipo
        # enviado antes disto existir também não tem.
        tenant = await catalog_tenant(session_factory, "sem-paleta")
        headers = await member_headers(client, session_factory, tenant)
        await _brand_media(session_factory, tenant, palette=None)

        resposta = await client.get(f"{base(tenant)}/branding/suggestions", headers=headers)
        assert resposta.json() == []

    async def test_imagem_ainda_processando_nao_vira_sugestao(
        self, client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        tenant = await catalog_tenant(session_factory, "processando")
        headers = await member_headers(client, session_factory, tenant)
        await _brand_media(
            session_factory,
            tenant,
            palette={"primary": "#2e7d32", "on_primary": "#ffffff", "secondary": None},
            status=MediaStatus.PROCESSING,
        )
        assert (
            await client.get(f"{base(tenant)}/branding/suggestions", headers=headers)
        ).json() == []

    async def test_sugerir_nao_escreve_a_marca(
        self, client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """O teste que protege a cor que a lojista escolheu.

        Se a sugestão escrevesse sozinha, trocar o logotipo repintaria a loja inteira sem
        ninguém pedir — e ela descobriria pelo cliente.
        """
        tenant = await catalog_tenant(session_factory, "nao-escreve")
        headers = await member_headers(client, session_factory, tenant)
        await client.put(
            f"{base(tenant)}/settings/branding",
            headers=headers,
            json={"value": {"primary_color": "#111111"}},
        )
        await _brand_media(
            session_factory,
            tenant,
            palette={"primary": "#2e7d32", "on_primary": "#ffffff", "secondary": None},
        )

        await client.get(f"{base(tenant)}/branding/suggestions", headers=headers)
        atual = await client.get(f"{base(tenant)}/settings", headers=headers)
        assert atual.json()["branding"]["primary_color"] == "#111111"

    async def test_a_loja_vizinha_nao_ve_o_logo_de_ninguem(
        self, client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        uma = await catalog_tenant(session_factory, "cor-a")
        outra = await catalog_tenant(session_factory, "cor-b")
        await _brand_media(
            session_factory,
            uma,
            palette={"primary": "#2e7d32", "on_primary": "#ffffff", "secondary": None},
        )
        vizinha = await member_headers(client, session_factory, outra)
        assert (
            await client.get(f"{base(outra)}/branding/suggestions", headers=vizinha)
        ).json() == []


async def _brand_media(
    session_factory: async_sessionmaker[AsyncSession],
    tenant: Tenant,
    *,
    palette: dict[str, str | None] | None,
    status: str = MediaStatus.READY,
) -> str:
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        media = MediaAsset(
            tenant_id=tenant.id,
            owner_type=MediaOwner.TENANT_BRAND,
            owner_id=None,
            status=status,
            role=MediaRole.LOGO,
            upload_key="tenants/x/uploads/logo",
            declared_mime="image/png",
            declared_bytes=100,
            renditions={"w320": {"key": "k", "width": 320, "height": 320, "bytes": 10}},
            public_prefix="tenants/x/media/y",
            palette=palette,
            created_by_actor="system:tests",
            updated_by_actor="system:tests",
        )
        session.add(media)
        await session.commit()
        return media.id
