// Workflow definition contract (v1) — mirrors the backend
// `openagent.services.workflow_definition` validator and the versioned
// `definition` JSON stored on workflow versions.

export const SCHEMA_VERSION = '1.1' as const;
/** 1.0 documents remain readable and are upgraded additively on import. */
export const SUPPORTED_SCHEMA_VERSIONS = ['1.0', '1.1'] as const;

export type TriggerType = 'manual' | 'webhook' | 'schedule' | 'event' | 'chat';

export type WorkflowNodeType =
  | 'agent'
  | 'prompt'
  | 'condition'
  | 'switch'
  | 'merge'
  | 'loop'
  | 'set'
  | 'transform'
  | 'filter'
  | 'map'
  | 'sort'
  | 'limit'
  | 'remove_duplicates'
  | 'split_out'
  | 'variable'
  | 'approval'
  | 'webhook'
  | 'tool'
  | 'connector_action'
  | 'connector_trigger'
  | 'connector_search'
  | 'connector_resource'
  | 'delay'
  | 'subworkflow'
  | 'code_agent'
  | 'code_search'
  | 'code_read'
  | 'code_patch'
  | 'code_test'
  | 'code_lint'
  | 'code_review'
  | 'git_commit'
  | 'create_pr'
  | 'verify'
  | 'evaluate'
  | 'assert'
  | 'quality_gate'
  | 'retry'
  | 'correct';

export type EdgeWhen = 'always' | 'success' | 'failure';

/** Payload kinds a port can carry. `items` is the item stream (n8n main). */
export type PortDataType =
  | 'any'
  | 'items'
  | 'json'
  | 'text'
  | 'number'
  | 'boolean'
  | 'binary';

/** Connection class. Error routing is a contract; no node exposes it yet. */
export type ConnectionType = 'main' | 'error';

export type VariableType = 'string' | 'number' | 'boolean' | 'json';

export type WorkflowStatus = 'draft' | 'active' | 'archived' | 'deprecated';

export interface Position {
  x: number;
  y: number;
}

export interface WorkflowTrigger {
  id: string;
  type: TriggerType;
  name: string;
  config: Record<string, unknown>;
  /** Canvas-only layout hint; ignored by the backend validator. */
  position?: Position;
}

export interface RetryPolicy {
  max_attempts: number;
}

export interface WorkflowNode {
  id: string;
  type: WorkflowNodeType;
  /** Registry version of the node definition (migration foundation). */
  type_version?: number;
  name: string;
  position?: Position;
  config: Record<string, unknown>;
  retry_policy?: RetryPolicy;
  timeout_seconds?: number;
  approval_required?: boolean;
  /** Excluded from validation errors and future execution when true. */
  disabled?: boolean;
  /** Free-form author note (metadata, never executed). */
  notes?: string;
  /** Grouping foundation (future canvas groups). */
  group_id?: string;
}

export interface EdgeCondition {
  when: EdgeWhen;
  expression?: string;
}

export interface WorkflowEdge {
  id: string;
  from: string;
  to: string;
  /**
   * 1.1 typed endpoints. Optional forever: a legacy edge resolves its source
   * port from `label` (condition/switch) or the node's first output, and its
   * target port from the node's first required input.
   */
  from_port?: string;
  to_port?: string;
  connection_type?: ConnectionType;
  /** Legacy branch/port label kept for the execution engine and n8n export. */
  label?: string;
  condition?: EdgeCondition;
  /** Free-form, non-executed connection metadata (UI/analytics). */
  metadata?: Record<string, unknown>;
}

export interface WorkflowVariable {
  name: string;
  type: VariableType;
  default?: unknown;
  required?: boolean;
  description?: string;
}

export interface StickyNote {
  id: string;
  /** World-space canvas position (px, 0,0 top-left). */
  x: number;
  y: number;
  /** Canvas-relative; negative allowed. Should not sit on nodes. */
  width: number;
  height: number;
  color: string;
  text: string;
}

/**
 * Output captured by "Pin output" in the node detail view. Pinned data is a
 * canvas-authoring aid: it feeds previews and step tests, while workflow
 * executions ignore it (the backend never reads `settings.pinned_data`).
 */
export interface PinnedDatum {
  /** ISO timestamp of when the data was pinned. */
  pinned_at: string;
  /** Node output object exactly as the engine returned it. */
  outputs: Record<string, unknown>;
  /** Input the node received when the data was captured (reference only). */
  inputs?: Record<string, unknown> | null;
  /** Run status the data came from. */
  status?: string;
}

export interface WorkflowSettings {
  timezone?: string;
  max_concurrency?: number;
  /** Workflow-level execution budget (1..86400s); absent means no limit. */
  execution_timeout_seconds?: number;
  sticky_notes?: StickyNote[];
  /** Node id → pinned output. Canvas-only; never executed. */
  pinned_data?: Record<string, PinnedDatum>;
}

export interface WorkflowDefinition {
  schema_version: string;
  triggers: WorkflowTrigger[];
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  variables: WorkflowVariable[];
  settings: WorkflowSettings;
  /** 1.1: workflow executed when this one fails (same organization). */
  error_workflow_id?: string | null;
  /** 1.1: read-only constants available to expressions. */
  static_data?: Record<string, unknown>;
  /** 1.1: portable, non-executed metadata (author, labels, notes). */
  metadata?: Record<string, unknown>;
}

/** Clipboard payload for copy/paste/duplicate (sensitive values stripped). */
export interface Clipboard {
  triggers: WorkflowTrigger[];
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
}

export interface ValidationIssue {
  code: string;
  message: string;
  severity: 'error' | 'warning';
  node_id?: string;
  field?: string;
  edge_id?: string;
}

export interface ValidationResult {
  valid: boolean;
  errors: ValidationIssue[];
  warnings: ValidationIssue[];
}

// --------------------------------------------------------------------------
// Data-flow contract (schema 1.1). Types only: the execution engine does not
// consume these yet (Prompt 08), and no runtime should claim they work.
// --------------------------------------------------------------------------

export interface BinaryReference {
  mime_type: string;
  file_name?: string;
  /** Stable reference into the binary store; never inline base64 here. */
  data_ref: string;
  size_bytes?: number;
}

export interface DataItemError {
  code?: string;
  message: string;
  retriable?: boolean;
}

export interface DataItem {
  json: Record<string, unknown>;
  binary?: Record<string, BinaryReference>;
  /** Upstream item index/indices this item derived from (n8n pairedItem). */
  paired_item?: number | number[];
  error?: DataItemError;
}

/** Payload leaving one named node port. */
export interface PortPayload {
  port: string;
  connection_type: ConnectionType;
  items: DataItem[];
}

/** One node-level execution trace record (contract for the event stream). */
export interface ExecutionTraceEvent {
  execution_id: string;
  node_id: string;
  node_name?: string;
  port?: string;
  connection_type?: ConnectionType;
  item_index?: number;
  attempt?: number;
  status: 'running' | 'succeeded' | 'failed' | 'skipped' | 'waiting';
  duration_ms?: number;
  input_items?: number;
  output_items?: number;
  error?: DataItemError;
  ts: string;
}

// API shapes ---------------------------------------------------------------

export interface WorkflowRecord {
  id: string;
  organization_id: string;
  name: string;
  slug: string;
  description?: string | null;
  tags?: string[];
  status: WorkflowStatus;
  metadata: Record<string, unknown>;
  version_count: number;
  created_at: string;
  updated_at: string;
}

export interface WorkflowVersionRecord {
  id: string;
  workflow_id: string;
  version: string;
  definition: WorkflowDefinition;
  status: string;
  created_by?: string | null;
  created_at: string;
}

export interface WorkflowExecutionRecord {
  id: string;
  organization_id: string;
  workflow_id: string;
  workflow_version_id?: string | null;
  status: string;
  trigger_type: string;
  started_at?: string | null;
  completed_at?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  created_at: string;
}

export interface WorkflowDetail extends WorkflowRecord {
  latest_version?: WorkflowVersionRecord | null;
  last_execution?: {
    id: string;
    status: string;
    trigger_type: string;
    started_at?: string | null;
    completed_at?: string | null;
  } | null;
}

export function emptyDefinition(): WorkflowDefinition {
  return {
    schema_version: SCHEMA_VERSION,
    triggers: [],
    nodes: [],
    edges: [],
    variables: [],
    settings: {},
    static_data: {},
    metadata: {},
  };
}
