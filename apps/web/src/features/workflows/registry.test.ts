import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

import { NODE_CATALOG, TRIGGER_CATALOG } from './node-catalog';
import {
  WORKFLOW_REGISTRY,
  buildRegistryDocument,
  checkNodeVersion,
  registryFieldTypesAreValid,
  serializeRegistry,
} from './registry';

const WEB_REGISTRY = path.resolve(process.cwd(), 'src/features/workflows/node-registry.json');
const API_REGISTRY = path.resolve(
  process.cwd(),
  '../../apps/api/src/openagent/registry/node-registry.json',
);

describe('node registry', () => {
  it('serializes every builtin catalog definition', () => {
    const doc = buildRegistryDocument();
    // The authoring catalog is the source of truth; neither side may silently
    // drop a definition when the registry is regenerated.
    expect(doc.nodes.length).toBe(NODE_CATALOG.length);
    expect(doc.triggers.length).toBe(TRIGGER_CATALOG.length);
    expect(doc.nodes.length + doc.triggers.length).toBe(65);
    expect(registryFieldTypesAreValid(doc)).toBe(true);

    const keys = [
      ...doc.nodes.map((n) => `node:${n.type}@${n.version}`),
      ...doc.triggers.map((t) => `trigger:${t.type}@${t.version}`),
    ];
    expect(new Set(keys).size).toBe(keys.length);
    expect(doc.nodes.every((n) => n.version >= 1 && n.outputs.length > 0)).toBe(true);
    expect(doc.nodes.every((n) => n.category.length > 0 && n.label.length > 0)).toBe(true);
  });

  it('keeps defaults limited to declared config fields', () => {
    const doc = buildRegistryDocument();
    for (const node of doc.nodes) {
      const fields = new Set(node.config_fields.map((f) => f.key));
      for (const key of Object.keys(node.default_config)) {
        expect(fields.has(key), `${node.type} default '${key}' has no field`).toBe(true);
      }
    }
  });

  it('matches the generated JSON consumed by the API', () => {
    const expected = serializeRegistry();

    if (process.env.WRITE_NODE_REGISTRY === '1') {
      fs.mkdirSync(path.dirname(API_REGISTRY), { recursive: true });
      fs.writeFileSync(WEB_REGISTRY, expected);
      fs.writeFileSync(API_REGISTRY, expected);
      return;
    }

    const web = fs.readFileSync(WEB_REGISTRY, 'utf8');
    const api = fs.readFileSync(API_REGISTRY, 'utf8');
    expect(web).toBe(expected);
    expect(api).toBe(expected);
    expect(WORKFLOW_REGISTRY.nodes.length).toBe(NODE_CATALOG.length);
  });

  it('reports pinned versions against the registry', () => {
    const latest = Math.max(...NODE_CATALOG.map((n) => n.version));
    expect(checkNodeVersion('condition', latest).error).toBeUndefined();
    expect(checkNodeVersion('condition', latest + 1).error).toMatch(/newer than the registry/);
    if (latest > 1) {
      expect(checkNodeVersion('condition', latest - 1).warning).toMatch(/v\d+ is available/);
    }
    expect(checkNodeVersion('not_a_node', 99)).toEqual({});
  });
});
