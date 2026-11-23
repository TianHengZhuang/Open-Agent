// Chat types shared by the studio drawer and the hosted /chat/[id] page.
// A chat session is a stored conversation for one workflow; the workflow's
// Chat Trigger receives the n8n-compatible item for each turn.

export type ChatRole = 'user' | 'assistant';

export interface ChatAttachment {
  name: string;
  mimeType: string;
  /** `data:` URL — never uploaded anywhere by the chat surface itself. */
  dataUrl: string;
  size: number;
}

/** One node in the per-turn execution trace. */
export interface ChatRunStep {
  node_id: string;
  node_type: string;
  node_name: string;
  status: string;
  duration_ms: number;
  error?: string | null;
  reason?: string | null;
}

export interface ChatRunMeta {
  execution_id: string | null;
  status: string;
  steps: ChatRunStep[];
  duration_ms: number;
  model: string;
  /** True when the workflow's model node was skipped (no endpoint/model). */
  modelSkipped: boolean;
  /** The node that produced the reply, for "Inspect in canvas". */
  replyNodeId: string | null;
  error?: string | null;
}

export interface ChatMessage {
  id: string;
  role: ChatRole;
  content: string;
  createdAt: number;
  attachments?: ChatAttachment[];
  run?: ChatRunMeta;
  error?: string | null;
  /** True while the reply is streaming. */
  pending?: boolean;
}

export interface ChatSession {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  messages: ChatMessage[];
}

export interface ChatState {
  version: 1;
  activeId: string | null;
  sessions: ChatSession[];
  /** Workflow the sessions belong to (defensive: storage is per workflow). */
  workflowId: string;
}

export interface ChatStreamMeta {
  session_id: string;
  model: string;
  provider: string;
  local: boolean;
}

export interface ChatStreamDone {
  session_id: string;
  status: string;
  reply: string;
  reply_node_id: string | null;
  execution_id: string | null;
  workflow_id: string;
  steps: ChatRunStep[];
  summary: {
    total?: number;
    succeeded?: number;
    failed?: number;
    skipped?: number;
    waiting?: number;
    duration_ms?: number;
  };
  error: string | null;
  model: string;
  model_skipped: boolean;
}

export type ChatStreamEvent =
  | { type: 'meta'; data: ChatStreamMeta }
  | { type: 'token'; data: { text: string } }
  | { type: 'done'; data: ChatStreamDone }
  | { type: 'error'; data: { message: string } };
