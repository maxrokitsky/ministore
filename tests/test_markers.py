"""Inline schema declaration via ``Annotated`` markers (Key/Index/Unique)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import pytest

from ministore import Index, Key, MinistoreError, Store, Unique


@dataclass
class Account:
    id: Key[int]
    email: Unique[str]
    age: Index[int]
    name: str


def make_account() -> Account:
    return Account(id=1, email="a@b.c", age=30, name="Anna")


@pytest.fixture
def db(tmp_path: Path) -> Store:
    return Store(tmp_path / "markers.db")


def _index_names(store: Store, table: str) -> set[str]:
    conn = store.connection()
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?", (table,)
    ).fetchall()
    return {r[0] for r in rows}


def test_dataclass_markers_define_schema(db: Store) -> None:
    users = db.collection(Account)  # no key= needed: Key marker provides it
    assert users.table.key == "id"
    assert users.table.unique == ("email",)
    assert users.table.indexes == ("age",)

    names = _index_names(db, "Account")
    assert "uniq_Account_email" in names
    assert "idx_Account_age" in names

    users.put(make_account())
    assert users.get(1) == make_account()
    assert users.where(age=30).first() == make_account()


def test_markers_merge_with_kwargs(db: Store) -> None:
    # An explicit index adds to the marked one; a re-declared field de-dupes.
    users = db.collection(Account, indexes=["name", "age"], name="acc_merged")
    assert users.table.key == "id"
    assert set(users.table.indexes) == {"age", "name"}
    assert len(users.table.indexes) == 2  # 'age' not duplicated
    assert users.table.unique == ("email",)


def test_explicit_key_matching_marker_is_ok(db: Store) -> None:
    users = db.collection(Account, key="id", name="acc_okkey")
    assert users.table.key == "id"


def test_conflicting_key_raises(db: Store) -> None:
    with pytest.raises(MinistoreError) as exc:
        db.collection(Account, key="age", name="acc_badkey")
    assert "Conflicting key" in str(exc.value)


def test_no_key_anywhere_raises(db: Store) -> None:
    @dataclass
    class NoKey:
        a: int
        b: str

    with pytest.raises(MinistoreError) as exc:
        db.collection(NoKey)
    assert "No key" in str(exc.value)


def test_multiple_key_markers_raise(db: Store) -> None:
    @dataclass
    class TwoKeys:
        a: Key[int]
        b: Key[int]

    with pytest.raises(MinistoreError) as exc:
        db.collection(TwoKeys)
    assert "more than one Key" in str(exc.value)


def test_marker_composes_with_extra_annotated_metadata(db: Store) -> None:
    @dataclass
    class Composed:
        id: Key[int]
        code: Annotated[Unique[str], "free-form note"]

    users = db.collection(Composed)
    assert users.table.key == "id"
    assert users.table.unique == ("code",)
    # The inner type is still ``str`` — the extra metadata is ignored.
    assert users.table.column("code").sqlite_type == "TEXT"
    users.put(Composed(id=1, code="abc"))
    assert users.get(1) == Composed(id=1, code="abc")


def test_unique_index_is_unique(db: Store) -> None:
    users = db.collection(Account, name="acc_uniq")
    users.put(make_account())
    conn = db.connection()
    # INSERT OR REPLACE upserts by PK, so duplicate the unique column on a *new* PK.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            'INSERT INTO acc_uniq ("id", "email", "age", "name") VALUES (?, ?, ?, ?)',
            (2, "a@b.c", 40, "Bob"),
        )


def _pydantic_account() -> type[Any]:
    import pydantic

    class PAccount(pydantic.BaseModel):
        id: Key[int]
        email: Unique[str]
        age: Index[int]
        name: str

    return PAccount


def _msgspec_account() -> type[Any]:
    import msgspec

    class MAccount(msgspec.Struct):
        id: Key[int]
        email: Unique[str]
        age: Index[int]
        name: str

    return MAccount


def test_pydantic_markers(db: Store) -> None:
    pytest.importorskip("pydantic")
    model = _pydantic_account()
    users = db.collection(model, name="PAccount")
    assert users.table.key == "id"
    assert users.table.unique == ("email",)
    assert users.table.indexes == ("age",)
    users.put(model(id=1, email="a@b.c", age=30, name="Anna"))
    assert users.get(1) == model(id=1, email="a@b.c", age=30, name="Anna")


def test_msgspec_markers(db: Store) -> None:
    pytest.importorskip("msgspec")
    model = _msgspec_account()
    users = db.collection(model, name="MAccount")
    assert users.table.key == "id"
    assert users.table.unique == ("email",)
    assert users.table.indexes == ("age",)
    users.put(model(id=1, email="a@b.c", age=30, name="Anna"))
    assert users.get(1) == model(id=1, email="a@b.c", age=30, name="Anna")
