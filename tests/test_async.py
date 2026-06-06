"""Tests for the asynchronous AsyncStore/AsyncCollection/AsyncQuery."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest

from ministore import AsyncStore, Index, Key, Unique
from tests.models import DUser, Role

pytest.importorskip("aiosqlite")


@dataclass
class AAccount:
    id: Key[int]
    email: Unique[str]
    age: Index[int]


def make_user(uid: int, name: str, age: int) -> DUser:
    return DUser(id=uid, name=name, age=age, role=Role.user, created=datetime(2020, 1, 1, 12, 0))


async def test_async_crud(tmp_path: Path) -> None:
    async with AsyncStore(tmp_path / "a.db") as db:
        users = await db.collection(DUser, key="id")
        await users.put(make_user(1, "Anna", 30))
        got = await users.get(1)
        assert got is not None and got.name == "Anna"
        assert await users.delete(1) is True
        assert await users.get(1) is None


async def test_async_put_many_and_query(tmp_path: Path) -> None:
    async with AsyncStore(tmp_path / "a.db") as db:
        users = await db.collection(DUser, key="id")
        await users.put_many([make_user(i, f"u{i}", 10 * i) for i in range(1, 5)])
        assert await users.count() == 4

        adults = await users.where(age__gte=20).order_by("-age").all()
        assert [u.id for u in adults] == [4, 3, 2]

        ids = {u.id async for u in users.where(age__lt=30)}
        assert ids == {1, 2}

        first = await users.where(age__gte=30).order_by("age").first()
        assert first is not None and first.id == 3

        assert await users.where(age__lt=30).delete() == 2
        assert await users.count() == 2


async def test_async_markers(tmp_path: Path) -> None:
    async with AsyncStore(tmp_path / "am.db") as db:
        accounts = await db.collection(AAccount)  # key/indexes from markers
        assert accounts.table.key == "id"
        assert accounts.table.unique == ("email",)
        assert accounts.table.indexes == ("age",)
        await accounts.put(AAccount(id=1, email="a@b.c", age=30))
        assert await accounts.get(1) == AAccount(id=1, email="a@b.c", age=30)


async def test_async_persistence(tmp_path: Path) -> None:
    path = tmp_path / "ap.db"
    async with AsyncStore(path) as db:
        users = await db.collection(DUser, key="id")
        await users.put(make_user(7, "x", 7))
    async with AsyncStore(path) as db:
        users = await db.collection(DUser, key="id")
        got = await users.get(7)
        assert got is not None and got.name == "x"


async def test_async_transaction_commit(tmp_path: Path) -> None:
    async with AsyncStore(tmp_path / "t.db") as db:
        users = await db.collection(DUser, key="id")
        async with db.transaction():
            await users.put(make_user(1, "a", 1))
            await users.put(make_user(2, "b", 2))
        assert await users.count() == 2
        assert db.in_transaction is False


async def test_async_transaction_rollback(tmp_path: Path) -> None:
    async with AsyncStore(tmp_path / "t.db") as db:
        users = await db.collection(DUser, key="id")
        await users.put(make_user(1, "a", 1))
        with pytest.raises(RuntimeError):
            async with db.transaction():
                await users.put(make_user(2, "b", 2))
                raise RuntimeError("boom")
        assert {u.id for u in await users.all()} == {1}
        assert db.in_transaction is False


async def test_async_explicit_primitives(tmp_path: Path) -> None:
    async with AsyncStore(tmp_path / "t.db") as db:
        users = await db.collection(DUser, key="id")
        await db.begin()
        await users.put(make_user(1, "a", 1))
        await db.rollback()
        assert await users.get(1) is None
        with pytest.raises(RuntimeError):
            await db.commit()  # nothing open


async def test_async_nested_savepoint(tmp_path: Path) -> None:
    async with AsyncStore(tmp_path / "t.db") as db:
        users = await db.collection(DUser, key="id")
        async with db.transaction():
            await users.put(make_user(1, "a", 1))
            with pytest.raises(RuntimeError):
                async with db.transaction():
                    await users.put(make_user(2, "b", 2))
                    raise RuntimeError("inner")
            await users.put(make_user(3, "c", 3))
        assert {u.id for u in await users.all()} == {1, 3}


async def test_async_transaction_serializes_other_tasks(tmp_path: Path) -> None:
    async with AsyncStore(tmp_path / "t.db") as db:
        users = await db.collection(DUser, key="id")
        order: list[str] = []
        tx_open = asyncio.Event()

        async def in_tx() -> None:
            async with db.transaction():
                await users.put(make_user(1, "a", 1))
                tx_open.set()
                await asyncio.sleep(0.05)  # hold the transaction open
                order.append("tx-end")

        async def autonomous() -> None:
            await tx_open.wait()
            await users.put(make_user(2, "b", 2))  # must wait for the tx to release
            order.append("auto")

        await asyncio.gather(in_tx(), autonomous())
        assert order == ["tx-end", "auto"]  # the autonomous write did not interleave
        assert await users.count() == 2
