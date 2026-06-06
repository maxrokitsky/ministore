# ministore

A tiny local **SQLite**-backed store for your models — `dataclass`, `pydantic`
or `msgspec`. Synchronous and asynchronous, fully typed, with minimal code.

```python
from dataclasses import dataclass
from ministore import Store

@dataclass
class User:
    id: int
    name: str
    age: int

db = Store("app.db")
users = db.collection(User, key="id")

users.put(User(1, "Anna", 30))
user = users.get(1)                                   # -> User | None
adults = users.where(age__gte=18).order_by("-age").all()
```

## Principles

- **No required dependencies.** The synchronous core runs on the stdlib
  `sqlite3`. `pip install ministore` and you're done.
- **Familiar models.** No ministore base classes: use your own `dataclass` /
  `pydantic.BaseModel` / `msgspec.Struct`. The adapter is detected
  automatically.
- **A column per field.** Every model field becomes a real SQLite column, so you
  can filter, sort and index by any field. Composite types (`list`, `dict`,
  nested models) are stored as JSON.
- **One API for sync and async.** `Store` and `AsyncStore` mirror each other.

## Installation

```bash
pip install ministore                 # core (sync only, stdlib)
pip install "ministore[async]"        # + async API (aiosqlite)
pip install "ministore[pydantic]"     # + pydantic model support
pip install "ministore[msgspec]"      # + msgspec model support
pip install "ministore[all]"          # everything
```

`dataclass` models work with zero dependencies.

## Queries

`where(**filters)` accepts filters in the `field__op=value` form (the default
operator is equality). It returns a lazy `Query` / `AsyncQuery`.

| Operator | SQL | Example |
|---|---|---|
| `exact` (default) | `=` | `where(name="Anna")` |
| `ne` | `!=` | `where(status__ne="off")` |
| `gt` `gte` `lt` `lte` | `> >= < <=` | `where(age__gte=18)` |
| `in` / `nin` | `IN` / `NOT IN` | `where(id__in=[1, 2, 3])` |
| `isnull` | `IS [NOT] NULL` | `where(deleted__isnull=True)` |
| `like` | `LIKE` (your own pattern) | `where(name__like="A%")` |
| `contains` / `icontains` | `LIKE %v%` | `where(name__contains="nn")` |
| `startswith` / `endswith` | `LIKE v%` / `%v` | `where(name__startswith="A")` |

```python
q = users.where(age__gte=18, name__startswith="A")
q.order_by("-age").limit(10).offset(0)

q.all()        # list[User]
q.first()      # User | None
q.count()      # int
q.exists()     # bool
q.delete()     # int — delete all matches, return the count

for u in users.where(age__gte=18):   # iterates lazily
    ...
```

The collection itself is convenient too:

```python
users.put_many([...])       # bulk insert (upsert by key)
users.all()                 # list[User]
users.count()  / len(users)
3 in users                  # is there a record with this key
users.delete(3)             # delete by key -> bool
users.clear()               # empty the collection -> int
users.drop()                # drop the table
```

## Transactions

By default every write commits on its own. To group several writes — across any
collections of the same store — into one atomic unit, use the `transaction()`
context manager: it commits once on exit and rolls back on any exception.

```python
with db.transaction():
    users.put(user)
    orders.put_many(items)
    accounts.where(id=7).delete()
# committed here; if the block raises, nothing above is written
```

The same primitives are also available explicitly, for full manual control —
`transaction()` is just a thin wrapper over them:

```python
db.begin()
try:
    users.put(user)
    db.commit()
except Exception:
    db.rollback()
    raise

db.in_transaction      # bool — are we inside an open transaction?
```

Transactions **nest** via SAVEPOINTs: an inner block can roll back on its own
while the outer one keeps going (and an outer rollback still discards everything,
inner commits included).

The synchronous `Store` tracks transaction state per thread (each thread has its
own connection). The asynchronous `AsyncStore` shares a single connection, so a
transaction takes exclusive use of it for its duration — other tasks' operations
wait until it finishes rather than interleaving into it. Everything mirrors the
sync API as coroutines:

```python
async with db.transaction():
    await users.put(user)
    await orders.put_many(items)

await db.begin(); ...; await db.commit()     # or the explicit primitives
```

## Projections

To fetch **fewer fields** in a typed way, declare a smaller "view" model and pass
it to `as_model(...)`. The query then selects only those columns and returns
instances of the view model — `list[View]`, so the type checker knows the exact
shape:

```python
@dataclass
class UserBrief:        # a subset of User's fields
    id: int
    name: str

briefs = users.where(age__gte=18).as_model(UserBrief).all()   # -> list[UserBrief]
brief  = users.as_model(UserBrief).first()                     # -> UserBrief | None
```

It is **read-only** and only changes the returned shape: `where(...)`,
`order_by(...)`, `count()` and `delete()` still run against the full table, so
you can filter or sort by a field the view omits:

```python
# filter/sort by `age` even though the view drops it
users.where(age__gte=18).order_by("-age").as_model(UserBrief).all()
```

The view model can be any supported kind (`dataclass` / `pydantic` / `msgspec`)
— it need not match the collection's kind. Its field names must be a subset of
the collection's; an unknown field raises `QueryError`.

## Indexes

Declare the key and indexes either as keyword arguments to `collection(...)`:

```python
users = db.collection(
    User,
    key="id",
    indexes=["age"],      # plain index
    unique=["email"],     # unique index
    name="users",         # table name (defaults to the class name)
)
```

…or inline, on the fields themselves, with the `Key` / `Index` / `Unique`
markers. They are generic aliases over `typing.Annotated`, so `Unique[str]` is
just a `str` to a type checker while ministore reads the marker to build the
table:

```python
from typing import Annotated
from ministore import Store, Key, Index, Unique

@dataclass
class User:
    id:    Key[int]        # primary key — no key= needed
    email: Unique[str]     # unique index
    age:   Index[int]      # plain index
    name:  str

users = db.collection(User)          # schema comes from the annotations

# Extra Annotated metadata composes by nesting (Annotated flattens it):
#   code: Annotated[Unique[str], Field(max_length=16)]
```

Both styles can be combined — the keyword arguments and the markers are merged
(`key=` must agree with a `Key` marker if both are present).

## Async API

Fully mirrors the sync one; methods are coroutines (`pip install
"ministore[async]"`):

```python
from ministore import AsyncStore

async with AsyncStore("app.db") as db:
    users = await db.collection(User, key="id")
    await users.put(User(1, "Anna", 30))
    user = await users.get(1)

    async for u in users.where(age__gte=18):
        ...
    adults = await users.where(age__gte=18).order_by("-age").all()
```

## Thread safety

A `Store` can be freely shared across threads: **WAL** mode is enabled, a
`busy_timeout` is set, and each thread lazily gets its own connection to the
file. An `AsyncStore` keeps a single connection that `aiosqlite` serves on a
dedicated thread and serializes, so it is safe to use from many coroutines.

## Schema and migrations

The core creates the table from the model on first access (`CREATE TABLE IF NOT
EXISTS`) and never **touches** an existing schema afterwards. If the model has
diverged from the table in the database, `collection(...)` raises a
`SchemaMismatchError` describing the difference:

```
SchemaMismatchError: Schema of table 'users' does not match the model:
  - missing column 'age' (INTEGER)
The ministore core does not alter existing tables.
Install 'ministore-migrate' for versioned migrations.
```

Versioned migrations are the job of a separate, optional package
**`ministore-migrate`**, keeping the core minimal. For now, a handy way to
evolve the schema without migrations is to put optional data into composite
fields (they go into a JSON column and don't change the schema).

## Type mapping

| Python | SQLite |
|---|---|
| `int`, `bool` | `INTEGER` |
| `float` | `REAL` |
| `str` | `TEXT` |
| `bytes` | `BLOB` |
| `datetime` / `date` / `time` | `TEXT` (ISO 8601) |
| `Decimal`, `UUID` | `TEXT` |
| `Enum` | by value type (`INTEGER`/`REAL`/`TEXT`) |
| `list` / `dict` / `set` / nested models / `X \| Y` | `TEXT` (JSON) |
| `Optional[T]` / `T \| None` | same column as `T`, but `NULL`-able |

## Development

```bash
uv sync            # environment + dev dependencies
uv run pytest      # tests
uv run pyright     # strict type checking
```

## Author

Max Rokitsky — <max@rokitsky.ru>
