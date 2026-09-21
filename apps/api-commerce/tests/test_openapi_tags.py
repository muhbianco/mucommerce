from __future__ import annotations

from fastapi.routing import APIRoute

from app.api.v1.router import ENDPOINT_ROUTERS, TAGS_METADATA


def test_every_route_tag_is_documented() -> None:
    """Regression: a mojibake tag ("Ops â€” Tenants") silently split the docs."""
    documented = {str(tag["name"]) for tag in TAGS_METADATA}
    routes = [r for router in ENDPOINT_ROUTERS for r in router.routes if isinstance(r, APIRoute)]
    used = {tag for route in routes for tag in route.tags}
    assert len(routes) > 10 and used, "route discovery found nothing"
    assert used <= documented, sorted(used - documented)
