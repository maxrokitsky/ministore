"""Tests for typed projections: ``Query.as_model`` / ``Collection.as_model``."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest

from ministore import AsyncStore, QueryError, Store
from tests.models import (
    DUser,
    DUserBrief,
    Role,
    make_msgspec_brief,
    make_pydantic_brief,
)


def make_user(uid: int, name: str, age: int) -> DUser:
    return DUser(id=uid, name=name, age=age, role=Role.user, created=datetime(2020, 1, 1, 12, 0))


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "app.db")


def _seed(store: Store) -> None:
    users = store.collection(DUser, key="id")
    users.put_many([make_user(i, f"u{i}", 10 * i) for i in range(1, 5)])  # ages 10,20,30,40


def test_projection_returns_subset(store: Store) -> None:
    _seed(store)
    users = store.collection(DUser, key="id")

    briefs: list[DUserBrief] = users.where().order_by("id").as_model(DUserBrief).all()

    assert briefs == [DUserBrief(id=i, name=f"u{i}") for i in range(1, 5)]
    assert all(isinstance(b, DUserBrief) for b in briefs)


def test_projection_filter_on_omitted_field(store: Store) -> None:
    """A field omitted from the view is still usable in WHERE."""
    _seed(store)
    users = store.collection(DUser, key="id")

    briefs = users.where(age__gte=30).order_by("id").as_model(DUserBrief).all()
    assert [b.id for b in briefs] == [3, 4]


def test_projection_order_by_omitted_field(store: Store) -> None:
    _seed(store)
    users = store.collection(DUser, key="id")

    briefs = users.where().order_by("-age").as_model(DUserBrief).all()
    assert [b.id for b in briefs] == [4, 3, 2, 1]


def test_projection_order_independent(store: Store) -> None:
    """as_model before/after where produces the same result."""
    _seed(store)
    users = store.collection(DUser, key="id")

    before = users.as_model(DUserBrief).where(age__gte=30).order_by("id").all()
    after = users.where(age__gte=30).as_model(DUserBrief).order_by("id").all()
    assert before == after == [DUserBrief(id=3, name="u3"), DUserBrief(id=4, name="u4")]


def test_collection_as_model_shortcut(store: Store) -> None:
    _seed(store)
    users = store.collection(DUser, key="id")

    briefs = users.as_model(DUserBrief).order_by("id").all()
    assert [b.id for b in briefs] == [1, 2, 3, 4]


def test_projection_reordered_columns(store: Store) -> None:
    _seed(store)
    users = store.collection(DUser, key="id")

    @dataclass
    class NameThenId:
        name: str
        id: int

    rows = users.where(id=2).as_model(NameThenId).all()
    assert rows == [NameThenId(name="u2", id=2)]


def test_projection_first(store: Store) -> None:
    _seed(store)
    users = store.collection(DUser, key="id")

    brief = users.where(age__gte=30).order_by("age").as_model(DUserBrief).first()
    assert brief == DUserBrief(id=3, name="u3")
    assert users.where(age__gt=1000).as_model(DUserBrief).first() is None


def test_projection_unknown_field_raises(store: Store) -> None:
    users = store.collection(DUser, key="id")

    @dataclass
    class Bad:
        id: int
        nope: int

    with pytest.raises(QueryError):
        users.as_model(Bad)


def test_projection_empty_model_raises(store: Store) -> None:
    users = store.collection(DUser, key="id")

    @dataclass
    class Empty:
        pass

    with pytest.raises(QueryError):
        users.as_model(Empty)


def test_projection_into_pydantic_view(store: Store) -> None:
    """A dataclass collection can be projected into a pydantic view."""
    pytest.importorskip("pydantic")
    _seed(store)
    users = store.collection(DUser, key="id")
    PUserBrief = make_pydantic_brief()

    briefs = users.where(id=1).as_model(PUserBrief).all()
    assert len(briefs) == 1
    assert briefs[0].id == 1 and briefs[0].name == "u1"


def test_projection_into_msgspec_view(store: Store) -> None:
    pytest.importorskip("msgspec")
    _seed(store)
    users = store.collection(DUser, key="id")
    MUserBrief = make_msgspec_brief()

    briefs = users.where(id=1).as_model(MUserBrief).all()
    assert len(briefs) == 1
    assert briefs[0].id == 1 and briefs[0].name == "u1"


async def test_async_projection(tmp_path: Path) -> None:
    pytest.importorskip("aiosqlite")
    async with AsyncStore(tmp_path / "a.db") as db:
        users = await db.collection(DUser, key="id")
        await users.put_many([make_user(i, f"u{i}", 10 * i) for i in range(1, 5)])

        briefs = await users.where(age__gte=30).order_by("id").as_model(DUserBrief).all()
        assert briefs == [DUserBrief(id=3, name="u3"), DUserBrief(id=4, name="u4")]

        ids = {b.id async for b in users.as_model(DUserBrief)}
        assert ids == {1, 2, 3, 4}

        first = await users.as_model(DUserBrief).order_by("-age").first()
        assert first == DUserBrief(id=4, name="u4")
