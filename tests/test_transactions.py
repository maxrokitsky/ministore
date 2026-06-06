"""Tests for transaction primitives (begin/commit/rollback) and transaction()."""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

import pytest

from ministore import Store
from tests.models import DUser, Role


def make_user(uid: int, name: str = "x", age: int = 1) -> DUser:
    return DUser(id=uid, name=name, age=age, role=Role.user, created=datetime(2020, 1, 1))


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "tx.db")


def test_transaction_commits_on_success(store: Store) -> None:
    users = store.collection(DUser, key="id")
    with store.transaction():
        users.put(make_user(1))
        users.put(make_user(2))
    assert users.count() == 2
    assert store.in_transaction is False


def test_transaction_rolls_back_on_error(store: Store) -> None:
    users = store.collection(DUser, key="id")
    users.put(make_user(1))
    with pytest.raises(RuntimeError):
        with store.transaction():
            users.put(make_user(2))
            users.put(make_user(3))
            raise RuntimeError("boom")
    assert {u.id for u in users.all()} == {1}  # only the pre-transaction row survives
    assert store.in_transaction is False


def test_explicit_begin_commit(store: Store) -> None:
    users = store.collection(DUser, key="id")
    store.begin()
    assert store.in_transaction is True
    users.put(make_user(1))
    store.commit()
    assert store.in_transaction is False
    assert users.get(1) is not None


def test_explicit_begin_rollback(store: Store) -> None:
    users = store.collection(DUser, key="id")
    store.begin()
    users.put(make_user(1))
    store.rollback()
    assert store.in_transaction is False
    assert users.get(1) is None


def test_commit_without_begin_raises(store: Store) -> None:
    store.collection(DUser, key="id")
    with pytest.raises(RuntimeError):
        store.commit()


def test_rollback_without_begin_raises(store: Store) -> None:
    store.collection(DUser, key="id")
    with pytest.raises(RuntimeError):
        store.rollback()


def test_transaction_spans_collections(store: Store) -> None:
    users = store.collection(DUser, key="id", name="users")
    archive = store.collection(DUser, key="id", name="archive")
    with store.transaction():
        users.put(make_user(1))
        archive.put(make_user(2))
    assert users.get(1) is not None and archive.get(2) is not None

    with pytest.raises(RuntimeError):
        with store.transaction():
            users.put(make_user(3))
            archive.put(make_user(4))
            raise RuntimeError("boom")
    assert users.get(3) is None and archive.get(4) is None  # both collections rolled back


def test_nested_inner_rollback_outer_commits(store: Store) -> None:
    users = store.collection(DUser, key="id")
    with store.transaction():
        users.put(make_user(1))  # outer
        with pytest.raises(RuntimeError):
            with store.transaction():
                users.put(make_user(2))  # inner — savepoint
                raise RuntimeError("inner")
        # inner savepoint rolled back, outer continues
        users.put(make_user(3))
    assert {u.id for u in users.all()} == {1, 3}  # inner row (2) gone, outer rows kept


def test_nested_outer_rollback_undoes_inner_commit(store: Store) -> None:
    users = store.collection(DUser, key="id")
    with pytest.raises(RuntimeError):
        with store.transaction():
            users.put(make_user(1))
            with store.transaction():
                users.put(make_user(2))  # inner commits (releases savepoint)
            raise RuntimeError("outer")
    assert users.count() == 0  # outer rollback discards everything, including inner


def test_put_many_atomic_in_transaction(store: Store) -> None:
    users = store.collection(DUser, key="id")
    with pytest.raises(RuntimeError):
        with store.transaction():
            users.put_many([make_user(i) for i in range(5)])
            raise RuntimeError("boom")
    assert users.count() == 0  # the whole batch rolled back with the transaction


def test_in_transaction_is_thread_local(store: Store) -> None:
    store.collection(DUser, key="id")
    started = threading.Event()
    proceed = threading.Event()
    seen: list[bool] = []

    def worker() -> None:
        store.begin()  # no writes -> no write-lock contention with the main thread
        seen.append(store.in_transaction)
        started.set()
        proceed.wait()
        store.commit()

    t = threading.Thread(target=worker)
    t.start()
    started.wait()
    assert store.in_transaction is False  # main thread has its own per-thread state
    proceed.set()
    t.join()
    assert seen == [True]
