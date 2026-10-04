"""Embalagens da loja (frete v2): cadastro, embalagem padrão, arquivar e apagar.

Regras que moram aqui (e em nenhum outro lugar):
- no máximo `MAX_PACKAGES_PER_TENANT` embalagens por loja, contando as arquivadas;
- nome único por loja, sem diferenciar maiúscula;
- **exatamente uma padrão** quando existe alguma: a primeira nasce padrão; a padrão não arquiva
  nem apaga; trocar a padrão tira a marca da antiga e põe na nova na mesma transação (o UNIQUE
  sobre `default_marker` segura "no máximo uma" até contra corrida);
- medida de fora, quando informada, vem inteira e nunca menor que a de dentro; tubo tem largura =
  altura (o diâmetro); o peso máximo passa da caixa vazia;
- apagar só embalagem que nenhuma regra de produto usa; usada, arquiva-se.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.writer import audit
from app.catalog.models import Product
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.shipping.models import MAX_PACKAGES_PER_TENANT, ProductPackageRule, ShippingPackage
from app.shipping.packing.model import Dims, PackageKind, derived_outer
from app.tenancy.context import TenantContext
from app.tenancy.service import Actor

#: Tudo que a loja edita numa embalagem (`default_marker` só muda por `make_default`).
EDITABLE = frozenset(
    {
        "name",
        "kind",
        "inner_length_mm",
        "inner_width_mm",
        "inner_height_mm",
        "outer_length_mm",
        "outer_width_mm",
        "outer_height_mm",
        "empty_weight_grams",
        "max_weight_grams",
        "material_cost_cents",
        "auto_select",
        "active",
        "position",
    }
)
_OUTER = ("outer_length_mm", "outer_width_mm", "outer_height_mm")
_AUDITED = (
    "name",
    "kind",
    "inner_length_mm",
    "inner_width_mm",
    "inner_height_mm",
    "outer_length_mm",
    "outer_width_mm",
    "outer_height_mm",
    "empty_weight_grams",
    "max_weight_grams",
    "material_cost_cents",
    "auto_select",
    "active",
    "default_marker",
)
#: Quantos produtos a tela mostra ao avisar o impacto de arquivar.
IMPACT_LIMIT = 50


class PackageNameTakenError(ConflictError):
    error_code = "package_name_taken"
    message = "Já existe uma embalagem com esse nome."


class PackageLimitError(ConflictError):
    error_code = "package_limit"
    message = f"A loja pode ter até {MAX_PACKAGES_PER_TENANT} embalagens. Arquive ou apague uma."


class DefaultPackageError(ConflictError):
    error_code = "default_package"
    message = "A embalagem padrão não pode ser arquivada nem apagada. Escolha outra padrão antes."


class PackageInUseError(ConflictError):
    error_code = "package_in_use"
    message = "Há produtos que usam esta embalagem. Arquive em vez de apagar."


def inner_dims(package: ShippingPackage) -> Dims:
    return Dims(package.inner_length_mm, package.inner_width_mm, package.inner_height_mm)


def outer_dims(package: ShippingPackage) -> Dims:
    """A medida que a transportadora cobra: a informada, ou a de dentro mais a parede."""
    if (
        package.outer_length_mm is not None
        and package.outer_width_mm is not None
        and package.outer_height_mm is not None
    ):
        return Dims(package.outer_length_mm, package.outer_width_mm, package.outer_height_mm)
    return derived_outer(inner_dims(package), PackageKind(package.kind))


class PackageService:
    def __init__(self, session: AsyncSession, tenant: TenantContext, actor: Actor) -> None:
        self.session = session
        self.tenant = tenant
        self.actor = actor

    # ------------------------------------------------------------------ leitura
    async def listing(self) -> list[ShippingPackage]:
        """Todas, padrão primeiro, depois ativas por posição; limitada pelo teto da loja."""
        stmt = (
            select(ShippingPackage)
            .order_by(
                ShippingPackage.default_marker.is_(None),
                ShippingPackage.active.desc(),
                ShippingPackage.position,
                ShippingPackage.name,
                ShippingPackage.id,
            )
            .limit(MAX_PACKAGES_PER_TENANT)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def get(self, package_id: str) -> ShippingPackage:
        package = await self.session.get(ShippingPackage, package_id)
        if package is None:
            raise NotFoundError("Embalagem não encontrada.")
        return package

    async def rules_count(self, package_ids: Sequence[str]) -> dict[str, int]:
        if not package_ids:
            return {}
        stmt = (
            select(ProductPackageRule.package_id, func.count())
            .where(ProductPackageRule.package_id.in_(list(package_ids)))
            .group_by(ProductPackageRule.package_id)
        )
        return {package_id: int(n) for package_id, n in (await self.session.execute(stmt)).all()}

    async def products_using(self, package_id: str) -> list[tuple[str, str]]:
        """Quem perde a embalagem se ela for arquivada: `(product_id, nome)`."""
        stmt = (
            select(Product.id, Product.name)
            .join(ProductPackageRule, ProductPackageRule.product_id == Product.id)
            .where(ProductPackageRule.package_id == package_id)
            .where(Product.archived_at.is_(None))
            .order_by(Product.name, Product.id)
            .limit(IMPACT_LIMIT)
        )
        return [(pid, name) for pid, name in (await self.session.execute(stmt)).all()]

    # ------------------------------------------------------------------ escrita
    async def create(self, changes: Mapping[str, Any]) -> ShippingPackage:
        total = int(
            await self.session.scalar(select(func.count()).select_from(ShippingPackage)) or 0
        )
        if total >= MAX_PACKAGES_PER_TENANT:
            raise PackageLimitError()
        await self._name_free(str(changes["name"]), exclude_id=None)
        # Defaults explícitos: o `default=` da coluna só entra no flush, e `_validate` roda antes.
        package = ShippingPackage(
            kind=PackageKind.BOX,
            empty_weight_grams=0,
            max_weight_grams=30_000,
            auto_select=True,
            active=True,
            position=0,
        )
        _apply(package, changes)
        # A primeira embalagem da loja é a padrão: sem padrão, o motor não tem para onde ir.
        has_default = await self.session.scalar(
            select(ShippingPackage.id).where(ShippingPackage.default_marker == 1).limit(1)
        )
        if has_default is None:
            package.default_marker = 1
            package.active = True
        _validate(package)
        package.created_by_actor = self.actor.id
        package.updated_by_actor = self.actor.id
        self.session.add(package)
        await self._flush()
        await self._audit("shipping_package.created", package, before=None)
        return package

    async def update(self, package_id: str, changes: Mapping[str, Any]) -> ShippingPackage:
        package = await self.get(package_id)
        before = _snapshot(package)
        if "name" in changes and str(changes["name"]).casefold() != package.name.casefold():
            await self._name_free(str(changes["name"]), exclude_id=package.id)
        _apply(package, changes)
        if package.is_default and not package.active:
            raise DefaultPackageError()
        _validate(package)
        if _snapshot(package) == before:
            return package
        package.updated_by_actor = self.actor.id
        await self._flush()
        await self._audit("shipping_package.updated", package, before=before)
        return package

    async def make_default(self, package_id: str) -> ShippingPackage:
        package = await self.get(package_id)
        if package.is_default:
            return package
        if not package.active:
            raise ConflictError(
                "Embalagem arquivada não pode ser a padrão.", reason="package_inactive"
            )
        before = _snapshot(package)
        # Tira a marca antes de pôr: o UNIQUE sobre `default_marker` recusaria duas por um
        # instante. Tudo na mesma transação, então ninguém vê a loja sem padrão.
        await self.session.execute(
            update(ShippingPackage)
            .where(ShippingPackage.default_marker == 1)
            .values(default_marker=None, updated_by_actor=self.actor.id)
            .execution_options(synchronize_session="fetch")
        )
        await self._flush()
        package.default_marker = 1
        package.updated_by_actor = self.actor.id
        await self._flush()
        await self._audit("shipping_package.default_changed", package, before=before)
        return package

    async def delete(self, package_id: str) -> None:
        package = await self.get(package_id)
        if package.is_default:
            raise DefaultPackageError()
        usos = (await self.rules_count([package.id])).get(package.id, 0)
        if usos:
            raise PackageInUseError(products=usos)
        before = _snapshot(package)
        await self.session.delete(package)
        await self._flush()
        await audit(
            self.session,
            actor=self.actor.id,
            action="shipping_package.deleted",
            entity_type="shipping_package",
            entity_id=package_id,
            tenant_id=self.tenant.id,
            before=before,
            after=None,
            ip=self.actor.ip,
            user_agent=self.actor.user_agent,
        )

    # ------------------------------------------------------------------ interno
    async def _name_free(self, name: str, *, exclude_id: str | None) -> None:
        # Compara sem diferenciar maiúscula aqui, porque o SQLite dos testes não tem colação _ci.
        nomes = await self.session.execute(select(ShippingPackage.id, ShippingPackage.name))
        for package_id, existente in nomes.all():
            if package_id != exclude_id and existente.casefold() == name.strip().casefold():
                raise PackageNameTakenError(name=name)

    async def _flush(self) -> None:
        try:
            await self.session.flush()
        except IntegrityError as exc:
            # Corrida: duas abas criando a "primeira" (duas padrão) ou o mesmo nome.
            raise ConflictError(
                "A embalagem mudou ao mesmo tempo em outra tela. Recarregue e tente de novo.",
                reason="package_conflict",
            ) from exc

    async def _audit(
        self, action: str, package: ShippingPackage, *, before: dict[str, Any] | None
    ) -> None:
        after = _snapshot(package)
        await audit(
            self.session,
            actor=self.actor.id,
            action=action,
            entity_type="shipping_package",
            entity_id=package.id,
            tenant_id=self.tenant.id,
            before={k: v for k, v in before.items() if after.get(k) != v} if before else None,
            after={k: v for k, v in after.items() if not before or before.get(k) != v},
            ip=self.actor.ip,
            user_agent=self.actor.user_agent,
        )


def _apply(package: ShippingPackage, changes: Mapping[str, Any]) -> None:
    for key, value in changes.items():
        if key in EDITABLE:
            setattr(
                package, key, value.strip() if key == "name" and isinstance(value, str) else value
            )


def _snapshot(package: ShippingPackage) -> dict[str, Any]:
    return {name: getattr(package, name) for name in _AUDITED}


def _validate(package: ShippingPackage) -> None:
    """O estado final faz sentido? Roda depois de aplicar o PATCH, sobre o que vai ser salvo."""
    kind = PackageKind(package.kind)
    inner = inner_dims(package)
    if not inner.complete:
        raise ValidationError("Informe as três medidas por dentro.", reason="inner_required")
    if kind == PackageKind.TUBE and package.inner_width_mm != package.inner_height_mm:
        raise ValidationError(
            "No tubo, largura e altura são o diâmetro: precisam ser iguais.",
            reason="tube_diameter",
        )
    outer = [getattr(package, name) for name in _OUTER]
    if any(v is not None for v in outer):
        if not all(v is not None for v in outer):
            raise ValidationError(
                "Informe as três medidas por fora, ou deixe as três vazias.",
                reason="outer_partial",
            )
        if any(
            o < i for o, i in zip(outer, (inner.length, inner.width, inner.height), strict=True)
        ):
            raise ValidationError(
                "A medida por fora não pode ser menor que a por dentro.", reason="outer_smaller"
            )
    if package.max_weight_grams <= package.empty_weight_grams:
        raise ValidationError(
            "O peso máximo precisa passar do peso da embalagem vazia.", reason="weight_limit"
        )
