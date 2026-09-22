"""Catalog use cases for the tenant panel.

Rules that live here (and nowhere else):
- every product has a default variant, created with it and sharing its SKU;
- SKU is unique per tenant across products and variants, and immutable;
- a slug is unique per tenant, generated from the name when omitted;
- publishing needs a price above zero, an active variant and at least one ready image;
- a published product is `active` (for sale) or `paused` (shown as unavailable, with a reason);
  only `active` sells, so a check that forgets `paused` fails closed;
- categories nest at most two levels; archiving one with active children is refused;
- tags are given by name and created on first use (slug from the name, one per slug).

Writes are audited; product lifecycle changes also go to the outbox (the product is the
aggregate; categories are not).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.outbox import emit
from app.audit.writer import audit
from app.catalog.models import (
    LIVE_VARIANT_STATUSES,
    PUBLISHED_STATUSES,
    Category,
    Product,
    ProductStatus,
    ProductVariant,
    Tag,
    VariantStatus,
)
from app.catalog.pricing import EffectivePrice, check_promotion, effective_price, variant_price
from app.catalog.repository import MAX_CATEGORIES, MAX_TAGS, CatalogRepository
from app.catalog.schemas import (
    CATEGORY_REQUIRED_FIELDS,
    PRODUCT_REQUIRED_FIELDS,
    CategoryCreate,
    CategoryUpdate,
    ProductCreate,
    ProductUpdate,
    VariantUpdate,
)
from app.core.exceptions import (
    ConflictError,
    InvalidTransitionError,
    NotFoundError,
    ValidationError,
)
from app.core.slugs import next_free_slug, slugify
from app.inventory.models import InventoryBalance
from app.media.models import MediaAsset, MediaOwner, MediaStatus
from app.media.repository import MediaRepository
from app.models.base import utcnow
from app.tenancy.context import TenantContext
from app.tenancy.repository import TenantRepository
from app.tenancy.service import Actor

SKU_SEQUENCE = "product_sku"
SKU_ATTEMPTS = 20
DEFAULT_VARIANT_NAME = "Padrão"

_PRODUCT_AUDITED = (
    "name",
    "slug",
    "base_price_cents",
    "promo_price_cents",
    "promo_starts_at",
    "promo_ends_at",
    "stock_policy",
    "kind",
)


@dataclass(frozen=True, slots=True)
class ProductView:
    product: Product
    variants: list[ProductVariant]
    category_ids: list[str]
    media: list[MediaAsset] = field(default_factory=list)
    tags: list[Tag] = field(default_factory=list)


def product_price(product: Product, now: datetime) -> EffectivePrice:
    return effective_price(
        base_cents=product.base_price_cents,
        promo_cents=product.promo_price_cents,
        starts_at=product.promo_starts_at,
        ends_at=product.promo_ends_at,
        now=now,
    )


def price_of_variant(product: Product, variant: ProductVariant, now: datetime) -> EffectivePrice:
    return variant_price(
        variant_price_cents=variant.price_cents,
        base_cents=product.base_price_cents,
        promo_cents=product.promo_price_cents,
        starts_at=product.promo_starts_at,
        ends_at=product.promo_ends_at,
        now=now,
    )


def _json_value(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _snapshot(obj: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    return {name: _json_value(getattr(obj, name)) for name in fields}


def _snapshot_values(values: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    return {name: _json_value(values[name]) for name in fields}


class CatalogService:
    def __init__(self, session: AsyncSession, tenant: TenantContext, actor: Actor) -> None:
        self.session = session
        self.tenant = tenant
        self.actor = actor
        self.repo = CatalogRepository(session)

    # ------------------------------------------------------------------ products
    async def create_product(self, data: ProductCreate) -> ProductView:
        check_promotion(
            base_cents=data.base_price_cents,
            promo_cents=data.promo_price_cents,
            starts_at=data.promo_starts_at,
            ends_at=data.promo_ends_at,
        )
        category_ids = await self._checked_category_ids(data.category_ids)
        tags = await self._resolve_tags(data.tags)
        sku = data.sku or await self._generate_sku()
        if data.sku and await self.repo.sku_taken(sku):
            raise ConflictError("SKU já usado nesta loja.", sku=sku)
        slug = await self._product_slug(data.slug, data.name)

        fields = data.model_dump(exclude={"sku", "slug", "category_ids", "seo", "tags"})
        product = Product(
            **fields,
            sku=sku,
            slug=slug,
            seo=data.seo.model_dump(exclude_none=True) if data.seo else None,
            status=ProductStatus.DRAFT,
            has_variants=False,
            created_by_actor=self.actor.id,
            updated_by_actor=self.actor.id,
        )
        self.session.add(product)
        await self._flush_unique("SKU ou slug já usado nesta loja.")
        variant = ProductVariant(
            product_id=product.id,
            sku=sku,
            name=DEFAULT_VARIANT_NAME,
            status=VariantStatus.ACTIVE,
            position=0,
            created_by_actor=self.actor.id,
            updated_by_actor=self.actor.id,
        )
        self.session.add(variant)
        await self._flush_unique("SKU já usado nesta loja.")
        # The balance row exists from day one: stock adjustments then only lock existing rows
        # (no concurrent INSERTs, whose gap locks are what deadlocks upserts on MariaDB).
        self.session.add(InventoryBalance(variant_id=variant.id, on_hand_milli=0, reserved_milli=0))
        await self.repo.replace_product_categories(product.id, category_ids)
        await self.repo.replace_product_tags(product.id, [t.id for t in tags])
        await self.session.flush()

        after = _snapshot(product, ("sku", *_PRODUCT_AUDITED))
        if tags:
            after["tags"] = [t.slug for t in tags]
        await self._audit("product.created", product, after=after)
        await self._emit(product, "product.created")
        return ProductView(product, [variant], sorted(category_ids), tags=tags)

    async def get_product(self, product_id: str) -> ProductView:
        product = await self._product_or_404(product_id)
        variants = (await self.repo.variants_for([product.id])).get(product.id, [])
        categories = (await self.repo.category_ids_for([product.id])).get(product.id, [])
        media = await MediaRepository(self.session).for_owner(MediaOwner.PRODUCT, product.id)
        tags = (await self.repo.tags_for([product.id])).get(product.id, [])
        return ProductView(product, variants, categories, list(media), tags)

    async def update_product(self, product_id: str, data: ProductUpdate) -> ProductView:
        product = await self._product_or_404(product_id)
        if product.status == ProductStatus.ARCHIVED:
            raise ConflictError("Produto arquivado não pode ser editado.")
        changes = data.model_dump(exclude_unset=True, exclude={"category_ids", "seo", "tags"})
        nulled = sorted(k for k, v in changes.items() if v is None and k in PRODUCT_REQUIRED_FIELDS)
        if nulled:
            raise ValidationError("Campos obrigatórios não podem ser nulos.", fields=nulled)

        merged = {
            "base_cents": changes.get("base_price_cents", product.base_price_cents),
            "promo_cents": changes.get("promo_price_cents", product.promo_price_cents),
            "starts_at": changes.get("promo_starts_at", product.promo_starts_at),
            "ends_at": changes.get("promo_ends_at", product.promo_ends_at),
        }
        check_promotion(**merged)
        if product.status in PUBLISHED_STATUSES and merged["base_cents"] <= 0:
            raise ConflictError("Produto publicado precisa de preço maior que zero.")
        if "slug" in changes and changes["slug"] != product.slug:
            taken = await self.repo.product_slugs_like(changes["slug"], exclude_id=product.id)
            if changes["slug"] in taken:
                raise ConflictError("Slug já usado nesta loja.", slug=changes["slug"])

        # Read before mutating: a query after setattr autoflushes, and a unique conflict there
        # would escape `_flush_unique` as a 500 instead of a 409.
        categories_before = (await self.repo.category_ids_for([product.id])).get(product.id, [])
        categories = categories_before
        if data.category_ids is not None:
            categories = await self._checked_category_ids(data.category_ids)
        tags_before = (await self.repo.tags_for([product.id])).get(product.id, [])
        tags = tags_before
        if data.tags is not None:
            tags = await self._resolve_tags(data.tags)

        if "seo" in data.model_fields_set:
            changes["seo"] = data.seo.model_dump(exclude_none=True) if data.seo else None
        old = {name: getattr(product, name) for name in changes}
        changed = sorted(name for name, value in changes.items() if old[name] != value)
        for name in changed:
            setattr(product, name, changes[name])
        if categories != categories_before:
            await self.repo.replace_product_categories(
                product.id, categories, current=categories_before
            )
            changed.append("category_ids")
        tag_ids_before = {t.id for t in tags_before}
        if {t.id for t in tags} != tag_ids_before:
            await self.repo.replace_product_tags(
                product.id, [t.id for t in tags], current=tag_ids_before
            )
            changed.append("tags")
        if not changed:
            return await self.get_product(product.id)

        product.updated_by_actor = self.actor.id
        await self._flush_unique("Slug já usado nesta loja.")
        # Values only for the short, meaningful fields; long text is listed by name.
        audited = [name for name in changed if name in _PRODUCT_AUDITED]
        before = {"fields": changed, **_snapshot_values(old, audited)}
        after = _snapshot(product, tuple(audited))
        if "tags" in changed:
            before["tags"] = [t.slug for t in tags_before]
            after["tags"] = [t.slug for t in tags]
        await self._audit("product.updated", product, before=before, after=after)
        await self._emit(product, "product.updated")
        variants = (await self.repo.variants_for([product.id])).get(product.id, [])
        return ProductView(product, variants, categories, tags=tags)

    async def archive_product(self, product_id: str) -> None:
        product = await self._product_or_404(product_id)
        if product.status == ProductStatus.ARCHIVED:
            return
        before = product.status
        product.status = ProductStatus.ARCHIVED
        product.archived_at = utcnow()
        product.updated_by_actor = self.actor.id
        await self.session.flush()
        await self._audit(
            "product.archived", product, before={"status": before}, after={"status": "archived"}
        )
        await self._emit(product, "product.archived")

    async def publish(self, product_id: str) -> ProductView:
        view = await self.get_product(product_id)
        product = view.product
        if product.status in PUBLISHED_STATUSES:
            return view  # a paused product stays paused: resuming is its own action
        if product.status == ProductStatus.ARCHIVED:
            raise ConflictError("Produto arquivado não pode ser publicado.")
        problems: list[str] = []
        if product.base_price_cents <= 0:
            problems.append("price")
        if not any(v.status == VariantStatus.ACTIVE for v in view.variants):
            problems.append("active_variant")
        if not any(m.status == MediaStatus.READY for m in view.media):
            problems.append("media")
        if problems:
            raise ConflictError("Produto ainda não pode ser publicado.", missing=problems)
        before = product.status
        product.status = ProductStatus.ACTIVE
        product.published_at = product.published_at or utcnow()
        product.updated_by_actor = self.actor.id
        await self.session.flush()
        await self._audit(
            "product.published", product, before={"status": before}, after={"status": "active"}
        )
        await self._emit(product, "product.published")
        return view

    async def unpublish(self, product_id: str) -> ProductView:
        view = await self.get_product(product_id)
        product = view.product
        if product.status not in PUBLISHED_STATUSES:
            return view
        before = product.status
        product.status = ProductStatus.INACTIVE
        _clear_pause(product)
        product.updated_by_actor = self.actor.id
        await self.session.flush()
        await self._audit(
            "product.unpublished",
            product,
            before={"status": before},
            after={"status": "inactive"},
        )
        await self._emit(product, "product.unpublished")
        return view

    async def update_variant(
        self, product_id: str, variant_id: str, data: VariantUpdate
    ) -> ProductView:
        product = await self._product_or_404(product_id)
        if product.status == ProductStatus.ARCHIVED:
            raise ConflictError("Produto arquivado não pode ser editado.")
        variant = await self.repo.get_variant(product.id, variant_id)
        if variant is None:
            raise NotFoundError("Variante não encontrada.")
        changes = data.model_dump(exclude_unset=True)
        for name in ("name", "status"):
            if name in changes and changes[name] is None:
                raise ValidationError("Campos obrigatórios não podem ser nulos.", fields=[name])
        if (
            product.status in PUBLISHED_STATUSES
            and changes.get("status") == VariantStatus.INACTIVE
            and variant.status in LIVE_VARIANT_STATUSES
        ):
            siblings = (await self.repo.variants_for([product.id])).get(product.id, [])
            if not any(v.status in LIVE_VARIANT_STATUSES and v.id != variant.id for v in siblings):
                raise ConflictError("Produto publicado precisa de ao menos uma variante ativa.")
        fields = ("name", "price_cents", "cost_cents", "status")
        before = _snapshot(variant, fields)
        for name, value in changes.items():
            setattr(variant, name, value)
        if variant.status != VariantStatus.PAUSED:
            _clear_pause(variant)  # a status set here ends the pause
        after = _snapshot(variant, fields)
        if after == before:
            return await self.get_product(product.id)
        variant.updated_by_actor = self.actor.id
        await self.session.flush()
        view = await self.get_product(product.id)
        await audit(
            self.session,
            actor=self.actor.id,
            action="product.variant_updated",
            entity_type="product_variant",
            entity_id=variant.id,
            tenant_id=self.tenant.id,
            before={k: v for k, v in before.items() if after[k] != v},
            after={k: after[k] for k in after if before[k] != after[k]},
            ip=self.actor.ip,
            user_agent=self.actor.user_agent,
        )
        await self._emit(product, "product.updated")
        return view

    # ------------------------------------------------------------------ pause
    async def pause_product(self, product_id: str, *, reason: str | None = None) -> ProductView:
        """Keeps the product in the storefront as unavailable. Idempotent: pausing twice is a
        no-op (no audit, no event); only a product for sale can be paused."""
        view = await self.get_product(product_id)
        product = view.product
        if product.status == ProductStatus.PAUSED:
            return view
        if product.status != ProductStatus.ACTIVE:
            raise InvalidTransitionError(
                "Só produto publicado pode ser pausado.", code="not_published"
            )
        product.status = ProductStatus.PAUSED
        _set_pause(product, reason, self.actor.id)
        product.updated_by_actor = self.actor.id
        await self.session.flush()
        await self._audit(
            "product.paused",
            product,
            before={"status": "active"},
            after={"status": "paused", "reason": reason},
        )
        await self._emit(product, "product.paused", reason=reason, actor=self.actor.id)
        return view

    async def resume_product(self, product_id: str) -> ProductView:
        view = await self.get_product(product_id)
        product = view.product
        if product.status == ProductStatus.ACTIVE:
            return view
        if product.status != ProductStatus.PAUSED:
            raise InvalidTransitionError("Produto não está pausado.", code="not_paused")
        reason = product.paused_reason
        product.status = ProductStatus.ACTIVE
        _clear_pause(product)
        product.updated_by_actor = self.actor.id
        await self.session.flush()
        await self._audit(
            "product.resumed",
            product,
            before={"status": "paused", "reason": reason},
            after={"status": "active"},
        )
        await self._emit(product, "product.resumed", actor=self.actor.id)
        return view

    async def pause_variant(
        self, product_id: str, variant_id: str, *, reason: str | None = None
    ) -> ProductView:
        return await self._set_variant_paused(product_id, variant_id, paused=True, reason=reason)

    async def resume_variant(self, product_id: str, variant_id: str) -> ProductView:
        return await self._set_variant_paused(product_id, variant_id, paused=False, reason=None)

    async def _set_variant_paused(
        self, product_id: str, variant_id: str, *, paused: bool, reason: str | None
    ) -> ProductView:
        product = await self._product_or_404(product_id)
        if product.status == ProductStatus.ARCHIVED:
            raise ConflictError("Produto arquivado não pode ser editado.")
        variant = await self.repo.get_variant(product.id, variant_id)
        if variant is None:
            raise NotFoundError("Variante não encontrada.")
        target, source = (
            (VariantStatus.PAUSED, VariantStatus.ACTIVE)
            if paused
            else (VariantStatus.ACTIVE, VariantStatus.PAUSED)
        )
        if variant.status == target:
            return await self.get_product(product.id)
        if variant.status != source:
            raise InvalidTransitionError(
                "Variante inativa não pode ser pausada."
                if paused
                else "Variante não está pausada.",
                code="variant_inactive" if paused else "not_paused",
            )
        previous_reason = variant.paused_reason
        variant.status = target
        if paused:
            _set_pause(variant, reason, self.actor.id)
        else:
            _clear_pause(variant)
        variant.updated_by_actor = self.actor.id
        await self.session.flush()
        action = "product.variant_paused" if paused else "product.variant_resumed"
        await audit(
            self.session,
            actor=self.actor.id,
            action=action,
            entity_type="product_variant",
            entity_id=variant.id,
            tenant_id=self.tenant.id,
            before={"status": source, **({} if paused else {"reason": previous_reason})},
            after={"status": target, **({"reason": reason} if paused else {})},
            ip=self.actor.ip,
            user_agent=self.actor.user_agent,
        )
        extra: dict[str, Any] = {"variant_id": variant.id, "actor": self.actor.id}
        if paused:
            extra["reason"] = reason
        await self._emit(product, action, **extra)
        return await self.get_product(product.id)

    # ------------------------------------------------------------------ tags
    async def list_tags(self) -> list[Tag]:
        return await self.repo.list_tags()

    async def _resolve_tags(self, names: list[str]) -> list[Tag]:
        """Tags for these names, created when new; same slug → same tag (first name wins)."""
        wanted: dict[str, str] = {}
        for raw in names:
            name = " ".join(raw.split())
            slug = slugify(name, max_length=80)
            if not slug:
                raise ValidationError("Tag sem letras ou números.", fields=["tags"])
            wanted.setdefault(slug, name)
        if not wanted:
            return []
        found = {t.slug: t for t in await self.repo.tags_by_slug(wanted)}
        missing = [slug for slug in wanted if slug not in found]
        if missing:
            if await self.repo.count_tags() + len(missing) > MAX_TAGS:
                raise ConflictError("Limite de tags atingido.", limit=MAX_TAGS)
            for slug in missing:
                tag = Tag(
                    slug=slug,
                    name=wanted[slug],
                    created_by_actor=self.actor.id,
                    updated_by_actor=self.actor.id,
                )
                self.session.add(tag)
                found[slug] = tag
            # Two writers creating the same new tag: the loser gets a 409 and retries.
            await self._flush_unique("Tag criada ao mesmo tempo em outra edição; tente de novo.")
        return sorted((found[slug] for slug in wanted), key=lambda t: t.name.casefold())

    # ------------------------------------------------------------------ categories
    async def create_category(self, data: CategoryCreate) -> Category:
        if await self.repo.count_categories() >= MAX_CATEGORIES:
            raise ConflictError("Limite de categorias atingido.", limit=MAX_CATEGORIES)
        if data.parent_id:
            await self._checked_parent(data.parent_id, moving=None)
        slug = await self._category_slug(data.slug, data.name)
        category = Category(
            parent_id=data.parent_id,
            slug=slug,
            name=data.name,
            description=data.description,
            position=data.position,
            created_by_actor=self.actor.id,
            updated_by_actor=self.actor.id,
        )
        self.session.add(category)
        await self._flush_unique("Slug de categoria já usado nesta loja.")
        await self._audit_category(
            "category.created", category, after={"slug": slug, "parent_id": data.parent_id}
        )
        return category

    async def list_categories(self) -> list[Category]:
        return list(await self.repo.list_categories())

    async def update_category(self, category_id: str, data: CategoryUpdate) -> Category:
        category = await self._category_or_404(category_id)
        changes = data.model_dump(exclude_unset=True)
        nulled = sorted(
            k for k, v in changes.items() if v is None and k in CATEGORY_REQUIRED_FIELDS
        )
        if nulled:
            raise ValidationError("Campos obrigatórios não podem ser nulos.", fields=nulled)
        if changes.get("parent_id") and changes["parent_id"] != category.parent_id:
            await self._checked_parent(changes["parent_id"], moving=category)
        if "slug" in changes and changes["slug"] != category.slug:
            taken = await self.repo.category_slugs_like(changes["slug"], exclude_id=category.id)
            if changes["slug"] in taken:
                raise ConflictError("Slug de categoria já usado nesta loja.", slug=changes["slug"])
        fields = ("name", "slug", "parent_id", "position")
        before = _snapshot(category, fields)
        for name, value in changes.items():
            setattr(category, name, value)
        category.updated_by_actor = self.actor.id
        await self._flush_unique("Slug de categoria já usado nesta loja.")
        after = _snapshot(category, fields)
        await self._audit_category(
            "category.updated",
            category,
            before={k: v for k, v in before.items() if after[k] != v},
            after={k: after[k] for k in after if before[k] != after[k]},
        )
        return category

    async def archive_category(self, category_id: str) -> None:
        category = await self._category_or_404(category_id)
        if await self.repo.has_children(category.id):
            raise ConflictError("Arquive as subcategorias primeiro.")
        category.archived_at = utcnow()
        category.updated_by_actor = self.actor.id
        await self.repo.unlink_category(category.id)
        await self.session.flush()
        await self._audit_category("category.archived", category, after={"archived": True})

    # ------------------------------------------------------------------ helpers
    async def _product_or_404(self, product_id: str) -> Product:
        product = await self.repo.get_product(product_id)
        if product is None:
            raise NotFoundError("Produto não encontrado.")
        return product

    async def _category_or_404(self, category_id: str) -> Category:
        category = await self.repo.get_category(category_id)
        if category is None:
            raise NotFoundError("Categoria não encontrada.")
        return category

    async def _checked_category_ids(self, category_ids: list[str]) -> list[str]:
        wanted = set(category_ids)
        found = await self.repo.active_category_ids(wanted)
        unknown = sorted(wanted - found)
        if unknown:
            # Same answer for "another tenant's id" and "no such id".
            raise ValidationError("Categoria inexistente.", category_ids=unknown)
        return sorted(found)

    async def _checked_parent(self, parent_id: str, *, moving: Category | None) -> None:
        """Two levels at most: the parent must be a root, and a category with children cannot
        become a child. That also rules out cycles."""
        parent = await self.repo.get_category(parent_id)
        if parent is None:
            raise ValidationError("Categoria pai inexistente.", parent_id=parent_id)
        if parent.parent_id is not None:
            raise ValidationError("Categorias têm no máximo dois níveis.")
        if moving is not None:
            if parent.id == moving.id:
                raise ValidationError("Categoria não pode ser pai de si mesma.")
            if await self.repo.has_children(moving.id):
                raise ValidationError("Categoria com subcategorias não pode virar subcategoria.")

    async def _generate_sku(self) -> str:
        sequences = TenantRepository(self.session)
        for _ in range(SKU_ATTEMPTS):
            candidate = f"P{await sequences.next_sequence(SKU_SEQUENCE):05d}"
            if not await self.repo.sku_taken(candidate):
                return candidate
        raise ConflictError("Não foi possível gerar um SKU livre; informe um SKU.")

    async def _product_slug(self, requested: str | None, name: str) -> str:
        if requested:
            if requested in await self.repo.product_slugs_like(requested):
                raise ConflictError("Slug já usado nesta loja.", slug=requested)
            return requested
        base = slugify(name) or "produto"
        slug = next_free_slug(base, await self.repo.product_slugs_like(base))
        if slug is None:
            raise ConflictError("Não foi possível gerar um slug livre; informe um slug.")
        return slug

    async def _category_slug(self, requested: str | None, name: str) -> str:
        if requested:
            if requested in await self.repo.category_slugs_like(requested):
                raise ConflictError("Slug de categoria já usado nesta loja.", slug=requested)
            return requested
        base = slugify(name) or "categoria"
        slug = next_free_slug(base, await self.repo.category_slugs_like(base))
        if slug is None:
            raise ConflictError("Não foi possível gerar um slug livre; informe um slug.")
        return slug

    async def _flush_unique(self, message: str) -> None:
        """A concurrent writer can take the same SKU/slug between our check and our insert."""
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise ConflictError(message) from exc

    async def _audit(
        self,
        action: str,
        product: Product,
        *,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None:
        await audit(
            self.session,
            actor=self.actor.id,
            action=action,
            entity_type="product",
            entity_id=product.id,
            tenant_id=self.tenant.id,
            before=before,
            after=after,
            ip=self.actor.ip,
            user_agent=self.actor.user_agent,
        )

    async def _audit_category(
        self,
        action: str,
        category: Category,
        *,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None:
        await audit(
            self.session,
            actor=self.actor.id,
            action=action,
            entity_type="category",
            entity_id=category.id,
            tenant_id=self.tenant.id,
            before=before,
            after=after,
            ip=self.actor.ip,
            user_agent=self.actor.user_agent,
        )

    async def _emit(self, product: Product, event_type: str, **extra: Any) -> None:
        await emit(
            self.session,
            aggregate_type="product",
            aggregate_id=product.id,
            event_type=event_type,
            payload={
                "product_id": product.id,
                "sku": product.sku,
                "slug": product.slug,
                "status": product.status,
                **extra,
            },
            tenant_id=self.tenant.id,
        )


def _set_pause(row: Product | ProductVariant, reason: str | None, actor: str) -> None:
    row.paused_at = utcnow()
    row.paused_reason = reason
    row.paused_by_actor = actor


def _clear_pause(row: Product | ProductVariant) -> None:
    row.paused_at = None
    row.paused_reason = None
    row.paused_by_actor = None
