"""Tests for the synchronous Store/Collection/Query."""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

import pytest

from ministore import Store
from tests.models import DUser, Role


def make_user(uid: int, name: str, age: int) -> DUser:
    return DUser(id=uid, name=name, age=age, role=Role.user, created=datetime(2020, 1, 1, 12, 0))


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "app.db")


def test_put_get_roundtrip(store: Store) -> None:
    users = store.collection(DUser, key="id")
    u = DUser(
        id=1,
        name="Anna",
        age=30,
        role=Role.admin,
        created=datetime(2021, 5, 1, 9, 30),
        tags=["x", "y"],
    )
    users.put(u)
    got = users.get(1)
    assert got == u
    assert got is not None and got.role is Role.admin
    assert got.tags == ["x", "y"]


def test_get_missing_returns_none(store: Store) -> None:
    users = store.collection(DUser, key="id")
    assert users.get(999) is None


def test_put_is_upsert(store: Store) -> None:
    users = store.collection(DUser, key="id")
    users.put(make_user(1, "a", 20))
    users.put(make_user(1, "b", 21))
    assert users.count() == 1
    got = users.get(1)
    assert got is not None and got.name == "b" and got.age == 21


def test_delete(store: Store) -> None:
    users = store.collection(DUser, key="id")
    users.put(make_user(1, "a", 20))
    assert users.delete(1) is True
    assert users.delete(1) is False
    assert users.get(1) is None


def test_put_many_and_len_and_contains(store: Store) -> None:
    users = store.collection(DUser, key="id")
    n = users.put_many([make_user(i, f"u{i}", 20 + i) for i in range(5)])
    assert n == 5
    assert len(users) == 5
    assert 3 in users
    assert 99 not in users


def test_where_filters(store: Store) -> None:
    users = store.collection(DUser, key="id")
    users.put_many([make_user(i, f"u{i}", 10 * i) for i in range(1, 5)])  # ages 10,20,30,40

    assert {u.id for u in users.where(age__gte=20)} == {2, 3, 4}
    assert {u.id for u in users.where(age__gt=20, age__lt=40)} == {3}
    assert {u.id for u in users.where(id__in=[1, 4])} == {1, 4}
    assert {u.id for u in users.where(name__startswith="u1")} == {1}
    assert {u.id for u in users.where(name__contains="3")} == {3}


def test_isnull_filter(store: Store) -> None:
    users = store.collection(DUser, key="id")
    users.put(
        DUser(id=1, name="a", age=20, role=Role.user, created=datetime(2020, 1, 1), nickname="nick")
    )
    users.put(make_user(2, "b", 21))  # nickname=None
    assert {u.id for u in users.where(nickname__isnull=True)} == {2}
    assert {u.id for u in users.where(nickname__isnull=False)} == {1}


def test_order_limit_offset_first(store: Store) -> None:
    users = store.collection(DUser, key="id")
    users.put_many([make_user(i, f"u{i}", 10 * i) for i in range(1, 6)])

    ordered = [u.id for u in users.where().order_by("-age")]
    assert ordered == [5, 4, 3, 2, 1]

    page = users.where().order_by("age").limit(2).offset(1).all()
    assert [u.id for u in page] == [2, 3]

    first = users.where(age__gte=30).order_by("age").first()
    assert first is not None and first.id == 3
    assert users.where(age__gt=1000).first() is None


def test_count_exists_clear(store: Store) -> None:
    users = store.collection(DUser, key="id")
    users.put_many([make_user(i, f"u{i}", i) for i in range(3)])
    assert users.where(age__gte=1).count() == 2
    assert users.where(age__gte=1).exists() is True
    assert users.where(age__gt=100).exists() is False
    assert users.clear() == 3
    assert users.count() == 0


def test_bulk_delete(store: Store) -> None:
    users = store.collection(DUser, key="id")
    users.put_many([make_user(i, f"u{i}", i) for i in range(5)])
    removed = users.where(age__lt=3).delete()
    assert removed == 3
    assert users.count() == 2


def test_persistence_across_reopen(tmp_path: Path) -> None:
    path = tmp_path / "persist.db"
    with Store(path) as db:
        db.collection(DUser, key="id").put(make_user(1, "a", 20))
    with Store(path) as db:
        got = db.collection(DUser, key="id").get(1)
        assert got is not None and got.name == "a"


def test_thread_safety(store: Store) -> None:
    users = store.collection(DUser, key="id")

    def worker(base: int) -> None:
        for i in range(base, base + 50):
            users.put(make_user(i, f"u{i}", i))

    threads = [threading.Thread(target=worker, args=(b,)) for b in (0, 100, 200, 300)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert users.count() == 200
