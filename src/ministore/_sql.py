"""SQL (DDL/DML) generation and schema verification.

This module only builds SQL strings — execution lives in the sync and async
layers. All identifiers are escaped, and values are always passed through
parameter placeholders.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from ._schema import Table
from .exceptions import SchemaMismatchError


def quote(identifier: str) -> str:
    """Escapes a SQL identifier (table/column name)."""
    return '"' + identifier.replace('"', '""') + '"'


BEGIN_SQL = "BEGIN"
COMMIT_SQL = "COMMIT"
ROLLBACK_SQL = "ROLLBACK"


def savepoint_name(depth: int) -> str:
    """Name of the savepoint opened at the given (zero-based) nesting depth."""
    return f"ministore_sp_{depth}"


def savepoint_sql(name: str) -> str:
    return f"SAVEPOINT {quote(name)}"


def release_sql(name: str) -> str:
    return f"RELEASE {quote(name)}"


def rollback_to_sql(name: str) -> str:
    return f"ROLLBACK TO {quote(name)}"


def create_table_sql(table: Table) -> str:
    cols: list[str] = []
    for col in table.columns:
        parts = [quote(col.name), col.sqlite_type]
        if col.name == table.key:
            parts.append("PRIMARY KEY")
        elif not col.nullable:
            parts.append("NOT NULL")
        cols.append(" ".join(parts))
    body = ", ".join(cols)
    return f"CREATE TABLE IF NOT EXISTS {quote(table.name)} ({body})"


def create_index_sql(table: Table) -> list[str]:
    statements: list[str] = []
    for field in table.indexes:
        idx = f"idx_{table.name}_{field}"
        statements.append(
            f"CREATE INDEX IF NOT EXISTS {quote(idx)} ON {quote(table.name)} ({quote(field)})"
        )
    for field in table.unique:
        idx = f"uniq_{table.name}_{field}"
        statements.append(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {quote(idx)} "
            f"ON {quote(table.name)} ({quote(field)})"
        )
    return statements


def insert_sql(table: Table) -> str:
    names = [quote(col.name) for col in table.columns]
    placeholders = ", ".join("?" for _ in table.columns)
    return (
        f"INSERT OR REPLACE INTO {quote(table.name)} ({', '.join(names)}) VALUES ({placeholders})"
    )


def get_sql(table: Table) -> str:
    cols = ", ".join(quote(col.name) for col in table.columns)
    return f"SELECT {cols} FROM {quote(table.name)} WHERE {quote(table.key)} = ?"


def delete_one_sql(table: Table) -> str:
    return f"DELETE FROM {quote(table.name)} WHERE {quote(table.key)} = ?"


def select_sql(table: Table, where: str, tail: str) -> str:
    cols = ", ".join(quote(col.name) for col in table.columns)
    return f"SELECT {cols} FROM {quote(table.name)}{where}{tail}"


def count_sql(table: Table, where: str) -> str:
    return f"SELECT COUNT(*) FROM {quote(table.name)}{where}"


def delete_where_sql(table: Table, where: str) -> str:
    return f"DELETE FROM {quote(table.name)}{where}"


def table_info_sql(table: Table) -> str:
    return f"PRAGMA table_info({quote(table.name)})"


def check_schema(table: Table, existing: Iterable[Mapping[str, object]]) -> None:
    """Compares the expected schema against the actual one (PRAGMA table_info).

    Raises SchemaMismatchError on any difference. Altering an existing schema is
    the job of the ``ministore-migrate`` package; the core only checks it.
    """
    actual = {str(row["name"]): str(row["type"]).upper() for row in existing}
    if not actual:
        return  # table does not exist yet — it will be created from scratch

    expected = {col.name: col.sqlite_type for col in table.columns}
    problems: list[str] = []

    for name, sqlite_type in expected.items():
        if name not in actual:
            problems.append(f"  - missing column {name!r} ({sqlite_type})")
        elif actual[name] != sqlite_type:
            problems.append(
                f"  - column {name!r}: expected {sqlite_type}, found {actual[name]} in the database"
            )
    for name in actual:
        if name not in expected:
            problems.append(f"  - extra column {name!r} (not in the model)")

    if problems:
        details = "\n".join(problems)
        raise SchemaMismatchError(
            f"Schema of table {table.name!r} does not match the model:\n{details}\n"
            f"The ministore core does not alter existing tables. "
            f"Install 'ministore-migrate' for versioned migrations."
        )
