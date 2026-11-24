"""n8n-style execution item model.

n8n passes nodes a list of *items*, each shaped like::

    {"json": {...}, "binary": {...}, "pairedItem": {"item": 0}}

``pairedItem`` links an output item back to the input item it came from, which
is what lets an expression resolve ``$("Fetch").item`` from a downstream node
without guessing. The local engine historically passed plain JSON values
between nodes; these helpers give both shapes one canonical reading so
expressions, previews and the builder can speak the n8n model without
rewriting every stored workflow.

This module is intentionally dependency-free and pure.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional

# Keys that make a mapping an item envelope rather than user data.
_ITEM_META_KEYS = {"json", "binary", "pairedItem", "error"}


def is_item(value: Any) -> bool:
    """True when ``value`` is an n8n item envelope (``{json: ...}``)."""
    return (
        isinstance(value, dict)
        and "json" in value
        and set(value.keys()) <= _ITEM_META_KEYS
        and isinstance(value.get("json"), (dict, list, str, int, float, bool, type(None)))
    )


def make_item(json: Any, index: Optional[int] = None, binary: Optional[dict] = None) -> Dict[str, Any]:
    """Build a canonical item envelope."""
    item: Dict[str, Any] = {"json": json}
    if binary is not None:
        item["binary"] = binary
    if index is not None:
        item["pairedItem"] = {"item": index}
    return item


def as_items(value: Any) -> List[Dict[str, Any]]:
    """Normalise any node output into a list of item envelopes.

    * ``None`` -> ``[]``
    * a single dict -> one item
    * a list -> one item per entry (entries that are already envelopes pass
      through untouched; everything else is wrapped)
    * a dict carrying ``items`` / ``data`` / ``results`` / ``body`` lists is
      treated as that list, matching how the local engine emits node output
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [item if is_item(item) else make_item(item, index) for index, item in enumerate(value)]
    if isinstance(value, dict):
        if is_item(value):
            return [value]
        for key in ("items", "data", "results", "body"):
            candidate = value.get(key)
            if isinstance(candidate, list):
                return as_items(candidate)
        return [make_item(value)]
    return [make_item(value)]


def item_binary(item: Any) -> Dict[str, Any]:
    """The binary properties of an item (``{}`` when it has none)."""
    if isinstance(item, dict):
        binary = item.get("binary")
        if isinstance(binary, dict):
            return binary
    return {}


def first_binary(items: Iterable[Any]) -> Dict[str, Any]:
    """The first binary property group among ``items`` (n8n's ``$binary``)."""
    for item in items:
        binary = item_binary(item)
        if binary:
            return binary
    return {}


def item_json(item: Any) -> Any:
    """Read the JSON payload of an item (or return a plain value unchanged)."""
    if is_item(item):
        return item.get("json")
    return item


def json_list(items: Iterable[Any]) -> List[Any]:
    """Strip the envelopes off a list of items."""
    return [item_json(item) for item in items]


def first_json(value: Any, default: Any = None) -> Any:
    items = as_items(value)
    return item_json(items[0]) if items else default


def last_json(value: Any, default: Any = None) -> Any:
    items = as_items(value)
    return item_json(items[-1]) if items else default


def map_items(
    value: Any,
    fn: Callable[[Any, int], Any],
) -> List[Any]:
    """Apply ``fn(json, index)`` to every item, keeping envelopes intact."""
    out: List[Any] = []
    for index, item in enumerate(as_items(value)):
        result = fn(item_json(item), index)
        if is_item(item):
            out.append({**item, "json": result} if isinstance(result, (dict, list)) else result)
        else:
            out.append(result)
    return out


def paired_item_index(item: Any) -> Optional[int]:
    """The source index of an item, when it carries a ``pairedItem`` link."""
    if not isinstance(item, dict):
        return None
    paired = item.get("pairedItem")
    if isinstance(paired, dict):
        index = paired.get("item")
        return index if isinstance(index, int) else None
    if isinstance(paired, int):
        return paired
    return None
