"""Unit tests for the n8n-compatible expression evaluator and item model."""

from __future__ import annotations

import pytest

from openagent.runtime import expressions as ex
from openagent.runtime.items import as_items, first_json, is_item, make_item, paired_item_index
from openagent.runtime.local_engine import RunContext, resolve


def data(**overrides):
    payload = {
        "json": {"name": "ada", "tags": ["a", "b"], "total": 3, "nested": {"id": 7}},
        "items": [
            make_item({"name": "ada"}, index=0),
            make_item({"name": "grace"}, index=1),
        ],
        "node_outputs": {"Fetch": {"body": {"id": 42}, "items": [{"id": 1}, {"id": 2}]}},
        "variables": {"token": "abc"},
        "workflow": {"id": "wf1", "name": "Demo"},
        "execution": {"id": "run-9"},
        "parameter": {"limit": 10},
        "binary": {"file": {"fileName": "a.txt"}},
        "env": {"HOME": "/tmp"},
        "item_index": 2,
        "run_index": 1,
    }
    payload.update(overrides)
    return ex.ExpressionData(**payload)


def ev(source: str, **overrides):
    return ex.evaluate_expression(source, data(**overrides))


def test_arithmetic_and_precedence():
    assert ev("1 + 2 * 3") == 7
    assert ev("(1 + 2) * 3") == 9
    assert ev("7 % 2") == 1
    assert ev("10 / 4") == 2.5
    assert ev("-2 + 3") == 1


def test_string_semantics():
    assert ev("'a' + 1") == "a1"
    assert ex.output_string(ev("1 + 2")) == "3"
    assert ev("'a' == 'a'") is True
    assert ev("1 === 1") is True
    assert ev("1 === '1'") is False
    assert ev("1 == '1'") is True


def test_roots_and_member_access():
    assert ev("$json.name") == "ada"
    assert ev("$json.tags[1]") == "b"
    assert ev("$json.nested.id") == 7
    assert ev("$vars.token") == "abc"
    assert ev("$env.HOME") == "/tmp"
    assert ev("$workflow.name") == "Demo"
    assert ev("$execution.id") == "run-9"
    assert ev("$parameter.limit") == 10
    assert ev("$binary.file.fileName") == "a.txt"
    assert ev("$itemIndex") == 2
    assert ev("$runIndex") == 1


def test_node_references():
    assert ev('$node["Fetch"].json.body.id') == 42
    assert ev("$('Fetch').first().json.id") == 1
    assert ev("$('Fetch').last().json.id") == 2
    assert ev("$('Fetch').all().length") == 2
    assert ev('$items("Fetch").length') == 2
    assert ev("$input.first().json.name") == "ada"
    assert ev("$input.all().length") == 2


def test_optional_chaining():
    assert ev("$json.missing?.name") is None
    assert ev("$json.nested?.id") == 7
    with pytest.raises(ex.ExpressionError):
        ev("$json.missing.name")


def test_string_methods():
    assert ev("$json.name.toUpperCase()") == "ADA"
    assert ev("'a,b,c'.split(',').length") == 3
    assert ev("' x '.trim()") == "x"
    assert ev("'hello'.includes('ell')") is True
    assert ev("'hello'.slice(1, 3)") == "el"
    assert ev("'a'.padStart(3, '0')") == "00a"


def test_array_and_iterator_methods():
    assert ev("$json.tags.join('-')") == "a-b"
    assert ev("$json.tags.includes('a')") is True
    assert ev("$json.tags.map(t => t.toUpperCase())") == ["A", "B"]
    assert ev("$json.tags.filter(t => t !== 'a')") == ["b"]
    assert ev("$json.tags.map(t => t + '!').join(',')") == "a!,b!"
    assert ev("$('Fetch').all().map(i => i.json.id)") == [1, 2]


def test_time_helpers():
    assert ex.evaluate_expression("$now.year", ex.ExpressionData()).__class__ is int
    iso = ex.evaluate_expression("$now.format('yyyy-MM-dd')", ex.ExpressionData())
    assert len(iso) == 10 and iso[4] == "-"
    assert ex.evaluate_expression("$now.plus(1, 'days').toISODate() > $today.toISODate()", ex.ExpressionData()) is True
    assert ex.evaluate_expression("$now.minus(1, 'days').toISODate() < $today.toISODate()", ex.ExpressionData()) is True


def test_ternary_and_logic():
    assert ev("$json.total > 2 ? 'big' : 'small'") == "big"
    assert ev("$json.missing || 'fallback'") == "fallback"
    assert ev("$json.name && 'yes'") == "yes"


def test_output_stringification():
    assert ex.output_string({"a": 1}) == '{"a": 1}'
    assert ex.output_string([1, 2]) == "[1, 2]"
    assert ex.output_string(None) == ""
    assert ex.output_string(True) == "true"


def test_unknown_reference_raises():
    with pytest.raises(ex.UnknownReferenceError):
        ev("mystery.value")


def test_template_resolution():
    payload = data()
    assert ex.evaluate_template("Hi {{ $json.name }}", payload) == "Hi ada"
    assert ex.evaluate_template("{{ $json.total + 1 }}", payload) == 4
    assert ex.evaluate_template({"a": "{{ $vars.token }}"}, payload) == {"a": "abc"}


# ---------------------------------------------------------------------------
# Item model
# ---------------------------------------------------------------------------


def test_items_normalisation():
    assert as_items(None) == []
    assert as_items({"a": 1})[0]["json"] == {"a": 1}
    assert len(as_items([1, 2])) == 2
    assert as_items({"items": [{"a": 1}]})[0]["json"] == {"a": 1}

    envelope = make_item({"a": 1}, index=0)
    assert is_item(envelope)
    assert as_items([envelope]) == [envelope]
    assert not is_item({"json": 1, "other": 2})


def test_items_helpers():
    items = as_items([{"a": 1}, {"a": 2}])
    assert first_json(items) == {"a": 1}
    assert paired_item_index(items[1]) == 1
    assert paired_item_index({"a": 1}) is None


# ---------------------------------------------------------------------------
# Local engine integration
# ---------------------------------------------------------------------------


def test_engine_resolves_n8n_style_expressions():
    ctx = RunContext(
        trigger={"json": {"chatInput": "hello"}, "_execution_id": "run-1"},
        variables={"token": "t"},
        outputs={"classify": {"text": "spam"}},
        item={"json": {"chatInput": "hello"}},
    )
    assert resolve("{{ $json.chatInput }}", ctx) == "hello"
    assert resolve("{{ json.chatInput }}", ctx) == "hello"
    assert resolve("{{ nodes.classify.text }}", ctx) == "spam"
    assert resolve("Reply: {{ $json.chatInput.toUpperCase() }}", ctx) == "Reply: HELLO"


def test_engine_keeps_legacy_fallback():
    ctx = RunContext(trigger={"ticket_id": "T-1"}, variables={"x": 2})
    assert resolve("Ticket {{ticket_id}}", ctx) == "Ticket T-1"
    assert resolve("{{ variables.x }}", ctx) == 2
