"""Architecture rules the code must keep (E11-02 and the reservation invariant).

- An `Order` row is built only in `OrderService._insert_order`, and that is called only from
  `OrderService.place`: every order — storefront, panel, agents — goes through the same gate.
- An `InventoryReservation` is built only in `ReservationService`, which keeps reserved_milli
  and the reservations in step under the balance lock.
- An order is cancelled only through `payments.cancellation.cancel_order`, which requests the
  refund of a paid order in the same transaction: no path cancels a paid order and keeps the
  money.
- Money-moving provider calls (`create_charge`, `fetch_status`, `cancel` on a provider) happen
  only in `PaymentService`, whose round trips commit first and lock after: no transaction or
  lock is ever held while a payment provider answers.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"


def _sources() -> list[tuple[Path, ast.Module, str]]:
    found = []
    for path in sorted(APP.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        found.append((path, ast.parse(text), text))
    return found


def _calls(tree: ast.AST, name: str) -> list[tuple[ast.Call, list[str]]]:
    """Calls to `name(...)` or `x.name(...)` with the enclosing class/function names."""
    result: list[tuple[ast.Call, list[str]]] = []

    def visit(node: ast.AST, scope: list[str]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope = [*scope, node.name]
        if isinstance(node, ast.Call):
            func = node.func
            called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if called == name:
                result.append((node, scope))
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    visit(tree, [])
    return result


def test_only_order_service_builds_an_order() -> None:
    offenders = []
    for path, tree, text in _sources():
        rel = path.relative_to(APP).as_posix()
        for _call, scope in _calls(tree, "Order"):
            if (rel, scope[-2:]) != ("orders/service.py", ["OrderService", "_insert_order"]):
                offenders.append(f"{rel}: Order(...) in {'.'.join(scope)}")
        for _call, scope in _calls(tree, "_insert_order"):
            if (rel, scope[-2:]) != ("orders/service.py", ["OrderService", "place"]):
                offenders.append(f"{rel}: _insert_order in {'.'.join(scope)}")
        if re.search(r"insert\s+into\s+orders\b", text, re.IGNORECASE):
            offenders.append(f"{rel}: raw INSERT INTO orders")
        if re.search(r"insert\(\s*Order\b|Order\.__table__", text):
            offenders.append(f"{rel}: Core insert on orders")
    assert not offenders, offenders


def test_only_the_reservation_service_builds_reservations() -> None:
    offenders = [
        f"{path.relative_to(APP).as_posix()}: {'.'.join(scope)}"
        for path, tree, _ in _sources()
        for _call, scope in _calls(tree, "InventoryReservation")
        if path.relative_to(APP).as_posix() != "inventory/reservations.py"
        or scope[:1] != ["ReservationService"]
    ]
    assert not offenders, offenders


def test_only_the_payment_service_calls_payment_providers() -> None:
    offenders = []
    for path, tree, _ in _sources():
        rel = path.relative_to(APP).as_posix()
        if rel.startswith("payments/providers/"):
            continue
        for name in ("create_charge", "fetch_status"):
            for _call, scope in _calls(tree, name):
                if rel != "payments/service.py" or scope[:1] != ["PaymentService"]:
                    offenders.append(f"{rel}: {name} in {'.'.join(scope)}")
        for call, scope in _calls(tree, "cancel"):
            receiver = call.func.value if isinstance(call.func, ast.Attribute) else None
            outside = rel != "payments/service.py" or scope[:1] != ["PaymentService"]
            if isinstance(receiver, ast.Name) and receiver.id == "provider" and outside:
                offenders.append(f"{rel}: provider.cancel in {'.'.join(scope)}")
    assert not offenders, offenders


def test_orders_are_cancelled_only_with_their_refund() -> None:
    """Any `.cancel(...)` call is either the provider round trip inside PaymentService or the
    order cancel inside `cancel_order` (whatever variable holds the OrderService)."""
    allowed = {
        ("payments/cancellation.py", "cancel_order"),
        ("payments/service.py", "_cancel_at_provider"),
    }
    offenders = [
        f"{path.relative_to(APP).as_posix()}: .cancel in {'.'.join(scope)}"
        for path, tree, _ in _sources()
        for call, scope in _calls(tree, "cancel")
        if isinstance(call.func, ast.Attribute)
        and (path.relative_to(APP).as_posix(), scope[-1]) not in allowed
    ]
    assert not offenders, offenders


def test_only_the_landing_gateway_talks_to_a_model() -> None:
    """A chave mora na api-agents e o livro-caixa só fecha se toda chamada passar pelo mesmo
    lugar. Um segundo caminho, mesmo "temporário", é uma segunda cópia da chave na VPS e uma
    fatura que ninguém sabe explicar.

    Duas provas: quem pode **importar** o gateway, e quem pode **chamá-lo**.
    """
    pode_importar = {
        "landing/gateway.py",
        "landing/generation.py",
        "workers/landing.py",
    }
    offenders = []
    for path, tree, _ in _sources():
        rel = path.relative_to(APP).as_posix()
        for node in ast.walk(tree):
            importa = (
                isinstance(node, ast.ImportFrom)
                and (node.module or "").startswith("app.landing.gateway")
            ) or (
                isinstance(node, ast.Import)
                and any(a.name.startswith("app.landing.gateway") for a in node.names)
            )
            if importa and rel not in pode_importar:
                offenders.append(f"{rel}: importa o gateway do modelo")
        for call, scope in _calls(tree, "complete"):
            receiver = call.func.value if isinstance(call.func, ast.Attribute) else None
            chama_gateway = isinstance(receiver, ast.Name) and receiver.id in {"gateway", "llm"}
            if chama_gateway and rel != "landing/generation.py":
                offenders.append(f"{rel}: gateway.complete em {'.'.join(scope)}")
    assert not offenders, offenders


def test_a_draft_is_never_written_straight_into_settings() -> None:
    """Publicar uma proposta usa a mesma porta da edição à mão (`TenantService.set_setting`),
    que valida, confere referências e audita. Um atalho daqui para `tenant_settings` seriam dois
    caminhos de escrita, e um deles esquecido na próxima mudança."""
    landing = [p for p, _, _ in _sources() if p.relative_to(APP).as_posix().startswith("landing/")]
    offenders = []
    for path in landing:
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(APP).as_posix()
        if "TenantSetting(" in text:
            offenders.append(f"{rel}: constrói TenantSetting direto")
        if re.search(r"\bset_setting\b", text) and rel != "landing/drafts.py":
            offenders.append(f"{rel}: escreve setting fora do serviço de propostas")
    assert not offenders, offenders


def test_the_prompt_builder_never_touches_the_database() -> None:
    """`prompt.py` é função pura sobre os snapshots. É a forma mais barata de garantir que nada
    além do brief e do inventário chegue perto de um serviço externo: não existe caminho."""
    text = (APP / "landing" / "prompt.py").read_text(encoding="utf-8")
    proibidos = ["AsyncSession", "session", "select(", "await "]
    offenders = [termo for termo in proibidos if termo in text]
    assert not offenders, f"prompt.py tocou no banco: {offenders}"


def test_the_packing_engine_is_pure_and_deterministic() -> None:
    """O motor de embalagem (frete v2) é puro: o hash do plano vai na assinatura da cotação e é
    reconstruído no `place`, então a mesma entrada tem de dar o mesmo plano. Sem banco, sem
    rede, sem aleatoriedade e sem relógio decidindo nada."""
    proibidos = {
        "sqlalchemy",
        "httpx",
        "asyncio",
        "random",
        "time",
        "datetime",
        "app.core",
        "app.tenancy",
        "app.catalog",
    }
    offenders = []
    for path in sorted((APP / "shipping" / "packing").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            nomes: list[str] = []
            if isinstance(node, ast.Import):
                nomes = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                nomes = [node.module]
            for nome in nomes:
                if any(nome == p or nome.startswith(f"{p}.") for p in proibidos):
                    offenders.append(f"{path.name}: {nome}")
            if isinstance(node, (ast.Await, ast.AsyncFunctionDef)):
                offenders.append(f"{path.name}: async")
    assert not offenders, offenders
