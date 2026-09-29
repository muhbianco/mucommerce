"""Versioned schemas for `tenant_settings` values.

Every write (ops or tenant panel) goes through `validate_setting`, so a stored value always
matches its schema: readers (storefront context, checkout) can trust the shape instead of
defending against arbitrary JSON. `schema_version` is stored next to the value; a breaking
change adds a V2 model plus a data migration, never an in-place reinterpretation.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic import ValidationError as PydanticValidationError

from app.core.exceptions import ValidationError
from app.landing.blocks import MAX_BLOCKS, LandingBlock

AccessMode = Literal["public", "login_required", "whitelist"]
HexColor = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]
HttpsUrl = Annotated[str, Field(pattern=r"^https://", max_length=500)]
# Ids of the tenant's own rows (media, products, categories): UUIDv7 strings.
MediaId = Annotated[str, Field(min_length=36, max_length=36)]


class _Setting(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StorefrontV1(_Setting):
    # `whitelist` is the safe default: nothing is shown until access is granted.
    access_mode: AccessMode = "whitelist"
    currency: Literal["BRL"] = "BRL"


class BrandingV1(_Setting):
    """A marca da loja. Campos novos entram opcionais e com padrão: o que está salvo continua
    valendo, e nenhuma loja acorda com a vitrine diferente sem ter pedido."""

    primary_color: HexColor = "#111111"
    # Accent for links and highlights; None = the primary colour.
    secondary_color: HexColor | None = None
    # Font family of the storefront (system stack, serif or rounded).
    font: Literal["system", "serif", "rounded"] = "system"
    logo_url: HttpsUrl | None = None
    # Uploaded logo (media owner `tenant_brand`); wins over `logo_url` once processed.
    logo_media_id: MediaId | None = None
    #: Quina dos cartões e botões. Enum e não número: raio de 40px num cartão é defeito, e uma
    #: lista de três não produz um.
    radius: Literal["square", "soft", "round"] = "soft"
    #: Quanto ar entre as seções. Loja com muita foto pede folga; catálogo grande pede aperto.
    density: Literal["cozy", "normal", "airy"] = "normal"
    #: Papel da vitrine. Três triplas auditadas em vez de um seletor de cor: escuro é o que mais
    #: se pede e o que mais quebra contraste quando alguém escolhe à mão.
    surface: Literal["light", "warm", "dark"] = "light"
    #: Fonte dos títulos. `inherit` usa a do corpo — que é o que quase toda loja quer.
    heading_font: Literal["inherit", "serif", "rounded"] = "inherit"
    #: Altura do logotipo no cabeçalho. Marca horizontal precisa de mais que os 40px fixos de
    #: antes; marca quadrada, de menos.
    logo_height_px: Annotated[int, Field(ge=24, le=72)] = 40


class SeoV1(_Setting):
    title: Annotated[str, Field(max_length=70)] | None = None
    description: Annotated[str, Field(max_length=160)] | None = None
    og_image_url: HttpsUrl | None = None
    og_image_media_id: MediaId | None = None
    # Search engines index the store only when the tenant opts in (and it is public).
    indexable: bool = False


# ------------------------------------------------------------------------------ landing
# Os blocos moram em `app.landing.blocks`: são doze tipos com arranjo e tom, e o arquivo tem
# regras próprias sobre alargar sem quebrar dado salvo. Aqui fica só o invólucro que entra em
# `SETTINGS_SCHEMAS`.


class LandingV1(_Setting):
    blocks: Annotated[list[LandingBlock], Field(max_length=MAX_BLOCKS)] = []


Cents = Annotated[int, Field(ge=0, le=100_000_000)]
FulfillmentMode = Literal["pickup", "delivery", "shipping"]
SettingId = Annotated[str, Field(min_length=1, max_length=36)]
Cep = Annotated[str, Field(pattern=r"^\d{8}$")]
ClockTime = Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]
PlaceName = Annotated[str, Field(min_length=1, max_length=80)]


class PickupLocation(_Setting):
    # Ids are assigned on write (settings_normalizers) and stay the same across edits.
    id: SettingId
    name: PlaceName
    address: Annotated[str, Field(min_length=1, max_length=300)]
    instructions: Annotated[str, Field(max_length=300)] | None = None
    active: bool = True


class CepRange(_Setting):
    start: Cep
    end: Cep

    @model_validator(mode="after")
    def _ordered(self) -> CepRange:
        if self.end < self.start:
            raise ValueError("fim da faixa de CEP antes do início")
        return self


class DeliveryZone(_Setting):
    id: SettingId
    name: PlaceName
    kind: Literal["cep_ranges", "districts"]
    cep_ranges: Annotated[list[CepRange], Field(max_length=20)] = []
    # Districts are matched inside this city/state, ignoring accents and case.
    city: PlaceName | None = None
    state: Annotated[str, Field(pattern=r"^[A-Z]{2}$")] | None = None
    districts: Annotated[list[PlaceName], Field(max_length=200)] = []
    fee_cents: Annotated[int, Field(ge=0, le=10_000_000)] = 0
    min_order_cents: Cents | None = None
    eta_minutes: Annotated[int, Field(ge=0, le=10_080)] | None = None
    active: bool = True

    @model_validator(mode="after")
    def _has_area(self) -> DeliveryZone:
        if self.kind == "cep_ranges" and not self.cep_ranges:
            raise ValueError("zona por CEP precisa de ao menos uma faixa")
        if self.kind == "districts" and not (self.districts and self.city and self.state):
            raise ValueError("zona por bairro precisa de bairros, cidade e UF")
        return self


class DeliveryWindow(_Setting):
    weekday: Annotated[int, Field(ge=0, le=6)]  # 0 = Monday (date.weekday())
    start: ClockTime
    end: ClockTime
    modes: Annotated[list[FulfillmentMode], Field(min_length=1, max_length=3)]

    @model_validator(mode="after")
    def _ordered(self) -> DeliveryWindow:
        if self.end <= self.start:
            raise ValueError("fim da janela deve ser depois do início")
        return self


class PickupSettings(_Setting):
    enabled: bool = True
    locations: Annotated[list[PickupLocation], Field(max_length=10)] = []


class DeliverySettings(_Setting):
    enabled: bool = False
    zones: Annotated[list[DeliveryZone], Field(max_length=50)] = []


class SchedulingSettings(_Setting):
    enabled: bool = False
    windows: Annotated[list[DeliveryWindow], Field(max_length=28)] = []
    min_lead_minutes: Annotated[int, Field(ge=0, le=10_080)] = 60
    days_ahead: Annotated[int, Field(ge=1, le=30)] = 7


class ShippingOrigin(_Setting):
    """De onde a mercadoria sai. A transportadora cota a partir daqui e a etiqueta imprime isto."""

    name: PlaceName
    postal_code: Cep
    address: Annotated[str, Field(min_length=1, max_length=120)]
    number: Annotated[str, Field(min_length=1, max_length=20)]
    district: Annotated[str, Field(min_length=1, max_length=80)]
    city: PlaceName
    state: Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
    complement: Annotated[str, Field(max_length=80)] | None = None
    #: CPF ou CNPJ do remetente, só dígitos (a transportadora exige um dos dois na etiqueta).
    document: Annotated[str, Field(pattern=r"^\d{11}|\d{14}$")] | None = None
    phone: Annotated[str, Field(max_length=20)] | None = None
    email: Annotated[str, Field(max_length=120)] | None = None


class ShippingBoxSetting(_Setting):
    """Uma embalagem da loja. Sem nenhuma, o empacotador usa uma caixa pequena genérica.

    `id` e `name` chegaram depois e por isso são opcionais: as lojas que já tinham uma caixa
    padrão continuam validando, e o normalizador atribui o id na primeira escrita — o mesmo
    caminho dos locais de retirada e das zonas de entrega.
    """

    id: SettingId | None = None
    name: Annotated[str, Field(min_length=1, max_length=60)] | None = None
    width_mm: Annotated[int, Field(ge=10, le=2000)]
    height_mm: Annotated[int, Field(ge=10, le=2000)]
    depth_mm: Annotated[int, Field(ge=10, le=2000)]
    max_weight_grams: Annotated[int, Field(ge=100, le=100_000)] = 30_000
    #: Tara: caixa vazia também pesa, e a transportadora cobra o peso real.
    empty_weight_grams: Annotated[int, Field(ge=0, le=10_000)] = 0


class ShippingService(_Setting):
    """Um serviço que a loja resolveu oferecer, como ele voltou da cotação."""

    code: Annotated[str, Field(min_length=1, max_length=24)]
    name: Annotated[str, Field(min_length=1, max_length=80)]
    carrier: Annotated[str, Field(max_length=60)] = ""
    active: bool = True


class ShippingSettings(_Setting):
    """Envio por transportadora (ADR 0015). Convive com retirada e entrega por zona."""

    enabled: bool = False
    provider: Annotated[str, Field(min_length=1, max_length=24)] = "melhorenvio"
    origin: ShippingOrigin | None = None
    #: A caixa padrão, de quando havia uma só. Continua sendo a usada por todo produto que não
    #: aponta para nenhuma — tirá-la mudaria o frete de quem já vende.
    box: ShippingBoxSetting | None = None
    #: As demais embalagens. O produto escolhe a dele pelo `id`; quem não escolhe usa a padrão.
    boxes: Annotated[list[ShippingBoxSetting], Field(max_length=12)] = []
    #: Vazio = oferece tudo que a transportadora devolver.
    services: Annotated[list[ShippingService], Field(max_length=20)] = []
    #: Acréscimo da loja sobre o frete cotado (embalagem, mão de obra).
    markup_percent: Annotated[int, Field(ge=0, le=100)] = 0
    markup_cents: Annotated[int, Field(ge=0, le=10_000_000)] = 0
    #: Acima deste subtotal o frete sai zero para o cliente (a loja continua pagando a etiqueta).
    free_above_cents: Cents | None = None
    #: Dias de preparo somados ao prazo da transportadora.
    handling_days: Annotated[int, Field(ge=0, le=30)] = 0


class FulfillmentV2(_Setting):
    """Pickup locations, delivery zones (CEP ranges or districts) and time windows.

    V1 (`{modes, min_order_cents}`) became this in migration 0017; `shipping` entrou na etapa J."""

    pickup: PickupSettings = PickupSettings()
    delivery: DeliverySettings = DeliverySettings()
    shipping: ShippingSettings = ShippingSettings()
    min_order_cents: Cents = 0
    scheduling: SchedulingSettings = SchedulingSettings()

    @model_validator(mode="after")
    def _unique(self) -> FulfillmentV2:
        for label, items in (("local", self.pickup.locations), ("zona", self.delivery.zones)):
            ids = [item.id for item in items]
            names = [item.name.casefold() for item in items]
            if len(set(ids)) != len(ids) or len(set(names)) != len(names):
                raise ValueError(f"{label} repetido")
        # Nome repetido em embalagem é escolha impossível na tela do produto; id repetido faria
        # o empacotador escolher uma das duas em silêncio.
        nomes = [b.name.casefold() for b in self.shipping.boxes if b.name]
        identificadores = [b.id for b in self.shipping.boxes if b.id]
        if len(set(nomes)) != len(nomes) or len(set(identificadores)) != len(identificadores):
            raise ValueError("embalagem repetida")
        return self


class MethodSurcharge(_Setting):
    """Quanto a mais custa pagar por este meio. Percentual em pontos-base (350 = 3,5%)."""

    percent_bps: Annotated[int, Field(ge=0, le=3000)] = 0
    fixed_cents: Annotated[int, Field(ge=0, le=100_000)] = 0

    @property
    def zero(self) -> bool:
        return self.percent_bps == 0 and self.fixed_cents == 0


class InstallmentSurcharge(_Setting):
    """Faixa de parcelas do cartão: até `up_to` parcelas, cobra isto.

    A taxa da maquininha cresce com o parcelamento; uma taxa única de cartão devolve menos do
    que o lojista paga em 12x. As faixas são lidas da menor para a maior.
    """

    up_to: Annotated[int, Field(ge=1, le=12)]
    percent_bps: Annotated[int, Field(ge=0, le=3000)] = 0
    fixed_cents: Annotated[int, Field(ge=0, le=100_000)] = 0


class PaymentsV1(_Setting):
    """Repasse da taxa ao cliente (Lei 13.455/2017: pode, desde que informado).

    Por isso a linha aparece no checkout **antes** de confirmar, e não só na fatura.
    """

    enabled: bool = False
    #: Por meio: `pix`, `card`, `link`. Pix costuma ficar em zero — é o barato.
    surcharge: dict[Literal["pix", "card", "link"], MethodSurcharge] = {}
    #: Faixas de parcela do cartão; vazio = usa `surcharge["card"]` para qualquer parcela.
    card_installments: Annotated[list[InstallmentSurcharge], Field(max_length=12)] = []

    @model_validator(mode="after")
    def _ordered(self) -> PaymentsV1:
        limites = [faixa.up_to for faixa in self.card_installments]
        if len(set(limites)) != len(limites):
            raise ValueError("faixa de parcelas repetida")
        return self


class CheckoutV1(_Setting):
    # Only `reserve_on_place` is implemented (ADR 0011); the other value is ignored.
    reservation_mode: Literal["reserve_on_place", "decrement_on_payment"] = "reserve_on_place"
    # Order deadline: payment window before the reservation is released.
    pix_ttl_minutes: Annotated[int, Field(ge=5, le=1440)] = 30
    # Paid orders skip `payment_confirmed` and go straight to `accepted`.
    auto_accept: bool = False
    # Last status in which the customer may still cancel a paid order (refund + restock).
    customer_cancel_until: Literal["payment_confirmed", "accepted"] = "accepted"
    # Orders awaiting payment one customer may have open at once (anti stock hoarding).
    max_open_orders: Annotated[int, Field(ge=1, le=10)] = 3
    # Refunds above this need a second person to approve (four eyes).
    refund_four_eyes_threshold_cents: Annotated[int, Field(ge=0, le=100_000_000)] = 20_000


class EmailV1(_Setting):
    """De onde saem os e-mails desta loja.

    A senha **não** mora aqui: é senha de app, vai para o cofre cifrado
    (`tenant_integration_credentials`, provider `smtp`), como a chave do meio de pagamento.

    `username` é também o remetente. Não são dois campos porque o Gmail recusa enviar com um
    "De" diferente da conta autenticada — separar convidaria a loja a preencher errado e a
    descobrir pelo cliente que não recebeu.
    """

    enabled: bool = False
    host: Annotated[str, Field(min_length=3, max_length=200)] = "smtp.gmail.com"
    #: 587 é STARTTLS, que é o que o Gmail pede. 465 (TLS direto) também é aceito.
    port: Annotated[int, Field(ge=1, le=65535)] = 587
    #: A conta que autentica e assina o envio. Vazio enquanto a loja não configurou.
    username: Annotated[str, Field(max_length=200)] = ""
    #: O nome que aparece no "De". Vazio: vale o nome da loja.
    from_name: Annotated[str, Field(max_length=80)] = ""


SETTINGS_SCHEMAS: dict[str, tuple[int, type[_Setting]]] = {
    "storefront": (1, StorefrontV1),
    "branding": (1, BrandingV1),
    "seo": (1, SeoV1),
    "landing": (1, LandingV1),
    "fulfillment": (2, FulfillmentV2),
    "checkout": (1, CheckoutV1),
    "payments": (1, PaymentsV1),
    "email": (1, EmailV1),
}


def default_settings() -> dict[str, dict[str, Any]]:
    """Defaults written when a tenant is created (one row per key)."""
    return {key: model().model_dump(mode="json") for key, (_, model) in SETTINGS_SCHEMAS.items()}


def validate_setting(key: str, value: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Return (schema_version, normalized value) or raise a 422 listing the invalid fields."""
    entry = SETTINGS_SCHEMAS.get(key)
    if entry is None:
        raise ValidationError("Chave de configuração desconhecida.", key=key)
    version, model = entry
    try:
        parsed = model.model_validate(value)
    except PydanticValidationError as exc:
        errors = [
            {"field": ".".join(str(p) for p in err["loc"]), "message": err["msg"]}
            for err in exc.errors(include_url=False, include_context=False, include_input=False)
        ]
        raise ValidationError("Configuração inválida.", key=key, errors=errors) from exc
    return version, parsed.model_dump(mode="json")


def email_settings(settings: Mapping[str, Any]) -> EmailV1:
    """De onde saem os e-mails da loja; loja que nunca configurou cai nos padrões."""
    return EmailV1.model_validate(settings.get("email") or {})


def checkout_settings(settings: Mapping[str, Any]) -> CheckoutV1:
    """The store's checkout settings; missing keys (older rows) take the defaults."""
    return CheckoutV1.model_validate(settings.get("checkout") or {})


def fulfillment_settings(settings: Mapping[str, Any]) -> FulfillmentV2:
    return FulfillmentV2.model_validate(settings.get("fulfillment") or {})


def payments_settings(settings: Mapping[str, Any]) -> PaymentsV1:
    return PaymentsV1.model_validate(settings.get("payments") or {})
