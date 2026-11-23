import { z } from 'zod';

import { WORKFLOW_REGISTRY } from './registry';

// Zod schemas for the workflow definition contract v1.
// Used to type-check imported JSON before it enters the editor.
// Graph rules (cycles, reachability, required config) live in validate.ts
// so the editor can surface them incrementally.

const triggerSchema = z.object({
  id: z.string().min(1),
  type: z.enum(['manual', 'webhook', 'schedule', 'event', 'chat']),
  name: z.string().min(1),
  config: z.record(z.string(), z.unknown()).default({}),
});

const nodeTypeSchema = z
  .string()
  .min(1)
  .refine((type) => WORKFLOW_REGISTRY.nodes.some((n) => n.type === type), {
    message: 'Unknown node type in the canonical registry',
  });

const nodeSchema = z.object({
  id: z.string().min(1),
  type: nodeTypeSchema,
  type_version: z.number().int().min(1).optional(),
  name: z.string().min(1),
  position: z.object({ x: z.number(), y: z.number() }).optional(),
  config: z.record(z.string(), z.unknown()).default({}),
  retry_policy: z.object({ max_attempts: z.number().int().min(0).max(10) }).optional(),
  timeout_seconds: z.number().positive().optional(),
  approval_required: z.boolean().optional(),
  disabled: z.boolean().optional(),
  notes: z.string().max(2000).optional(),
  group_id: z.string().optional(),
});

const edgeSchema = z.object({
  id: z.string().min(1),
  from: z.string().min(1),
  to: z.string().min(1),
  from_port: z.string().min(1).optional(),
  to_port: z.string().min(1).optional(),
  connection_type: z.enum(['main', 'error']).optional(),
  metadata: z.record(z.string(), z.unknown()).optional(),
  label: z.string().optional(),
  condition: z
    .object({
      when: z.enum(['always', 'success', 'failure']).default('always'),
      expression: z.string().optional(),
    })
    .optional(),
});

const variableSchema = z.object({
  name: z.string().regex(/^[A-Za-z_][A-Za-z0-9_]*$/),
  type: z.enum(['string', 'number', 'boolean', 'json']),
  default: z.unknown().optional(),
  required: z.boolean().optional(),
  description: z.string().optional(),
});

export const workflowDefinitionSchema = z.object({
  schema_version: z.string(),
  triggers: z.array(triggerSchema),
  nodes: z.array(nodeSchema),
  edges: z.array(edgeSchema),
  variables: z.array(variableSchema).default([]),
  settings: z.record(z.string(), z.unknown()).default({}),
  // Additive 1.1 fields; optional so 1.0 files still import.
  error_workflow_id: z.string().min(1).nullish(),
  static_data: z.record(z.string(), z.unknown()).default({}),
  metadata: z.record(z.string(), z.unknown()).default({}),
});

export type WorkflowDefinitionInput = z.infer<typeof workflowDefinitionSchema>;
