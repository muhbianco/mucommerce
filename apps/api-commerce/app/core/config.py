from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import quote_plus

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "staging", "production"]


class Settings(BaseSettings):
    """Configuration read from the environment (and `.env` in development).

    Anything tenant-specific never lives here: it lives in `tenant_settings`,
    `tenant_feature_flags` and `tenant_integration_credentials`.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    environment: Environment = "development"
    log_level: str = "INFO"
    docs_enabled: bool = True
    api_title: str = "MuhBianco Commerce API"
    api_description: str = (
        "Backend da loja SaaS multi-tenant MuhBianco. Fonte da verdade de tenants, "
        "catálogo, pedidos, pagamentos, estoque e produção."
    )
    root_path: str = ""

    # --- database (runtime user: DML only) -------------------------------------------
    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_user: str = "mucommerce_app"
    db_password: SecretStr = SecretStr("")
    db_name: str = "mucommerce"
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_recycle_seconds: int = 1800
    # Full URL override (tests use sqlite+aiosqlite).
    database_url_override: str = ""

    # --- database (migration user: DDL on this database only) -----------------------
    migrate_db_user: str = "mucommerce_migrate"
    migrate_db_password: SecretStr = SecretStr("")

    # Production keeps this false: schema changes run in the `commerce_migrate` job.
    run_migrations_on_startup: bool = False

    # --- redis / celery -------------------------------------------------------------
    redis_url: str = ""  # cache, rate limit, locks (empty = in-memory fallbacks)
    celery_broker_url: str = ""  # empty = tasks run eagerly (dev/tests)
    celery_result_backend: str = ""

    # --- admin auth -----------------------------------------------------------------
    jwt_secret: SecretStr = SecretStr("")
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "api-commerce"
    jwt_audience: str = "api-commerce"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 14
    login_rate_limit_attempts: int = 10
    login_rate_limit_window_seconds: int = 300

    # --- internal service tokens (one per consumer, rotated independently) ----------
    internal_token_web: SecretStr = SecretStr("")
    internal_token_traefik: SecretStr = SecretStr("")
    internal_token_agents: SecretStr = SecretStr("")

    # --- tenant credentials at rest -------------------------------------------------
    # base64 (standard or urlsafe) of 32 random bytes. Generate: openssl rand -base64 32
    credentials_master_key: SecretStr = SecretStr("")
    credentials_key_version: int = 1

    # --- platform / edge ------------------------------------------------------------
    platform_tenant_slug: str = "muhbianco"
    # Storefront host of the platform tenant (the "loja modelo"); no staging, no aliases.
    platform_base_domain: str = "loja.muhbianco.com.br"
    panel_host: str = "painel.muhbianco.com.br"
    api_public_host: str = "api-commerce.muhbianco.com.br"
    edge_cname_target: str = "edge.muhbianco.com.br"
    edge_public_ips: str = ""  # comma separated A-record targets the tenant must point to
    edge_web_upstream: str = "http://commerce-web:3000"
    edge_api_upstream: str = "http://commerce-api:8000"
    edge_cert_resolver: str = "letsencryptresolver"
    chatwoot_public_url: str = "https://chatwoot.muhbianco.com.br"
    tenant_cache_ttl_seconds: int = 60
    dns_resolvers: str = "1.1.1.1,8.8.8.8"
    domain_verify_max_age_hours: int = 48

    # --- object storage (MinIO, S3 API) -----------------------------------------------
    # Empty endpoint = storage not configured: media routes answer 503, readiness omits it.
    storage_endpoint: str = ""  # internal S3 API, e.g. http://minio:9000
    # Public S3 API origin: browsers POST uploads here and read public media from it.
    storage_public_url: str = ""  # e.g. https://storage.s3.muhbianco.com.br
    storage_region: str = "us-east-1"
    storage_access_key: SecretStr = SecretStr("")
    storage_secret_key: SecretStr = SecretStr("")
    storage_public_bucket: str = "commerce-public"
    storage_private_bucket: str = "commerce-private"
    storage_timeout_seconds: float = 20.0

    # --- MuhBianco accounts (api-agents): staff sign in to the panel with their account -----
    # Internal URL used to redeem the one-time sign-in code (never leaves chatbot-net).
    muhbianco_accounts_internal_url: str = "http://api_agents:8000"
    muhbianco_accounts_timeout_seconds: float = 5.0

    # --- cors -----------------------------------------------------------------------
    cors_origins: str = ""

    # --- observability --------------------------------------------------------------
    sentry_dsn: str = ""
    metrics_enabled: bool = True

    # --- bootstrap (CLI only) -------------------------------------------------------
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: SecretStr = SecretStr("")

    @model_validator(mode="after")
    def _validate_production(self) -> Settings:
        if self.environment == "production":
            missing = [
                name
                for name, value in (
                    ("JWT_SECRET", self.jwt_secret),
                    ("DB_PASSWORD", self.db_password),
                    ("CREDENTIALS_MASTER_KEY", self.credentials_master_key),
                    ("INTERNAL_TOKEN_WEB", self.internal_token_web),
                    ("INTERNAL_TOKEN_TRAEFIK", self.internal_token_traefik),
                )
                if not value.get_secret_value()
            ]
            if missing:
                raise ValueError(f"Missing required production settings: {', '.join(missing)}")
            if self.run_migrations_on_startup:
                raise ValueError("RUN_MIGRATIONS_ON_STARTUP must be false in production")
        if len(self.jwt_secret.get_secret_value()) < 32 and self.environment != "development":
            raise ValueError("JWT_SECRET must have at least 32 characters")
        return self

    def _mysql_url(self, user: str, password: str, database: str | None) -> str:
        auth = quote_plus(user)
        if password:
            auth += f":{quote_plus(password)}"
        db = f"/{database}" if database else ""
        return f"mysql+asyncmy://{auth}@{self.db_host}:{self.db_port}{db}?charset=utf8mb4"

    @property
    def database_url(self) -> str:
        if self.database_url_override:
            return self.database_url_override
        return self._mysql_url(self.db_user, self.db_password.get_secret_value(), self.db_name)

    @property
    def migrate_database_url(self) -> str:
        if self.database_url_override:
            return self.database_url_override
        return self._mysql_url(
            self.migrate_db_user, self.migrate_db_password.get_secret_value(), self.db_name
        )

    @property
    def migrate_server_url(self) -> str:
        """Server-level URL (no database) used by `db ensure` to CREATE DATABASE."""
        return self._mysql_url(
            self.migrate_db_user, self.migrate_db_password.get_secret_value(), None
        )

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def edge_public_ip_list(self) -> list[str]:
        return [ip.strip() for ip in self.edge_public_ips.split(",") if ip.strip()]

    @property
    def static_edge_hosts(self) -> frozenset[str]:
        """Hosts routed by the stack's Swarm labels, not by the dynamic providers.http config.

        They never enter `build_traefik_config` (a second router for the same Host would
        race the label one) and DNS re-checks never take them offline: they live in our
        own zone, not in a tenant's.
        """
        return frozenset(
            host.strip().lower()
            for host in (self.platform_base_domain, self.panel_host, self.api_public_host)
            if host.strip()
        )

    @property
    def storage_configured(self) -> bool:
        return bool(
            self.storage_endpoint
            and self.storage_public_url
            and self.storage_access_key.get_secret_value()
            and self.storage_secret_key.get_secret_value()
        )

    @property
    def dns_resolver_list(self) -> list[str]:
        return [ip.strip() for ip in self.dns_resolvers.split(",") if ip.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def internal_token_for(self, consumer: str) -> str:
        mapping = {
            "web": self.internal_token_web,
            "traefik": self.internal_token_traefik,
            "agents": self.internal_token_agents,
        }
        secret = mapping.get(consumer)
        return secret.get_secret_value() if secret else ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
