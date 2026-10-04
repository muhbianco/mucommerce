"""Migrations must apply and revert on a real engine (SQLite here, MariaDB in CI)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

from app.core.bootstrap import alembic_config, alembic_sqlalchemy_url


@pytest.fixture
def sqlite_url(tmp_path: Path) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / 'migrations.db'}"


def test_upgrade_head_then_downgrade_base(sqlite_url: str) -> None:
    config = alembic_config(sqlite_url)
    command.upgrade(config, "head")

    engine = create_engine(sqlite_url.replace("+aiosqlite", ""))
    tables = set(inspect(engine).get_table_names())
    engine.dispose()
    assert {"tenants", "tenant_domains", "outbox_events", "admin_users", "customers"} <= tables

    command.downgrade(config, "base")
    engine = create_engine(sqlite_url.replace("+aiosqlite", ""))
    remaining = set(inspect(engine).get_table_names()) - {"alembic_version"}
    engine.dispose()
    assert remaining == set()


def test_alembic_url_escapes_percent_encoding() -> None:
    raw = "mysql+asyncmy://u:a%2Fb@127.0.0.1:3306/db"
    config = alembic_config(raw)
    assert config.get_main_option("sqlalchemy.url") == raw
    assert alembic_sqlalchemy_url(raw) == "mysql+asyncmy://u:a%%2Fb@127.0.0.1:3306/db"


def test_single_head_and_linear_history() -> None:
    script = ScriptDirectory.from_config(alembic_config())
    heads = script.get_heads()
    assert len(heads) == 1, heads


def test_models_match_migrations(sqlite_url: str) -> None:
    """`alembic check` on SQLite: a column the models changed but a migration did not (a type,
    a nullability, an index) fails here, without waiting for the MariaDB job."""
    config = alembic_config(sqlite_url)
    command.upgrade(config, "head")
    command.check(config)


@pytest.mark.skipif(not os.environ.get("MARIADB_TEST_URL"), reason="MariaDB not available")
def test_models_match_migrations_on_mariadb() -> None:
    """`alembic check` against a freshly migrated MariaDB: model drift fails CI."""
    url = os.environ["MARIADB_TEST_URL"]
    config = alembic_config(url)
    command.upgrade(config, "head")
    command.check(config)
    command.downgrade(config, "base")


def _insert(conn: Any, table: str, **values: Any) -> None:
    """Insere preenchendo sozinho o que é NOT NULL sem default (o teste só liga para o resto)."""
    import json as _json
    import uuid
    from datetime import UTC, datetime

    from sqlalchemy import Boolean, DateTime, Integer, MetaData, Table

    reflected = Table(table, MetaData(), autoload_with=conn)
    row = dict(values)
    for column in reflected.columns:
        if column.name in row or column.nullable or column.server_default is not None:
            continue
        if isinstance(column.type, Boolean):
            row[column.name] = False
        elif isinstance(column.type, Integer):
            row[column.name] = 0
        elif isinstance(column.type, DateTime):
            row[column.name] = datetime.now(UTC).replace(tzinfo=None)
        elif "JSON" in type(column.type).__name__.upper():
            row[column.name] = _json.dumps({})
        else:
            row[column.name] = uuid.uuid4().hex[:8]
    conn.execute(reflected.insert().values(**row))


def test_backfill_copia_as_caixas_sem_mudar_o_frete(sqlite_url: str) -> None:
    """0037: a caixa padrão vira embalagem padrão automática; as outras mantêm o id e só valem
    para quem aponta para elas; o produto que apontava vira `restricted` com uma regra."""
    import json as _json

    from sqlalchemy import text

    config = alembic_config(sqlite_url)
    command.upgrade(config, "0036_product_packing_traits")
    engine = create_engine(sqlite_url.replace("+aiosqlite", ""))
    tenant_id = "01a00000-0000-7000-8000-00000000aaaa"
    caixa_id = "01a00000-0000-7000-8000-00000000bbbb"
    fulfillment = {
        "shipping": {
            "box": {"width_mm": 300, "height_mm": 200, "depth_mm": 150, "empty_weight_grams": 120},
            "boxes": [
                {
                    "id": caixa_id,
                    "name": "Caixa grande",
                    "width_mm": 600,
                    "height_mm": 400,
                    "depth_mm": 400,
                    "max_weight_grams": 25000,
                }
            ],
        }
    }
    with engine.begin() as conn:
        _insert(conn, "tenants", id=tenant_id, slug="loja-backfill")
        _insert(
            conn,
            "tenant_settings",
            id="01a00000-0000-7000-8000-00000000cccc",
            tenant_id=tenant_id,
            key="fulfillment",
            value=_json.dumps(fulfillment),
        )
        _insert(
            conn,
            "products",
            id="01a00000-0000-7000-8000-00000000dddd",
            tenant_id=tenant_id,
            sku="RAB-1",
            slug="rabiola",
            shipping_box_id=caixa_id,
            packing_mode="auto",
            packing_rotation="any",
        )
        _insert(
            conn,
            "products",
            id="01a00000-0000-7000-8000-00000000eeee",
            tenant_id=tenant_id,
            sku="PIPA-1",
            slug="pipa",
            packing_mode="auto",
            packing_rotation="any",
        )
    engine.dispose()

    command.upgrade(config, "0037_backfill_shipping_packages")
    engine = create_engine(sqlite_url.replace("+aiosqlite", ""))
    with engine.connect() as conn:
        pacotes = conn.execute(
            text(
                "SELECT id, name, inner_length_mm, inner_width_mm, inner_height_mm, "
                "outer_length_mm, empty_weight_grams, max_weight_grams, auto_select, "
                "default_marker FROM shipping_packages ORDER BY position"
            )
        ).all()
        regras = conn.execute(
            text("SELECT product_id, package_id FROM product_package_rules")
        ).all()
        modos = dict(conn.execute(text("SELECT sku, packing_mode FROM products")).all())
    engine.dispose()

    padrao, grande = pacotes
    assert padrao.name == "Caixa padrão"
    # depth → comprimento; de fora = de dentro, para o preço não mudar com o backfill.
    assert (padrao.inner_length_mm, padrao.inner_width_mm, padrao.inner_height_mm) == (
        150,
        300,
        200,
    )
    assert padrao.outer_length_mm == 150
    assert (padrao.empty_weight_grams, padrao.max_weight_grams) == (120, 30000)
    assert (bool(padrao.auto_select), padrao.default_marker) == (True, 1)
    assert grande.id == caixa_id, "o id da caixa é mantido: é por ele que o produto aponta"
    assert (bool(grande.auto_select), grande.default_marker) == (False, None)
    assert grande.max_weight_grams == 25000
    assert [(r.product_id, r.package_id) for r in regras] == [
        ("01a00000-0000-7000-8000-00000000dddd", caixa_id)
    ]
    assert modos == {"RAB-1": "restricted", "PIPA-1": "auto"}

    command.downgrade(config, "0036_product_packing_traits")
    engine = create_engine(sqlite_url.replace("+aiosqlite", ""))
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM shipping_packages")).scalar() == 0
        assert set(
            dict(conn.execute(text("SELECT sku, packing_mode FROM products")).all()).values()
        ) == {"auto"}
    engine.dispose()
