"""SQLite type compatibility for the zero-dependency local database mode.

Models use PostgreSQL-specific column types (JSONB, ARRAY). Postgres/asyncpg
uses them natively; on SQLite they are rendered as JSON, which SQLite supports.
The overrides are dialect-scoped, so PostgreSQL behaviour is unchanged.
"""

from datetime import UTC

from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.sqlite import base as sqlite_base
from sqlalchemy.ext.compiler import compiles


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(type_, compiler, **kw):  # noqa: ARG001
    return "JSON"


@compiles(ARRAY, "sqlite")
def _compile_array_sqlite(type_, compiler, **kw):  # noqa: ARG001
    return "JSON"


# SQLite has no native timezone support: DateTime columns come back naive,
# which breaks comparisons against datetime.now(timezone.utc). Re-attach UTC so
# the application logic (which assumes timezone-aware UTC everywhere) is
# unchanged. PostgreSQL is unaffected.
_original_result_processor = sqlite_base.DATETIME.result_processor


def _utc_result_processor(self, dialect, coltype):
    base = _original_result_processor(self, dialect, coltype)
    if base is None:
        return None

    def process(value):
        result = base(value)
        if result is not None and getattr(result, "tzinfo", None) is None:
            return result.replace(tzinfo=UTC)
        return result

    return process


sqlite_base.DATETIME.result_processor = _utc_result_processor


def dedupe_index_names(metadata) -> int:
    """Remove duplicate index declarations from metadata (idempotent).

    Several models declare the same index twice: once through
    ``mapped_column(index=True)`` and once explicitly in ``__table_args__``.
    PostgreSQL never notices (hand-written migrations), but ``create_all``
    emits both ``CREATE INDEX`` statements and fails with "index ... already
    exists". Both share the same name, so keeping the first is safe.
    """
    removed = 0
    for table in metadata.tables.values():
        kept: dict[str, object] = {}
        for index in list(table.indexes):
            existing = kept.get(index.name)
            if existing is not None:
                # Never let deduplication drop a UNIQUE constraint: a
                # ``unique=True`` column and an explicit non-unique index can
                # share a name, and keeping the non-unique one would silently
                # remove the constraint on SQLite create_all.
                if index.unique and not existing.unique:
                    existing.unique = True
                table.indexes.discard(index)
                removed += 1
            else:
                kept[index.name] = index
    return removed

# PostgreSQL ARRAY columns must (de)serialize to/from JSON on SQLite. The DDL
# override above makes the column JSON, but binding still needs to encode a
# Python list and decoding needs to return one. Dialect-scoped by name.
import json  # noqa: E402

from sqlalchemy.dialects.postgresql import ARRAY as _PG_ARRAY  # noqa: E402

_original_array_bind = _PG_ARRAY.bind_processor
_original_array_result = _PG_ARRAY.result_processor


def _array_bind_processor(self, dialect):
    if dialect.name == "sqlite":
        def process(value):
            if value is None:
                return None
            return json.dumps(value)
        return process
    return _original_array_bind(self, dialect)


def _array_result_processor(self, dialect, coltype):
    if dialect.name == "sqlite":
        def process(value):
            if value is None:
                return None
            if isinstance(value, (list, dict)):
                return value
            return json.loads(value)
        return process
    return _original_array_result(self, dialect, coltype)


_PG_ARRAY.bind_processor = _array_bind_processor
_PG_ARRAY.result_processor = _array_result_processor


# PostgreSQL UUID columns expect uuid.UUID objects (bind calls ``.hex``).
# SQLite accepts hex text, so tolerate string IDs from JSON/webhook payloads
# while normalizing them to the same on-disk representation.
import uuid as _uuid  # noqa: E402

from sqlalchemy.dialects.postgresql import UUID as _PG_UUID  # noqa: E402

_original_uuid_bind = _PG_UUID.bind_processor


def _uuid_bind_processor(self, dialect):
    if dialect.name == "sqlite":
        def process(value):
            if value is None:
                return None
            if isinstance(value, _uuid.UUID):
                return value.hex
            try:
                return _uuid.UUID(str(value)).hex
            except ValueError:
                return str(value)
        return process
    return _original_uuid_bind(self, dialect)


_PG_UUID.bind_processor = _uuid_bind_processor
