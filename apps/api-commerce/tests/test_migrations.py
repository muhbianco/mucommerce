"""Migrations must apply and revert on a real engine (SQLite here, MariaDB in CI)."""

from __future__ import annotations

import os
from pathlib import Path

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
