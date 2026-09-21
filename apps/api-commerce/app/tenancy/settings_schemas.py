"""Versioned schemas for `tenant_settings` values.

Every write (ops or tenant panel) goes through `validate_setting`, so a stored value always
matches its schema: readers (storefront context, checkout) can trust the shape instead of
defending against arbitrary JSON. `schema_version` is stored next to the value; a breaking
change adds a V2 model plus a data migration, never an in-place reinterpretation.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from app.core.exceptions import ValidationError

AccessMode = Literal["public", "login_required", "whitelist"]
HexColor = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]
HttpsUrl = Annotated[str, Field(pattern=r"^https://", max_length=500)]


class _Setting(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StorefrontV1(_Setting):
    # `whitelist` is the safe default: nothing is shown until access is granted.
    access_mode: AccessMode = "whitelist"
    currency: Literal["BRL"] = "BRL"


class BrandingV1(_Setting):
    primary_color: HexColor = "#111111"
    logo_url: HttpsUrl | None = None


class SeoV1(_Setting):
    title: Annotated[str, Field(max_length=70)] | None = None
    description: Annotated[str, Field(max_length=160)] | None = None
    og_image_url: HttpsUrl | None = None
    # Search engines index the store only when the tenant opts in (and it is public).
    indexable: bool = False


class FulfillmentV1(_Setting):
    modes: Annotated[list[Literal["pickup", "delivery"]], Field(min_length=1, max_length=2)] = [
        "pickup"
    ]
    min_order_cents: Annotated[int, Field(ge=0, le=100_000_000)] = 0


class CheckoutV1(_Setting):
    reservation_mode: Literal["reserve_on_place", "decrement_on_payment"] = "reserve_on_place"
    pix_ttl_minutes: Annotated[int, Field(ge=5, le=1440)] = 30


SETTINGS_SCHEMAS: dict[str, tuple[int, type[_Setting]]] = {
    "storefront": (1, StorefrontV1),
    "branding": (1, BrandingV1),
    "seo": (1, SeoV1),
    "fulfillment": (1, FulfillmentV1),
    "checkout": (1, CheckoutV1),
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
