"""Round-trip for all three kinds of models."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from ministore import Store
from tests.models import DUser, Role, make_msgspec, make_pydantic


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "models.db")


def test_dataclass_roundtrip(store: Store) -> None:
    users = store.collection(DUser, key="id")
    u = DUser(
        id=1,
        name="Anna",
        age=30,
        role=Role.admin,
        created=datetime(2021, 1, 2, 3, 4),
        tags=["a", "b"],
        nickname="ann",
    )
    users.put(u)
    assert users.get(1) == u


def test_pydantic_roundtrip(store: Store) -> None:
    pytest.importorskip("pydantic")
    PUser = make_pydantic()
    users = store.collection(PUser, key="id")
    u = PUser(
        id=1, name="Bob", age=25, role=Role.user, created=datetime(2021, 1, 2, 3, 4), tags=["q"]
    )
    users.put(u)
    assert users.get(1) == u


def test_msgspec_roundtrip(store: Store) -> None:
    pytest.importorskip("msgspec")
    MUser = make_msgspec()
    users = store.collection(MUser, key="id")
    u = MUser(
        id=1, name="Vera", age=40, role=Role.admin, created=datetime(2021, 1, 2, 3, 4), tags=["z"]
    )
    users.put(u)
    assert users.get(1) == u


def test_query_works_for_pydantic(store: Store) -> None:
    pytest.importorskip("pydantic")
    PUser = make_pydantic()
    users = store.collection(PUser, key="id")
    users.put_many(
        [
            PUser(id=i, name=f"u{i}", age=10 * i, role=Role.user, created=datetime(2020, 1, 1))
            for i in range(1, 4)
        ]
    )
    assert {u.id for u in users.where(age__gte=20, role=Role.user)} == {2, 3}
