"""Generic-alias markers for declaring the key and indexes inline.

These let a model declare its primary key and indexes right next to the
fields, as an alternative to the ``collection(key=..., indexes=..., unique=...)``
keyword arguments::

    from ministore import Key, Index, Unique

    @dataclass
    class User:
        id:    Key[int]
        email: Unique[str]
        age:   Index[int]

Each marker is a generic alias built on ``typing.Annotated`` — ``Unique[str]``
is exactly ``Annotated[str, <marker>]``. To a type checker the field's type is
just the inner type (``str``); the marker rides along as metadata that ministore
reads when building the table. The keyword-argument form keeps working and may be
freely combined with the markers (the two are merged).

Extra metadata composes by nesting, which ``Annotated`` flattens::

    code: Annotated[Unique[str], Field(max_length=16)]   # -> str, unique, max_length
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, TypeVar

_T = TypeVar("_T")


class _Marker:
    """Base class for the metadata objects embedded in the marker aliases."""


@dataclass(frozen=True)
class _KeyMarker(_Marker):
    """Marks the field as the table's primary key."""


@dataclass(frozen=True)
class _IndexMarker(_Marker):
    """Marks the field for a (non-unique) index."""


@dataclass(frozen=True)
class _UniqueMarker(_Marker):
    """Marks the field for a unique index."""


_KEY = _KeyMarker()
_INDEX = _IndexMarker()
_UNIQUE = _UniqueMarker()

# Public generic aliases. ``Unique[str]`` == ``Annotated[str, _UNIQUE]``: a type
# checker sees the inner type, while the marker survives as Annotated metadata.
Key = Annotated[_T, _KEY]
Index = Annotated[_T, _INDEX]
Unique = Annotated[_T, _UNIQUE]


def marker_kind(obj: object) -> str | None:
    """Classifies an ``Annotated`` metadata item as a ministore marker.

    Accepts both a marker instance (the usual case, embedded by the aliases) and
    the marker class, returning ``"key"`` / ``"index"`` / ``"unique"``. Returns
    ``None`` for any other metadata (e.g. pydantic ``Field`` or msgspec ``Meta``
    constraints), which the schema builder simply ignores.
    """
    cls = obj if isinstance(obj, type) else type(obj)
    if not issubclass(cls, _Marker):
        return None
    if issubclass(cls, _KeyMarker):
        return "key"
    if issubclass(cls, _UniqueMarker):
        return "unique"
    if issubclass(cls, _IndexMarker):
        return "index"
    return None
