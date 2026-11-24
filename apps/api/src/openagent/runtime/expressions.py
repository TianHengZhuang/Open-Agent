"""n8n-compatible ``{{ ... }}`` expression evaluator.

Ported from n8n's closed expression grammar
(``packages/workflow/src/expressions/native-evaluation`` — grammar, parser and
evaluator) so a workflow authored against n8n's expression language evaluates
the same way here.

What is supported:

* literals (strings, numbers, booleans, ``null``, ``undefined``, arrays)
* member access, index access, optional chaining (``?.``), grouping
* unary ``!`` ``-`` ``+``; binary ``+ - * / %`` and ``=== !== == != < <= > >=``
  with JavaScript coercion; ``&&`` ``||``; ternary ``? :``
* data roots: ``$json $binary $parameter $vars $itemIndex $runIndex $now
  $today $workflow $execution $env $node $items $input $('Node')``
* the legacy local roots used by this engine's stored workflows
  (``json nodes variables trigger run workflow env``)
* allowlisted string / number / array / object methods, array iterators with
  single-parameter arrow callbacks (``map``, ``filter``, ``find``, ``some``,
  ``every``) and the date helpers ``plus`` / ``minus`` / ``format``

The evaluation is total over the parsed grammar: no Python ``eval`` is used and
no attribute outside the allowlists is ever touched.
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from openagent.runtime.items import as_items, first_json

# ---------------------------------------------------------------------------
# Limits (mirroring n8n's native evaluator)
# ---------------------------------------------------------------------------

MAX_DEPTH = 64
MAX_STEPS = 100_000
MAX_RESULT_LENGTH = 1_000_000

_EXPR_RE = re.compile(r"\{\{\s*(.+?)\s*\}\}", re.S)


class ExpressionError(Exception):
    """An expression could not be parsed or evaluated."""


class UnknownReferenceError(ExpressionError):
    """A root name is not known to this evaluator (callers may fall back)."""


# ---------------------------------------------------------------------------
# JavaScript value semantics
# ---------------------------------------------------------------------------


class _Undefined:
    """``undefined`` — distinct from ``None`` (``null``), as in JavaScript."""

    _instance: Optional["_Undefined"] = None

    def __new__(cls) -> "_Undefined":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "undefined"


UNDEFINED = _Undefined()


def _is_nullish(value: Any) -> bool:
    return value is None or value is UNDEFINED


def js_number_str(value: float) -> str:
    """Render a float the way JavaScript's ``Number#toString`` does."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    if value == 0:
        return "0"
    if float(value).is_integer() and abs(value) < 1e21:
        return str(int(value))
    text = repr(float(value))
    if text.endswith(".0"):
        text = text[:-2]
    return text


def js_to_string(value: Any) -> str:
    """JavaScript ``String(value)`` for concatenation."""
    if value is UNDEFINED or value is None:
        return "null" if value is None else "undefined"
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return js_number_str(float(value))
    if isinstance(value, list):
        return ",".join(js_to_string(entry) for entry in value)
    if isinstance(value, dict):
        return "[object Object]"
    return str(value)


def output_string(value: Any) -> str:
    """Render a value inside a mixed template (JSON for structures)."""
    if _is_nullish(value):
        return ""
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, default=str)
        except (TypeError, ValueError):
            return str(value)
    return js_to_string(value)


def js_truthy(value: Any) -> bool:
    if _is_nullish(value):
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0 and not (isinstance(value, float) and math.isnan(value))
    if isinstance(value, str):
        return value != ""
    if isinstance(value, (list, dict)):
        return True
    return True


def _js_to_number(value: Any) -> float:
    if value is UNDEFINED:
        return float("nan")
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0.0
        try:
            return float(text)
        except ValueError:
            return float("nan")
    return float("nan")


def _is_numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def js_strict_equals(left: Any, right: Any) -> bool:
    if _is_nullish(left) or _is_nullish(right):
        return _is_nullish(left) and _is_nullish(right)
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    if _is_numeric(left) and _is_numeric(right):
        return float(left) == float(right)
    if type(left) is not type(right):
        return False
    return left == right


def js_loose_equals(left: Any, right: Any) -> bool:
    if _is_nullish(left) and _is_nullish(right):
        return True
    if _is_nullish(left) or _is_nullish(right):
        return False
    if isinstance(left, bool) or isinstance(right, bool):
        return _js_to_number(left) == _js_to_number(right)
    if _is_numeric(left) and isinstance(right, str):
        return float(left) == _js_to_number(right)
    if isinstance(left, str) and _is_numeric(right):
        return _js_to_number(left) == float(right)
    if type(left) is type(right):
        return left == right
    return False


def _numeric_operand(value: Any, op: str) -> float:
    if isinstance(value, bool) or isinstance(value, (str, list, dict)) or _is_nullish(value):
        raise ExpressionError(f"Operator '{op}' needs numbers")
    if not isinstance(value, (int, float)):
        raise ExpressionError(f"Operator '{op}' needs numbers")
    return float(value)


def _compact_number(value: float) -> Any:
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e21:
        return int(value)
    return value


def js_slice(value: Sequence[Any], start: Any = None, end: Any = None) -> Any:
    length = len(value)
    begin = 0 if start is None else int(_js_to_number(start))
    finish = length if end is None else int(_js_to_number(end))
    if begin < 0:
        begin = max(length + begin, 0)
    if finish < 0:
        finish = max(length + finish, 0)
    return value[begin:finish]


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<number>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
  | (?P<string>'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*")
  | (?P<ident>[A-Za-z_$][A-Za-z0-9_$]*)
  | (?P<op>\?\.|===|!==|==|!=|<=|>=|&&|\|\||=>|[+\-*/%!<>=?:.,()\[\]])
    """,
    re.X,
)

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}


def _unescape(raw: str) -> str:
    out: List[str] = []
    i = 0
    while i < len(raw):
        char = raw[i]
        if char != "\\" or i + 1 >= len(raw):
            out.append(char)
            i += 1
            continue
        nxt = raw[i + 1]
        if nxt == "u" and i + 5 < len(raw):
            try:
                out.append(chr(int(raw[i + 2 : i + 6], 16)))
                i += 6
                continue
            except ValueError:
                out.append("u")
                i += 2
                continue
        out.append(_ESCAPES.get(nxt, nxt))
        i += 2
    return "".join(out)


def _tokenize(source: str) -> List[Tuple[str, Any]]:
    tokens: List[Tuple[str, Any]] = []
    position = 0
    while position < len(source):
        match = _TOKEN_RE.match(source, position)
        if not match:
            raise ExpressionError(f"Unexpected character {source[position]!r} in expression")
        position = match.end()
        kind = match.lastgroup
        text = match.group()
        if kind == "ws":
            continue
        if kind == "number":
            value: Any = float(text) if ("." in text or "e" in text.lower()) else int(text)
            tokens.append(("number", value))
        elif kind == "string":
            tokens.append(("string", _unescape(text[1:-1])))
        elif kind == "ident":
            tokens.append(("ident", text))
        else:
            tokens.append(("op", text))
    tokens.append(("eof", None))
    return tokens


# ---------------------------------------------------------------------------
# Parser (the n8n closed grammar)
# ---------------------------------------------------------------------------

_COMPARISONS = {"===", "!==", "==", "!=", "<", "<=", ">", ">="}
_LEGACY_ROOTS = {"json", "nodes", "variables", "trigger", "run", "workflow", "env", "item", "index"}
_FUNCTION_ROOTS = {"$", "$items", "$input", "$node", "$getWorkflowStaticData"}


class _Parser:
    def __init__(self, tokens: List[Tuple[str, Any]]) -> None:
        self.tokens = tokens
        self.pos = 0
        self.depth = 0
        self.locals: List[str] = []

    # -- token helpers ----------------------------------------------------
    def peek(self, offset: int = 0) -> Tuple[str, Any]:
        index = min(self.pos + offset, len(self.tokens) - 1)
        return self.tokens[index]

    def take(self) -> Tuple[str, Any]:
        token = self.tokens[self.pos]
        self.pos += 1
        return token

    def accept_op(self, *ops: str) -> Optional[str]:
        kind, value = self.peek()
        if kind == "op" and value in ops:
            self.pos += 1
            return value
        return None

    def expect_op(self, op: str) -> None:
        if self.accept_op(op) is None:
            raise ExpressionError(f"Expected '{op}' in expression")

    def _enter(self) -> None:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise ExpressionError("Expression is nested too deeply")

    def _leave(self) -> None:
        self.depth -= 1

    # -- grammar ----------------------------------------------------------
    def parse(self) -> Any:
        node = self.expression()
        kind, _ = self.peek()
        if kind != "eof":
            raise ExpressionError("Unexpected trailing input in expression")
        return node

    def expression(self) -> Any:
        node = self.logical_or()
        if self.accept_op("?"):
            consequent = self.expression()
            self.expect_op(":")
            alternate = self.expression()
            return ("cond", node, consequent, alternate)
        return node

    def logical_or(self) -> Any:
        node = self.logical_and()
        while self.accept_op("||"):
            node = ("logical", "||", node, self.logical_and())
        return node

    def logical_and(self) -> Any:
        node = self.equality()
        while self.accept_op("&&"):
            node = ("logical", "&&", node, self.equality())
        return node

    def equality(self) -> Any:
        node = self.relational()
        while True:
            op = self.accept_op("===", "!==", "==", "!=")
            if op is None:
                return node
            node = ("binary", op, node, self.relational())

    def relational(self) -> Any:
        node = self.additive()
        while True:
            op = self.accept_op("<", "<=", ">", ">=")
            if op is None:
                return node
            node = ("binary", op, node, self.additive())

    def additive(self) -> Any:
        node = self.multiplicative()
        while True:
            op = self.accept_op("+", "-")
            if op is None:
                return node
            node = ("binary", op, node, self.multiplicative())

    def multiplicative(self) -> Any:
        node = self.unary()
        while True:
            op = self.accept_op("*", "/", "%")
            if op is None:
                return node
            node = ("binary", op, node, self.unary())

    def unary(self) -> Any:
        op = self.accept_op("!", "-", "+")
        if op is not None:
            self._enter()
            try:
                return ("unary", op, self.unary())
            finally:
                self._leave()
        return self.postfix()

    def postfix(self) -> Any:
        node = self.primary()
        while True:
            optional = False
            if self.peek() == ("op", "?."):
                self.pos += 1
                optional = True
            if optional or self.peek() == ("op", "."):
                if not optional:
                    self.pos += 1
                kind, value = self.take()
                if kind not in ("ident", "number"):
                    raise ExpressionError("Expected a property name after '.'")
                key: Any = value if kind == "ident" else int(value)
                if self.peek() == ("op", "("):
                    if not isinstance(key, str):
                        raise ExpressionError("A method name must be an identifier")
                    args = self.arguments()
                    node = ("call", node, key, args, optional)
                else:
                    node = ("member", node, key, optional)
                continue
            if self.peek() == ("op", "["):
                self.pos += 1
                index = self.expression()
                self.expect_op("]")
                node = ("index", node, index)
                continue
            return node

    def arguments(self) -> List[Any]:
        self.expect_op("(")
        args: List[Any] = []
        if self.accept_op(")"):
            return args
        while True:
            args.append(self.expression())
            if self.accept_op(")"):
                return args
            self.expect_op(",")

    def _arrow_ahead(self) -> bool:
        """True when the tokens at the cursor start a ``param => body`` pair."""
        kind, value = self.peek()
        if kind == "ident" and self.peek(1) == ("op", "=>"):
            return True
        if kind == "op" and value == "(":
            offset = 1
            depth = 1
            while True:
                kind_at, value_at = self.peek(offset)
                if kind_at == "eof":
                    return False
                if kind_at == "op" and value_at == "(":
                    depth += 1
                elif kind_at == "op" and value_at == ")":
                    depth -= 1
                    if depth == 0:
                        return self.peek(offset + 1) == ("op", "=>")
                offset += 1
        return False

    def arrow(self) -> Any:
        self._enter()
        try:
            if self.accept_op("("):
                kind, value = self.take()
                if kind != "ident":
                    raise ExpressionError("An arrow callback takes one parameter")
                self.expect_op(")")
                param = value
            else:
                kind, value = self.take()
                if kind != "ident":
                    raise ExpressionError("An arrow callback takes one parameter")
                param = value
            self.expect_op("=>")
            self.locals.append(param)
            try:
                body = self.expression()
            finally:
                self.locals.pop()
            return ("lambda", param, body)
        finally:
            self._leave()

    def primary(self) -> Any:
        kind, value = self.peek()
        if self._arrow_ahead():
            return self.arrow()
        if kind == "number":
            self.pos += 1
            return ("literal", value)
        if kind == "string":
            self.pos += 1
            return ("literal", value)
        if kind == "ident":
            self.pos += 1
            if value == "true":
                return ("literal", True)
            if value == "false":
                return ("literal", False)
            if value == "null":
                return ("literal", None)
            if value == "undefined":
                return ("literal", UNDEFINED)
            if value.startswith("$") or value in _LEGACY_ROOTS:
                if value in _FUNCTION_ROOTS and self.peek() == ("op", "("):
                    return ("func", value, self.arguments())
                return ("root", value)
            if value in self.locals:
                return ("local", value)
            raise UnknownReferenceError(f"Unknown reference: {value}")
        if kind == "op" and value == "(":
            self.pos += 1
            self._enter()
            try:
                node = self.expression()
            finally:
                self._leave()
            self.expect_op(")")
            return node
        if kind == "op" and value == "[":
            self.pos += 1
            elements: List[Any] = []
            if self.accept_op("]"):
                return ("array", elements)
            while True:
                elements.append(self.expression())
                if self.accept_op("]"):
                    return ("array", elements)
                self.expect_op(",")
        if kind == "eof":
            raise ExpressionError("Expression ended unexpectedly")
        raise ExpressionError(f"Unexpected token {value!r} in expression")


_AST_CACHE: Dict[str, Any] = {}
_AST_CACHE_LIMIT = 512


def compile_expression(source: str) -> Any:
    """Parse ``source`` into an AST, caching the result."""
    cached = _AST_CACHE.get(source)
    if cached is not None:
        return cached
    ast = _Parser(_tokenize(source)).parse()
    if len(_AST_CACHE) >= _AST_CACHE_LIMIT:
        _AST_CACHE.clear()
    _AST_CACHE[source] = ast
    return ast


# ---------------------------------------------------------------------------
# Data proxy (n8n's WorkflowDataProxy surface)
# ---------------------------------------------------------------------------


@dataclass
class ExpressionData:
    """Everything an expression may read, resolved by the caller."""

    json: Any = None
    # The legacy `json` root used by stored workflows (the trigger payload).
    # `$json` is the node's current input item, as in n8n.
    legacy_json: Any = None
    items: List[Dict[str, Any]] = field(default_factory=list)
    node_outputs: Dict[str, Any] = field(default_factory=dict)
    variables: Dict[str, Any] = field(default_factory=dict)
    parameter: Dict[str, Any] = field(default_factory=dict)
    binary: Dict[str, Any] = field(default_factory=dict)
    workflow: Dict[str, Any] = field(default_factory=dict)
    execution: Dict[str, Any] = field(default_factory=dict)
    env: Dict[str, str] = field(default_factory=lambda: dict(os.environ))
    trigger: Any = None
    # Workflow static data, keyed by scope (``global``, ``node:<Name>``). The
    # evaluator hands out the live dict so a write is visible to the run.
    static_data: Dict[str, Any] = field(default_factory=dict)
    item_index: int = 0
    run_index: int = 0
    timezone: Optional[str] = None


class _NodeProxy:
    """``$('Node Name')`` — lazy access to one node's output items."""

    __slots__ = ("name", "items")

    def __init__(self, name: str, items: List[Dict[str, Any]]) -> None:
        self.name = name
        self.items = items

    def member(self, key: str) -> Any:
        if key == "item":
            if not self.items:
                return None
            return self.items[0]
        if key == "json":
            return first_json(self.items)
        if key == "binary":
            return self.items[0].get("binary", {}) if self.items else {}
        raise ExpressionError(f"Unknown node reference member '{key}'")


class _LegacyNodes:
    """``$node['Name']`` — the legacy item-level view (``.json``)."""

    __slots__ = ("_outputs",)

    def __init__(self, outputs: Dict[str, Any]) -> None:
        self._outputs = outputs

    def lookup(self, name: str) -> Any:
        if name not in self._outputs:
            raise ExpressionError(f"Node '{name}' has not run")
        # The legacy view is item-level: the node's whole output is `.json`,
        # even when it is a dict that happens to carry an `items` key.
        return _LegacyNode(name, [{"json": self._outputs[name]}])


class _LegacyNode:
    __slots__ = ("name", "items")

    def __init__(self, name: str, items: List[Dict[str, Any]]) -> None:
        self.name = name
        self.items = items

    def member(self, key: str) -> Any:
        if key == "json":
            return first_json(self.items)
        if key == "binary":
            return self.items[0].get("binary", {}) if self.items else {}
        raise ExpressionError(f"Unknown node reference member '{key}'")


class _InputProxy:
    """``$input`` — the items handed to the current node."""

    __slots__ = ("items",)

    def __init__(self, items: List[Dict[str, Any]]) -> None:
        self.items = items

    def member(self, key: str) -> Any:
        if key == "item":
            return self.items[0] if self.items else None
        if key == "json":
            return first_json(self.items)
        if key == "binary":
            return self.items[0].get("binary", {}) if self.items else {}
        raise ExpressionError(f"Unknown $input member '{key}'")


_MONTH_DAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
_UNIT_ALIASES = {
    "year": "years",
    "years": "years",
    "quarter": "quarters",
    "quarters": "quarters",
    "month": "months",
    "months": "months",
    "week": "weeks",
    "weeks": "weeks",
    "day": "days",
    "days": "days",
    "hour": "hours",
    "hours": "hours",
    "minute": "minutes",
    "minutes": "minutes",
    "second": "seconds",
    "seconds": "seconds",
    "millisecond": "milliseconds",
    "milliseconds": "milliseconds",
}


class N8nDateTime:
    """A small Luxon-``DateTime``-shaped wrapper used by ``$now``/``$today``."""

    __slots__ = ("value",)

    def __init__(self, value: datetime) -> None:
        self.value = value

    @classmethod
    def now(cls, timezone_name: Optional[str] = None, midnight: bool = False) -> "N8nDateTime":
        value = datetime.now(timezone.utc)
        if timezone_name:
            try:
                from zoneinfo import ZoneInfo

                value = value.astimezone(ZoneInfo(timezone_name))
            except Exception:  # unknown zone: stay in UTC rather than fail
                pass
        if midnight:
            value = value.replace(hour=0, minute=0, second=0, microsecond=0)
        return cls(value)

    # -- getters ---------------------------------------------------------
    def member(self, key: str) -> Any:
        value = self.value
        if key == "year":
            return value.year
        if key == "month":
            return value.month
        if key == "day":
            return value.day
        if key == "hour":
            return value.hour
        if key == "minute":
            return value.minute
        if key == "second":
            return value.second
        if key == "millisecond":
            return value.microsecond // 1000
        if key == "weekday":
            return value.isoweekday()
        if key == "weekNumber":
            return value.isocalendar()[1]
        if key == "quarter":
            return (value.month - 1) // 3 + 1
        if key == "daysInMonth":
            leap = value.year % 4 == 0 and (value.year % 100 != 0 or value.year % 400 == 0)
            return 29 if (value.month == 2 and leap) else _MONTH_DAYS[value.month - 1]
        if key == "offset":
            offset = value.utcoffset()
            return int(offset.total_seconds() // 60) if offset else 0
        raise ExpressionError(f"Unknown date property '{key}'")

    # -- methods ---------------------------------------------------------
    def to_millis(self) -> int:
        return int(self.value.timestamp() * 1000)

    def to_iso(self) -> str:
        return self.value.isoformat(timespec="milliseconds")

    def to_iso_date(self) -> str:
        return self.value.date().isoformat()

    def to_iso_time(self) -> str:
        return self.value.timetz().isoformat(timespec="milliseconds")

    def plus(self, amount: Any, unit: Any) -> "N8nDateTime":
        return N8nDateTime(_shift_datetime(self.value, _js_to_number(amount), unit, 1))

    def minus(self, amount: Any, unit: Any) -> "N8nDateTime":
        return N8nDateTime(_shift_datetime(self.value, _js_to_number(amount), unit, -1))

    def format(self, pattern: Any) -> str:
        return _format_datetime(self.value, str(pattern))


def _shift_datetime(value: datetime, amount: float, unit: Any, sign: int) -> datetime:
    name = _UNIT_ALIASES.get(str(unit).lower())
    if name is None:
        raise ExpressionError(f"Unknown date unit '{unit}'")
    delta = amount * sign
    if name == "years":
        return value.replace(year=value.year + int(delta))
    if name == "quarters":
        return value + timedelta(days=int(delta) * 91)
    if name == "months":
        months = value.month - 1 + int(delta)
        year = value.year + months // 12
        month = months % 12 + 1
        day = min(value.day, _MONTH_DAYS[month - 1])
        return value.replace(year=year, month=month, day=day)
    if name == "weeks":
        return value + timedelta(weeks=delta)
    if name == "days":
        return value + timedelta(days=delta)
    if name == "hours":
        return value + timedelta(hours=delta)
    if name == "minutes":
        return value + timedelta(minutes=delta)
    if name == "seconds":
        return value + timedelta(seconds=delta)
    return value + timedelta(milliseconds=delta)


_FORMAT_TOKENS = (
    ("yyyy", lambda v: f"{v.year:04d}"),
    ("yy", lambda v: f"{v.year % 100:02d}"),
    ("MMMM", lambda v: v.strftime("%B")),
    ("MMM", lambda v: v.strftime("%b")),
    ("MM", lambda v: f"{v.month:02d}"),
    ("dd", lambda v: f"{v.day:02d}"),
    ("HH", lambda v: f"{v.hour:02d}"),
    ("hh", lambda v: f"{(v.hour % 12) or 12:02d}"),
    ("mm", lambda v: f"{v.minute:02d}"),
    ("ss", lambda v: f"{v.second:02d}"),
    ("SSS", lambda v: f"{v.microsecond // 1000:03d}"),
    ("M", lambda v: str(v.month)),
    ("d", lambda v: str(v.day)),
    ("H", lambda v: str(v.hour)),
    ("h", lambda v: str((v.hour % 12) or 12)),
    ("m", lambda v: str(v.minute)),
    ("s", lambda v: str(v.second)),
    ("a", lambda v: "AM" if v.hour < 12 else "PM"),
)

_FORMAT_RE = re.compile("|".join(token for token, _ in _FORMAT_TOKENS))


def _format_datetime(value: datetime, pattern: str) -> str:
    def replace(match: re.Match) -> str:
        token = match.group(0)
        for name, render in _FORMAT_TOKENS:
            if name == token:
                return render(value)
        return token

    # Protect the literal text between tokens from the tokenizer.
    parts: List[str] = []
    last = 0
    for match in _FORMAT_RE.finditer(pattern):
        parts.append(pattern[last : match.start()])
        parts.append(replace(match))
        last = match.end()
    parts.append(pattern[last:])
    return "".join(parts)


_OBJECT_EXTENSIONS = {"keys", "values", "toJsonString", "hasField", "isEmpty", "isNotEmpty", "merge"}
_STRING_EXTENSIONS = {"isEmpty", "isNotEmpty", "toNumber", "toBoolean", "toJson"}


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


class _Evaluator:
    def __init__(self, data: ExpressionData) -> None:
        self.data = data
        self.steps = 0
        self.param_name: Optional[str] = None
        self.param_value: Any = UNDEFINED
        self.roots = self._build_roots()

    # -- roots -----------------------------------------------------------
    def _build_roots(self) -> Dict[str, Any]:
        data = self.data
        current = data.json
        nodes = _LegacyNodes(data.node_outputs)
        return {
            "$json": current,
            "$binary": data.binary,
            "$parameter": data.parameter,
            "$vars": data.variables,
            "$itemIndex": data.item_index,
            "$runIndex": data.run_index,
            "$now": N8nDateTime.now(data.timezone),
            "$today": N8nDateTime.now(data.timezone, midnight=True),
            "$node": nodes,
            "$input": _InputProxy(data.items),
            "$workflow": data.workflow,
            "$execution": data.execution,
            "$env": data.env,
            # legacy local roots
            "json": data.legacy_json if data.legacy_json is not None else current,
            "nodes": data.node_outputs,
            "variables": data.variables,
            "trigger": data.trigger if data.trigger is not None else current,
            "run": {"id": data.execution.get("id")},
            "workflow": data.workflow,
            "env": data.env,
            "item": current,
            "index": data.item_index,
        }

    # -- helpers ---------------------------------------------------------
    def _tick(self) -> None:
        self.steps += 1
        if self.steps > MAX_STEPS:
            raise ExpressionError("Expression is too complex to evaluate")

    def _bounded(self, value: Any) -> Any:
        if isinstance(value, str) and len(value) > MAX_RESULT_LENGTH:
            raise ExpressionError("Expression result is too large")
        if isinstance(value, list) and len(value) > MAX_RESULT_LENGTH:
            raise ExpressionError("Expression result is too large")
        return value

    # -- evaluation ------------------------------------------------------
    def eval(self, node: Any) -> Any:
        self._tick()
        kind = node[0]
        if kind == "literal":
            return node[1]
        if kind == "local":
            return self.param_value if node[1] == self.param_name else UNDEFINED
        if kind == "root":
            return self._root(node[1])
        if kind == "array":
            return [self.eval(element) for element in node[1]]
        if kind == "lambda":
            return node
        if kind == "member":
            return self._member(self.eval(node[1]), node[2], node[3])
        if kind == "index":
            return self._index(self.eval(node[1]), self.eval(node[2]))
        if kind == "call":
            return self._call(node)
        if kind == "func":
            return self._function(node[1], node[2])
        if kind == "unary":
            return self._unary(node[1], self.eval(node[2]))
        if kind == "binary":
            return self._binary(node[1], self.eval(node[2]), self.eval(node[3]))
        if kind == "logical":
            left = self.eval(node[2])
            if node[1] == "&&":
                return self.eval(node[3]) if js_truthy(left) else left
            return left if js_truthy(left) else self.eval(node[3])
        if kind == "cond":
            return self.eval(node[2]) if js_truthy(self.eval(node[1])) else self.eval(node[3])
        raise ExpressionError(f"Unsupported expression node '{kind}'")

    def _root(self, name: str) -> Any:
        if name in ("$", "$items"):
            raise ExpressionError(f"'{name}' must be called")
        if name in self.roots:
            return self.roots[name]
        raise UnknownReferenceError(f"Unknown reference: {name}")

    def _member(self, obj: Any, key: Any, optional: bool) -> Any:
        if _is_nullish(obj):
            if optional:
                return UNDEFINED
            raise ExpressionError(f"Cannot read properties of {js_to_string(obj)} (reading '{key}')")
        if isinstance(obj, _NodeProxy):
            return obj.member(str(key))
        if isinstance(obj, _LegacyNodes):
            return obj.lookup(str(key))
        if isinstance(obj, _LegacyNode):
            return obj.member(str(key))
        if isinstance(obj, _InputProxy):
            return obj.member(str(key))
        if isinstance(obj, N8nDateTime):
            return obj.member(str(key))
        if isinstance(obj, (str, list)) and key == "length":
            return len(obj)
        if isinstance(obj, dict):
            return obj.get(key, UNDEFINED)
        if isinstance(obj, list):
            value = _index_list(obj, key)
            return value
        if isinstance(obj, str):
            if key == "length":
                return len(obj)
            raise ExpressionError(f"Unknown string property '{key}'")
        raise ExpressionError(f"Cannot read property '{key}' of {type(obj).__name__}")

    def _index(self, obj: Any, key: Any) -> Any:
        if _is_nullish(obj):
            raise ExpressionError("Cannot read properties of undefined")
        if isinstance(obj, _LegacyNodes):
            return obj.lookup(str(key))
        if isinstance(obj, dict):
            return obj.get(key, UNDEFINED)
        if isinstance(obj, (list, str, tuple)):
            return _index_list(obj, key)
        raise ExpressionError(f"Cannot index {type(obj).__name__}")

    def _function(self, name: str, arg_nodes: List[Any]) -> Any:
        if name == "$":
            args = [self.eval(arg) for arg in arg_nodes]
            if not args:
                raise ExpressionError("$() needs a node name")
            node_name = str(args[0])
            output = self.data.node_outputs.get(node_name)
            if output is None:
                raise ExpressionError(f"Node '{node_name}' has not run")
            return _NodeProxy(node_name, as_items(output))
        if name == "$items":
            args = [self.eval(arg) for arg in arg_nodes]
            if not args:
                return list(self.data.items)
            output = self.data.node_outputs.get(str(args[0]))
            if output is None:
                raise ExpressionError(f"Node '{args[0]}' has not run")
            return as_items(output)
        if name == "$input":
            return _InputProxy(self.data.items)
        if name == "$node":
            args = [self.eval(arg) for arg in arg_nodes]
            if not args:
                return self.roots["$node"]
            return _LegacyNodes(self.data.node_outputs).lookup(str(args[0]))
        if name == "$getWorkflowStaticData":
            args = [self.eval(arg) for arg in arg_nodes]
            first = args[0] if args else None
            scope = "global" if _is_nullish(first) else str(first)
            bucket = self.data.static_data.get(scope)
            if not isinstance(bucket, dict):
                bucket = {}
                self.data.static_data[scope] = bucket
            return bucket
        raise UnknownReferenceError(f"Unknown function: {name}")

    def _unary(self, op: str, value: Any) -> Any:
        if op == "!":
            return not js_truthy(value)
        if op == "-":
            return _compact_number(-_numeric_operand(value, "-"))
        return _compact_number(_numeric_operand(value, "+"))

    def _binary(self, op: str, left: Any, right: Any) -> Any:
        if op == "+":
            if isinstance(left, str) or isinstance(right, str):
                return self._bounded(js_to_string(left) + js_to_string(right))
            if isinstance(left, list) and isinstance(right, list):
                return left + right
            return _compact_number(_numeric_operand(left, op) + _numeric_operand(right, op))
        if op == "===":
            return js_strict_equals(left, right)
        if op == "!==":
            return not js_strict_equals(left, right)
        if op == "==":
            return js_loose_equals(left, right)
        if op == "!=":
            return not js_loose_equals(left, right)
        if isinstance(left, str) and isinstance(right, str):
            if op == "<":
                return left < right
            if op == "<=":
                return left <= right
            if op == ">":
                return left > right
            return left >= right
        left_num = _numeric_operand(left, op)
        right_num = _numeric_operand(right, op)
        if op == "<":
            return left_num < right_num
        if op == "<=":
            return left_num <= right_num
        if op == ">":
            return left_num > right_num
        if op == ">=":
            return left_num >= right_num
        if op == "-":
            return _compact_number(left_num - right_num)
        if op == "*":
            return _compact_number(left_num * right_num)
        if op == "/":
            if right_num == 0:
                return float("inf") if left_num >= 0 else float("-inf")
            return _compact_number(left_num / right_num)
        if op == "%":
            if right_num == 0:
                return float("nan")
            return _compact_number(math.fmod(left_num, right_num))
        raise ExpressionError(f"Unsupported operator '{op}'")

    def _call(self, node: Any) -> Any:
        _, receiver_node, method, arg_nodes, optional = node
        receiver = self.eval(receiver_node)
        if _is_nullish(receiver):
            if optional:
                return UNDEFINED
            raise ExpressionError(f"Cannot read properties of {js_to_string(receiver)} (reading '{method}')")

        # Node / input proxies resolve their own members.
        if isinstance(receiver, (_NodeProxy, _LegacyNode, _InputProxy)):
            if method == "all":
                return list(receiver.items)
            if method == "first":
                return receiver.items[0] if receiver.items else None
            if method == "last":
                return receiver.items[-1] if receiver.items else None
            raise ExpressionError(f"Unknown node method '{method}'")

        # Array callbacks need the AST body, so they resolve before arguments.
        if isinstance(receiver, list) and arg_nodes and arg_nodes[0][0] == "lambda":
            if len(arg_nodes) > 1:
                raise ExpressionError("Callback methods take one function")
            _, param, body = arg_nodes[0]
            return self._iterate(receiver, method, param, body)

        args = [self.eval(arg) for arg in arg_nodes]

        if isinstance(receiver, N8nDateTime):
            return self._datetime_call(receiver, method, args)
        if isinstance(receiver, str):
            return self._string_call(receiver, method, args)
        if isinstance(receiver, bool):
            raise ExpressionError(f"Unsupported boolean method '{method}'")
        if isinstance(receiver, (int, float)):
            return self._number_call(float(receiver), method, args)
        if isinstance(receiver, list):
            return self._array_call(receiver, method, args)
        if isinstance(receiver, dict):
            return self._object_call(receiver, method, args)
        raise ExpressionError(f"Unsupported method '{method}'")

    def _iterate(self, receiver: List[Any], method: str, param: str, body: Any) -> Any:
        if method not in ("map", "filter", "find", "some", "every"):
            raise ExpressionError(f"Unknown array method '{method}'")
        previous_name, previous_value = self.param_name, self.param_value
        results: List[Any] = []
        found: Any = UNDEFINED
        try:
            self.param_name = param
            for element in receiver:
                self.param_value = element
                value = self.eval(body)
                if method == "map":
                    results.append(value)
                elif method == "filter":
                    if js_truthy(value):
                        results.append(element)
                elif method == "find":
                    if js_truthy(value):
                        return element
                elif method == "some":
                    if js_truthy(value):
                        return True
                elif method == "every":
                    if not js_truthy(value):
                        return False
        finally:
            self.param_name, self.param_value = previous_name, previous_value
        if method == "map":
            return self._bounded(results)
        if method == "filter":
            return results
        if method == "find":
            return found
        if method == "some":
            return False
        return True

    def _datetime_call(self, value: N8nDateTime, method: str, args: List[Any]) -> Any:
        if method == "toMillis":
            return value.to_millis()
        if method == "toSeconds":
            return int(value.to_millis() // 1000)
        if method == "toUnixInteger":
            return int(value.to_millis() // 1000)
        if method in ("toISO", "toString"):
            return value.to_iso()
        if method == "toISODate":
            return value.to_iso_date()
        if method == "toISOTime":
            return value.to_iso_time()
        if method == "plus":
            return value.plus(*args)
        if method == "minus":
            return value.minus(*args)
        if method == "format":
            return value.format(*args)
        raise ExpressionError(f"Unknown date method '{method}'")

    def _string_call(self, value: str, method: str, args: List[Any]) -> Any:
        if method == "toUpperCase":
            return value.upper()
        if method == "toLowerCase":
            return value.lower()
        if method == "trim":
            return value.strip()
        if method == "trimStart":
            return value.lstrip()
        if method == "trimEnd":
            return value.rstrip()
        if method == "includes":
            return js_to_string(args[0]) in value
        if method == "startsWith":
            return value.startswith(js_to_string(args[0]))
        if method == "endsWith":
            return value.endswith(js_to_string(args[0]))
        if method == "slice":
            return js_slice(value, *args)
        if method == "substring":
            start = int(_js_to_number(args[0])) if args else 0
            end = int(_js_to_number(args[1])) if len(args) > 1 else len(value)
            return value[max(0, start) : max(0, end)]
        if method == "indexOf":
            return value.find(js_to_string(args[0]))
        if method == "charAt":
            index = int(_js_to_number(args[0])) if args else 0
            return value[index] if 0 <= index < len(value) else ""
        if method == "replace":
            return value.replace(js_to_string(args[0]), js_to_string(args[1]), 1) if len(args) > 1 else value
        if method == "replaceAll":
            return value.replace(js_to_string(args[0]), js_to_string(args[1])) if len(args) > 1 else value
        if method == "split":
            separator = args[0] if args else None
            return value.split() if separator is None else value.split(js_to_string(separator))
        if method == "concat":
            return value + "".join(js_to_string(arg) for arg in args)
        if method == "padStart":
            width = int(_js_to_number(args[0])) if args else 0
            pad = js_to_string(args[1]) if len(args) > 1 else " "
            return value.rjust(width, pad[:1] or " ")
        if method == "padEnd":
            width = int(_js_to_number(args[0])) if args else 0
            pad = js_to_string(args[1]) if len(args) > 1 else " "
            return value.ljust(width, pad[:1] or " ")
        if method == "repeat":
            count = int(_js_to_number(args[0])) if args else 0
            return self._bounded(value * max(0, count))
        if method in _STRING_EXTENSIONS:
            return self._string_extension(value, method, args)
        raise ExpressionError(f"Unknown string method '{method}'")

    def _string_extension(self, value: str, method: str, args: List[Any]) -> Any:
        if method == "isEmpty":
            return value == ""
        if method == "isNotEmpty":
            return value != ""
        if method == "toNumber":
            number = _js_to_number(value)
            return _compact_number(number) if not math.isnan(number) else None
        if method == "toBoolean":
            return js_truthy(value)
        if method == "toJson":
            try:
                return json.loads(value)
            except ValueError as exc:
                raise ExpressionError("String is not valid JSON") from exc
        raise ExpressionError(f"Unknown string method '{method}'")

    def _number_call(self, value: float, method: str, args: List[Any]) -> Any:
        if method == "toFixed":
            digits = int(_js_to_number(args[0])) if args else 0
            return f"{value:.{max(0, min(digits, 100))}f}"
        if method == "toPrecision":
            digits = int(_js_to_number(args[0])) if args else 0
            return f"{value:.{max(1, min(digits, 100))}g}"
        if method == "toString":
            return js_number_str(value)
        if method == "toNumber":
            return _compact_number(value)
        raise ExpressionError(f"Unknown number method '{method}'")

    def _array_call(self, value: List[Any], method: str, args: List[Any]) -> Any:
        if method == "includes":
            return any(js_strict_equals(entry, args[0]) for entry in value) if args else False
        if method == "indexOf":
            for index, entry in enumerate(value):
                if args and js_strict_equals(entry, args[0]):
                    return index
            return -1
        if method == "lastIndexOf":
            for index in range(len(value) - 1, -1, -1):
                if args and js_strict_equals(value[index], args[0]):
                    return index
            return -1
        if method == "join":
            separator = js_to_string(args[0]) if args else ","
            parts = ["" if _is_nullish(entry) else js_to_string(entry) for entry in value]
            return separator.join(parts)
        if method == "slice":
            return js_slice(value, *args)
        if method == "at":
            index = int(_js_to_number(args[0])) if args else 0
            if index < 0:
                index += len(value)
            return value[index] if 0 <= index < len(value) else None
        if method == "concat":
            result = list(value)
            for arg in args:
                result.extend(arg if isinstance(arg, list) else [arg])
            return result
        if method == "flat":
            depth = int(_js_to_number(args[0])) if args else 1
            return _flatten(value, max(0, depth))
        if method == "toReversed":
            return list(reversed(value))
        if method == "toSorted":
            return sorted(value, key=_sort_key)
        if method == "keys":
            return list(range(len(value)))
        if method == "values":
            return list(value)
        raise ExpressionError(f"Unknown array method '{method}'")

    def _object_call(self, value: Dict[Any, Any], method: str, args: List[Any]) -> Any:
        if method == "keys":
            return list(value.keys())
        if method == "values":
            return list(value.values())
        if method == "toJsonString":
            return json.dumps(value, separators=(",", ":"), default=str)
        if method == "hasField":
            return js_to_string(args[0]) in value if args else False
        if method == "isEmpty":
            return len(value) == 0
        if method == "isNotEmpty":
            return len(value) > 0
        if method == "merge":
            merged = dict(value)
            if args and isinstance(args[0], dict):
                merged.update(args[0])
            return merged
        raise ExpressionError(f"Unknown object method '{method}'")


def _sort_key(value: Any) -> Tuple[int, Any]:
    if _is_nullish(value):
        return (0, "")
    if isinstance(value, bool):
        return (1, int(value))
    if isinstance(value, (int, float)):
        return (1, float(value))
    return (2, js_to_string(value))


def _index_list(value: Sequence[Any], key: Any) -> Any:
    try:
        index = int(key) if not isinstance(key, bool) else 0
    except (TypeError, ValueError):
        raise ExpressionError(f"Invalid index {key!r}") from None
    if index < 0:
        index += len(value)
    if 0 <= index < len(value):
        return value[index]
    return UNDEFINED


def _flatten(value: List[Any], depth: int) -> List[Any]:
    out: List[Any] = []
    for entry in value:
        if isinstance(entry, list) and depth > 0:
            out.extend(_flatten(entry, depth - 1))
        else:
            out.append(entry)
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def evaluate_expression(source: str, data: Optional[ExpressionData] = None) -> Any:
    """Evaluate one expression body (without the surrounding ``{{ }}``)."""
    ast = compile_expression(source.strip())
    value = _Evaluator(data or ExpressionData()).eval(ast)
    return None if value is UNDEFINED else value


def _render(template: str, data: ExpressionData) -> Any:
    single = _EXPR_RE.fullmatch(template.strip())
    if single and "{{" not in single.group(1) and "}}" not in single.group(1):
        return evaluate_expression(single.group(1), data)

    def replace(match: re.Match) -> str:
        return output_string(evaluate_expression(match.group(1), data))

    return _EXPR_RE.sub(replace, template)


def evaluate_template(value: Any, data: Optional[ExpressionData] = None) -> Any:
    """Resolve expressions in a config value (recursing into dicts/lists)."""
    payload = data or ExpressionData()
    if isinstance(value, dict):
        return {key: evaluate_template(item, payload) for key, item in value.items()}
    if isinstance(value, list):
        return [evaluate_template(item, payload) for item in value]
    if not isinstance(value, str):
        return value
    if "{{" not in value:
        # A bare expression is still valid input for operand resolution.
        if _looks_like_expression(value):
            return evaluate_expression(value, payload)
        return value
    return _render(value, payload)


_BARE_START_RE = re.compile(r"^\s*(\$[A-Za-z_]|[A-Za-z_$][A-Za-z0-9_$]*\s*(\.|\[|\())")


def _looks_like_expression(value: str) -> bool:
    match = _BARE_START_RE.match(value)
    if not match:
        return False
    head = value.strip().split(".")[0].split("[")[0].strip()
    return head in _LEGACY_ROOTS or head.startswith("$")


def has_expression(value: Any) -> bool:
    """True when a config value contains an n8n-style expression."""
    if isinstance(value, dict):
        return any(has_expression(item) for item in value.values())
    if isinstance(value, list):
        return any(has_expression(item) for item in value)
    return isinstance(value, str) and "{{" in value
