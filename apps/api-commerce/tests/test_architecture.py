"""Architecture rules the code must keep (E11-02 and the reservation invariant).

- An `Order` row is built only in `OrderService._insert_order`, and that is called only from
  `OrderService.place`: every order — storefront, panel, agents — goes through the same gate.
- An `InventoryReservation` is built only in `ReservationService`, which keeps reserved_milli
  and the reservations in step under the balance lock.
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
