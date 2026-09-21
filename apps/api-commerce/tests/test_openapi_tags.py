from __future__ import annotations

from fastapi.routing import APIRoute

from app.api.v1.router import TAGS_METADATA, router


def test_every_route_tag_is_documented() -> None:
    """Regression: a mojibake tag ("Ops â€” Tenants") silently split the docs into two groups."""
    documented = {str(tag["name"]) for tag in TAGS_METADATA}
    used = {tag for route in router.routes if isinstance(route, APIRoute) for tag in route.tags}
    assert used <= documented, sorted(used - documented)
