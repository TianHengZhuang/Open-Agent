import { describe, expect, it } from 'vitest';
import {
  evaluateCondition,
  evaluateExpression,
  ExpressionError,
  makeInput,
  resolveValue,
} from './expressions';

const ctx = {
  json: { name: 'Ada', n: 2 },
  items: [{ json: { a: 1 } }, { json: { a: 2 } }],
  nodeOutputs: { Fetch: { json: { ok: true } } },
  vars: { env: 'prod' },
  now: new Date('2026-01-01T00:00:00Z'),
  execution: { id: 'exec_1', mode: 'preview' },
  workflow: { name: 'Support' },
};

describe('evaluateExpression', () => {
  it('reads the current item and n8n input helpers', () => {
    expect(evaluateExpression('$json.name', ctx)).toBe('Ada');
    expect(evaluateExpression('$input.first().json.a', ctx)).toBe(1);
    expect(evaluateExpression('$input.last().json.a', ctx)).toBe(2);
    expect(evaluateExpression('$input.all().length', ctx)).toBe(2);
    expect(evaluateExpression('$input.first().json.a + $json.n', ctx)).toBe(3);
  });

  it('exposes node outputs, vars, execution and workflow', () => {
    expect(evaluateExpression('$node["Fetch"].json.ok', ctx)).toBe(true);
    expect(evaluateExpression('$node["Missing"].json.anything', ctx)).toBeUndefined();
    expect(evaluateExpression('$vars.env', ctx)).toBe('prod');
    expect(evaluateExpression('$execution.id', ctx)).toBe('exec_1');
    expect(evaluateExpression('$workflow.name', ctx)).toBe('Support');
    expect(evaluateExpression('$now.getUTCFullYear()', ctx)).toBe(2026);
  });

  it('supports ordinary JavaScript expressions', () => {
    expect(evaluateExpression("$json.name.toUpperCase() + '!'", ctx)).toBe('ADA!');
    expect(evaluateExpression('$json.n > 1 ? "high" : "low"', ctx)).toBe('high');
    expect(evaluateExpression('$json.missing ?? "fallback"', ctx)).toBe('fallback');
  });

  it('isolates globals instead of touching the page', () => {
    expect(evaluateExpression('typeof fetch', ctx)).toBe('undefined');
    expect(evaluateExpression('typeof window', ctx)).toBe('undefined');
    expect(() => evaluateExpression('fetch("https://example.com")', ctx)).toThrow(ExpressionError);
  });

  it('reports parse and runtime failures with the expression attached', () => {
    try {
      evaluateExpression('$json.name.', ctx);
      throw new Error('expected a failure');
    } catch (error) {
      expect(error).toBeInstanceOf(ExpressionError);
      expect((error as ExpressionError).expression).toBe('$json.name.');
    }
    expect(() => evaluateExpression('nope()', ctx)).toThrow(ExpressionError);
  });
});

describe('resolveValue', () => {
  it('keeps the expression type for a lone expression', () => {
    expect(resolveValue('{{ $json.n + 3 }}', ctx)).toBe(5);
    expect(resolveValue('={{ $json.name }}', ctx)).toBe('Ada');
  });

  it('interpolates mixed text and recurses into config objects', () => {
    expect(resolveValue('Hi {{ $json.name }}!', ctx)).toBe('Hi Ada!');
    expect(
      resolveValue({ greeting: 'Hi {{ $json.name }}', untouched: 'plain' }, ctx),
    ).toEqual({ greeting: 'Hi Ada', untouched: 'plain' });
  });

  it('leaves plain strings alone and evaluates conditions safely', () => {
    expect(resolveValue('no expressions here', ctx)).toBe('no expressions here');
    expect(evaluateCondition('$json.n > 1', ctx)).toBe(true);
    expect(evaluateCondition('$json.n < 1', ctx)).toBe(false);
    expect(evaluateCondition('nope()', ctx)).toBe(false);
  });
});

describe('makeInput', () => {
  it('handles an empty item list without throwing', () => {
    const input = makeInput([]);
    expect(input.first()).toEqual({ json: {}, binary: undefined, pairedItem: undefined });
    expect(input.all()).toEqual([]);
  });
});
