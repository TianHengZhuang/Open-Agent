'use client';

import * as React from 'react';
import { nodeMeta, outputPortsFor, triggerMeta } from '@/features/workflows/node-catalog';
import type { WorkflowEditor } from '@/features/workflows/use-workflow-editor';
import type { StickyNote, WorkflowDefinition, WorkflowNode } from '@/features/workflows/types';
import { WorkflowMinimap } from './WorkflowMinimap';
import { CanvasContextMenu, type MenuTarget } from './CanvasContextMenu';
import { cn } from '@/lib/utils';
import {
  Minus,
  Plus,
  Maximize,
  StickyNote as StickyNoteIcon,
  X,
  MousePointer2,
  HelpCircle,
  Sparkles,
  Search,
  Braces,
  Loader2,
  CheckCircle2,
  XCircle,
} from 'lucide-react';

// n8n-style canvas: a left-to-right flow of rounded node cards on a dotted
// grid. Pan, zoom, drag (single + multi), box select, edge creation, context
// menu, minimap, copy/paste/duplicate, keyboard ops, sticky-note annotations,
// inline "+" add-node affordances and live execution status.

export const NODE_W = 244;
export const NODE_H = 84;
export const TRIGGER_H = 84;

export interface Viewport {
  x: number;
  y: number;
  k: number;
}

export interface AddNodeRequest {
  world: { x: number; y: number };
  screen?: { x: number; y: number };
  connectFrom?: { id: string; port: string };
}

/** Category → CSS custom property suffix used for the node icon tile. */
const CATEGORY_VAR: Record<string, string> = {
  Triggers: 'trigger',
  Logic: 'logic',
  Data: 'data',
  AI: 'ai',
  Agents: 'agents',
  Tools: 'tools',
  HTTP: 'http',
  Integrations: 'tools',
  Human: 'human',
  Utilities: 'utility',
  Advanced: 'advanced',
};

function categoryColor(category: string): string {
  const key = CATEGORY_VAR[category] ?? 'utility';
  return `hsl(var(--node-${key}))`;
}

function portPosition(
  kind: 'trigger' | 'node',
  pos: { x: number; y: number },
  port: 'in' | string,
  outputs: string[],
): { x: number; y: number } {
  const h = kind === 'trigger' ? TRIGGER_H : NODE_H;
  if (port === 'in') return { x: pos.x, y: pos.y + h / 2 };
  const n = outputs.length;
  const idx = Math.max(0, outputs.indexOf(port));
  if (n <= 1) return { x: pos.x + NODE_W, y: pos.y + h / 2 };
  const gap = h / (n + 1);
  return { x: pos.x + NODE_W, y: pos.y + gap * (idx + 1) };
}

function edgePath(a: { x: number; y: number }, b: { x: number; y: number }): string {
  const dx = Math.max(52, Math.abs(b.x - a.x) / 2);
  return `M ${a.x} ${a.y} C ${a.x + dx} ${a.y}, ${b.x - dx} ${b.y}, ${b.x} ${b.y}`;
}

function clip(text: string, max = 30): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

function nodeSubtitle(metaLabel: string, node: WorkflowNode): string {
  const c = node.config ?? {};
  switch (node.type) {
    case 'agent':
      return c.agent_id ? `Agent ${String(c.agent_id).slice(0, 12)}` : 'Choose an agent';
    case 'prompt':
      return c.model ? String(c.model) : 'Single model call';
    case 'condition':
      return c.expression ? String(c.expression) : 'Boolean branch';
    case 'webhook':
      return `${String(c.method ?? 'POST')} request`;
    case 'delay':
      return c.duration_seconds ? `Wait ${String(c.duration_seconds)}s` : 'Wait';
    case 'approval':
      return 'Human sign-off';
    case 'set':
      return 'Assign variables';
    case 'switch':
      return 'Route by expression';
    default:
      return metaLabel;
  }
}

interface Box {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

const STICKY_COLORS = ['#fff7d6', '#e8f7ff', '#e9fbe9', '#ffe9f3'];

function RunGlyph({ status, x, y }: { status?: string; x: number; y: number }) {
  if (!status) return null;
  const tone =
    status === 'succeeded'
      ? '#10b981'
      : status === 'failed'
        ? '#ef4444'
        : status === 'waiting'
          ? '#f59e0b'
          : '#94a3b8';
  return (
    <g transform={`translate(${x},${y})`} aria-hidden>
      <circle r={8} fill={tone} />
      {status === 'succeeded' && (
        <path d="M -3.4 0 L -1 2.6 L 3.6 -2.6" fill="none" stroke="white" strokeWidth={1.9} strokeLinecap="round" strokeLinejoin="round" />
      )}
      {status === 'failed' && (
        <path d="M -3 -3 L 3 3 M 3 -3 L -3 3" fill="none" stroke="white" strokeWidth={1.9} strokeLinecap="round" />
      )}
      {status === 'waiting' && (
        <>
          <circle r={3.6} fill="none" stroke="white" strokeWidth={1.4} />
          <path d="M 0 -1.8 V 0 L 1.6 1.2" fill="none" stroke="white" strokeWidth={1.2} strokeLinecap="round" />
        </>
      )}
      {status === 'skipped' && (
        <path d="M -3.4 0 H 3.4" fill="none" stroke="white" strokeWidth={1.9} strokeLinecap="round" />
      )}
    </g>
  );
}

interface DragState {
  ids: string[];
  startX: number;
  startY: number;
  before: WorkflowDefinition;
  sticky?: boolean;
}

export function WorkflowCanvas({
  editor,
  readOnly = false,
  runStatus,
  runItems,
  runErrors,
  activeNodeIds,
  running = false,
  lastRun,
  onOpenRunPanel,
  onAddNode,
  onBuildWithAi,
  onSearchNodes,
  onOpenVariables,
  onOpenNodeDetail,
}: {
  editor: WorkflowEditor;
  readOnly?: boolean;
  /** Node/trigger id → status of the most recent test run. */
  runStatus?: Record<string, string>;
  /** Node/trigger id → number of output items from the last run. */
  runItems?: Record<string, number>;
  /** Node/trigger id → error message from the last run (tooltip on the badge). */
  runErrors?: Record<string, string>;
  /** Nodes currently executing (single-step tests); rendered with a pulse. */
  activeNodeIds?: string[];
  /** A run is in flight (whole workflow) — shows the canvas HUD chip. */
  running?: boolean;
  /** Outcome of the last test run, summarised on the canvas after the panel closes. */
  lastRun?: {
    status: string;
    succeeded: number;
    failed: number;
    skipped: number;
    waiting: number;
  } | null;
  /** Reopens the run panel from the canvas run chip. */
  onOpenRunPanel?: () => void;
  /** Opens the 3-pane node detail view (input · parameters · output). */
  onOpenNodeDetail?: (id: string) => void;
  /** Opens the n8n-style node creator at a world/screen position. */
  onAddNode?: (request: AddNodeRequest) => void;
  /** Opens the AI assistant to draft the workflow from a prompt. */
  onBuildWithAi?: () => void;
  /** Focuses the node palette search. */
  onSearchNodes?: () => void;
  /** Opens the workflow settings panel where variables live. */
  onOpenVariables?: () => void;
}) {
  const { definition, selection, selectedIds, select, validation } = editor;
  const svgRef = React.useRef<SVGSVGElement>(null);
  const containerRef = React.useRef<HTMLDivElement>(null);
  const [view, setView] = React.useState<Viewport>({ x: 40, y: 40, k: 1 });
  const [size, setSize] = React.useState({ w: 800, h: 560 });
  const [cursor, setCursor] = React.useState<{ x: number; y: number } | null>(null);
  const [box, setBox] = React.useState<Box | null>(null);
  const [menu, setMenu] = React.useState<{
    x: number;
    y: number;
    world: { x: number; y: number };
    target: MenuTarget;
  } | null>(null);
  const [noteDraft, setNoteDraft] = React.useState<Record<string, string>>({});
  const [helpOpen, setHelpOpen] = React.useState(false);
  const dragRef = React.useRef<DragState | null>(null);
  const panRef = React.useRef<{ sx: number; sy: number; vx: number; vy: number } | null>(null);
  const boxRef = React.useRef<{ sx: number; sy: number } | null>(null);
  const didInteract = React.useRef(false);
  const readOnlyRef = React.useRef(readOnly);
  readOnlyRef.current = readOnly;

  const stickies = React.useMemo(
    () => definition.settings?.sticky_notes ?? [],
    [definition.settings?.sticky_notes],
  );

  React.useEffect(() => {
    const el = containerRef.current;
    const measure = () => {
      const r = el?.getBoundingClientRect();
      if (!r) return;
      setSize((prev) =>
        Math.abs(prev.w - r.width) > 0.5 || Math.abs(prev.h - r.height) > 0.5
          ? { w: r.width, h: r.height }
          : prev,
      );
    };
    measure();
    const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(measure) : null;
    if (el && observer) observer.observe(el);
    window.addEventListener('resize', measure);
    return () => {
      observer?.disconnect();
      window.removeEventListener('resize', measure);
    };
  }, []);

  React.useEffect(() => {
    setNoteDraft((prev) => {
      const next = { ...prev };
      for (const s of stickies) if (!(s.id in next)) next[s.id] = s.text;
      return next;
    });
  }, [stickies]);

  const errorIds = React.useMemo(
    () => new Set(validation.errors.map((e) => e.node_id).filter(Boolean) as string[]),
    [validation],
  );

  const kindOf = React.useCallback(
    (id: string): 'trigger' | 'node' | 'edge' => {
      if (definition.triggers.some((t) => t.id === id)) return 'trigger';
      if (definition.nodes.some((n) => n.id === id)) return 'node';
      return 'edge';
    },
    [definition],
  );

  const posOf = React.useCallback(
    (id: string) => {
      const t = definition.triggers.find((x) => x.id === id);
      if (t) return t.position ?? { x: 80, y: 80 };
      const n = definition.nodes.find((x) => x.id === id);
      return n?.position ?? { x: 80, y: 80 };
    },
    [definition],
  );

  const outputsOf = React.useCallback(
    (id: string): { kind: 'trigger' | 'node'; outputs: string[] } => {
      const t = definition.triggers.find((x) => x.id === id);
      if (t) return { kind: 'trigger', outputs: ['out'] };
      const n = definition.nodes.find((x) => x.id === id);
      if (n) {
        try {
          return { kind: 'node', outputs: outputPortsFor(nodeMeta(n.type), n) };
        } catch {
          return { kind: 'node', outputs: ['out'] };
        }
      }
      return { kind: 'node', outputs: ['out'] };
    },
    [definition],
  );

  const toWorld = React.useCallback(
    (clientX: number, clientY: number) => {
      const rect = svgRef.current?.getBoundingClientRect();
      if (!rect) return { x: 0, y: 0 };
      return {
        x: (clientX - rect.left - view.x) / view.k,
        y: (clientY - rect.top - view.y) / view.k,
      };
    },
    [view],
  );

  // -- background: pan (drag) or box select (shift+drag) ---------------------
  const onBackgroundDown = (e: React.PointerEvent) => {
    if (readOnly) return;
    if (e.shiftKey) {
      boxRef.current = { sx: e.clientX, sy: e.clientY };
      const w = toWorld(e.clientX, e.clientY);
      setBox({ x0: w.x, y0: w.y, x1: w.x, y1: w.y });
      return;
    }
    panRef.current = { sx: e.clientX, sy: e.clientY, vx: view.x, vy: view.y };
    didInteract.current = true;
    select(null);
    editor.setPendingEdge(null);
  };

  const onBackgroundMove = (e: React.PointerEvent) => {
    if (panRef.current) {
      setView((v) => ({
        ...v,
        x: panRef.current!.vx + (e.clientX - panRef.current!.sx),
        y: panRef.current!.vy + (e.clientY - panRef.current!.sy),
      }));
    }
    if (boxRef.current) {
      const w = toWorld(e.clientX, e.clientY);
      setBox((b) => (b ? { ...b, x1: w.x, y1: w.y } : b));
    }
    setCursor(toWorld(e.clientX, e.clientY));
  };

  const onBackgroundUp = (e: React.PointerEvent) => {
    panRef.current = null;
    if (boxRef.current && box) {
      const x0 = Math.min(box.x0, box.x1);
      const x1 = Math.max(box.x0, box.x1);
      const y0 = Math.min(box.y0, box.y1);
      const y1 = Math.max(box.y0, box.y1);
      if (Math.abs(x1 - x0) > 8 / view.k || Math.abs(y1 - y0) > 8 / view.k) {
        const hits: string[] = [];
        for (const t of definition.triggers) {
          const p = t.position ?? { x: 80, y: 80 };
          if (p.x < x1 && p.x + NODE_W > x0 && p.y < y1 && p.y + TRIGGER_H > y0) hits.push(t.id);
        }
        for (const n of definition.nodes) {
          const p = n.position ?? { x: 80, y: 80 };
          if (p.x < x1 && p.x + NODE_W > x0 && p.y < y1 && p.y + NODE_H > y0) hits.push(n.id);
        }
        editor.boxSelect(hits);
      } else if (!e.shiftKey) {
        select(null);
      }
    }
    boxRef.current = null;
    setBox(null);
  };

  const onWheelNative = React.useCallback((e: WheelEvent) => {
    didInteract.current = true;
    if (readOnlyRef.current) return;
    e.preventDefault();
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect) return;
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const factor = e.deltaY < 0 ? 1.1 : 1 / 1.1;
    setView((v) => {
      const k = Math.min(2, Math.max(0.25, v.k * factor));
      const wx = (mx - v.x) / v.k;
      const wy = (my - v.y) / v.k;
      return { k, x: mx - wx * k, y: my - wy * k };
    });
  }, []);

  React.useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    el.addEventListener('wheel', onWheelNative, { passive: false });
    return () => el.removeEventListener('wheel', onWheelNative);
  }, [onWheelNative]);

  // -- node drag (single + multi) -------------------------------------------
  const onItemDown = (e: React.PointerEvent, id: string) => {
    if (readOnly) return;
    e.stopPropagation();
    const kind = kindOf(id);
    if (e.ctrlKey || e.metaKey) {
      editor.toggleSelect({ kind: kind === 'edge' ? 'node' : kind, id });
      return;
    }
    let ids = selectedIds.includes(id) ? selectedIds.filter((x) => kindOf(x) !== 'edge') : [id];
    if (ids.length === 0) ids = [id];
    if (!selectedIds.includes(id)) {
      select(kind === 'edge' ? { kind: 'node', id } : { kind, id });
    }
    (e.currentTarget as Element).setPointerCapture(e.pointerId);
    dragRef.current = { ids, startX: e.clientX, startY: e.clientY, before: editor.definition };
  };

  const onStickyDown = (e: React.PointerEvent, note: StickyNote) => {
    if (readOnly) return;
    if ((e.target as Element).closest?.('textarea')) {
      e.stopPropagation();
      return;
    }
    e.stopPropagation();
    (e.currentTarget as Element).setPointerCapture(e.pointerId);
    dragRef.current = {
      ids: [note.id],
      startX: e.clientX,
      startY: e.clientY,
      before: editor.definition,
      sticky: true,
    };
  };

  const onItemMove = (e: React.PointerEvent) => {
    const drag = dragRef.current;
    if (!drag) return;
    const dx = (e.clientX - drag.startX) / view.k;
    const dy = (e.clientY - drag.startY) / view.k;
    const snap = (v: number) => Math.round(v / 4) * 4;

    if (drag.sticky) {
      const id = drag.ids[0];
      const base = (drag.before.settings.sticky_notes ?? []).find((s) => s.id === id);
      if (!base) return;
      editor.dispatch({
        type: 'move-sticky',
        id,
        x: Math.max(0, snap(base.x + dx)),
        y: Math.max(0, snap(base.y + dy)),
      });
      return;
    }

    for (const id of drag.ids) {
      const base =
        drag.before.triggers.find((t) => t.id === id)?.position ??
        drag.before.nodes.find((n) => n.id === id)?.position ??
        posOf(id);
      editor.dispatch({
        type: 'move-item',
        id,
        x: Math.max(0, snap(base.x + dx)),
        y: Math.max(0, snap(base.y + dy)),
      });
    }
  };

  const onItemUp = () => {
    const drag = dragRef.current;
    dragRef.current = null;
    if (drag) editor.dispatch({ type: 'commit-move', before: drag.before });
  };

  // -- edge creation --------------------------------------------------------
  const onOutputDown = (e: React.PointerEvent, fromId: string, port: string) => {
    if (readOnly) return;
    e.stopPropagation();
    editor.setPendingEdge({ fromId, fromPort: port });
  };
  const onInputUp = (e: React.PointerEvent, toId: string) => {
    if (readOnly) return;
    e.stopPropagation();
    const pending = editor.pendingEdge;
    if (pending && pending.fromId !== toId) {
      editor.connect(pending.fromId, toId, pending.fromPort);
    }
    editor.setPendingEdge(null);
  };

  // -- sticky notes ---------------------------------------------------------
  const addSticky = () => {
    const world = {
      x: (size.w / 2 - view.x) / view.k - 110,
      y: (size.h / 2 - view.y) / view.k - 70,
    };
    const color = STICKY_COLORS[stickies.length % STICKY_COLORS.length];
    editor.addSticky(Math.max(0, Math.round(world.x)), Math.max(0, Math.round(world.y)), color, '');
  };

  // -- drop from palette ----------------------------------------------------
  const onDrop = (e: React.DragEvent) => {
    if (readOnly) return;
    e.preventDefault();
    const payload = e.dataTransfer.getData('application/x-openagent-node');
    if (!payload) return;
    try {
      const { kind, nodeType, connectFrom } = JSON.parse(payload) as {
        kind: string;
        nodeType: string;
        connectFrom?: { id: string; port: string };
      };
      const w = toWorld(e.clientX, e.clientY);
      const at = {
        x: Math.max(0, Math.round(w.x - NODE_W / 2)),
        y: Math.max(0, Math.round(w.y - NODE_H / 2)),
      };
      const id =
        kind === 'trigger'
          ? editor.addTrigger(nodeType as never, at)
          : editor.addNode(nodeType as never, at);
      if (connectFrom) editor.connect(connectFrom.id, id, connectFrom.port);
    } catch {
      // Invalid payload — ignore.
    }
  };

  // -- context menu ---------------------------------------------------------
  const onContextMenu = (e: React.MouseEvent) => {
    if (readOnly) return;
    e.preventDefault();
    const world = toWorld(e.clientX, e.clientY);
    const el = (e.target as Element).closest?.('[data-item-kind]') as Element | null;
    if (el) {
      const kind = el.getAttribute('data-item-kind') as MenuTarget['kind'];
      const id = el.getAttribute('data-item-id')!;
      if (kind === 'sticky') {
        setMenu({ x: e.clientX, y: e.clientY, world, target: { kind, id } });
        return;
      }
      const node = definition.nodes.find((n) => n.id === id);
      if (!selectedIds.includes(id)) select({ kind: kind as 'trigger' | 'node' | 'edge', id });
      setMenu({
        x: e.clientX,
        y: e.clientY,
        world,
        target: { kind, id, disabled: node?.disabled },
      });
    } else {
      setMenu({ x: e.clientX, y: e.clientY, world, target: { kind: 'background' } });
    }
  };

  const onMenuAction = (actionId: string, target: MenuTarget) => {
    if (readOnly) return;
    switch (actionId) {
      case 'add':
        onAddNode?.({
          world: menu?.world ?? { x: 360, y: 240 },
          screen: menu ? { x: menu.x, y: menu.y } : undefined,
        });
        break;
      case 'configure':
        if (target.id) select({ kind: target.kind as 'trigger' | 'node' | 'edge', id: target.id });
        break;
      case 'detail':
        if (target.id) {
          select({ kind: target.kind as 'trigger' | 'node', id: target.id });
          onOpenNodeDetail?.(target.id);
        }
        break;
      case 'duplicate':
        if (target.id) {
          editor.boxSelect([target.id]);
          editor.duplicateSelection();
        }
        break;
      case 'copy':
        if (target.id) {
          editor.boxSelect([target.id]);
          editor.copySelection();
        }
        break;
      case 'paste':
        editor.pasteClipboard();
        break;
      case 'disable':
        if (target.id && target.kind === 'node') {
          editor.dispatch({ type: 'update-node', id: target.id, patch: { disabled: true } });
        }
        break;
      case 'enable':
        if (target.id && target.kind === 'node') {
          editor.dispatch({ type: 'update-node', id: target.id, patch: { disabled: false } });
        }
        break;
      case 'note':
        if (target.id) select({ kind: target.kind as 'trigger' | 'node', id: target.id });
        break;
      case 'delete':
        if (target.kind === 'sticky' && target.id) editor.deleteSticky(target.id);
        else if (target.id) editor.dispatch({ type: 'delete-ids', ids: [target.id] });
        break;
      case 'tidy':
        editor.autoLayout();
        break;
      case 'fit':
        fitView();
        break;
      default:
        break;
    }
  };

  // -- keyboard -------------------------------------------------------------
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (readOnly) return;
    const el = document.activeElement;
    const typing = el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT');
    if (typing) return;
    const mod = e.ctrlKey || e.metaKey;
    if ((e.key === 'Delete' || e.key === 'Backspace') && (selection || selectedIds.length > 0)) {
      e.preventDefault();
      editor.dispatch({ type: 'delete-selection' });
    } else if (e.key === 'Escape') {
      editor.setPendingEdge(null);
      select(null);
      setMenu(null);
      setHelpOpen(false);
    } else if (mod && e.key.toLowerCase() === 'z' && !e.shiftKey) {
      e.preventDefault();
      editor.undo();
    } else if (mod && (e.key.toLowerCase() === 'y' || (e.key.toLowerCase() === 'z' && e.shiftKey))) {
      e.preventDefault();
      editor.redo();
    } else if (mod && e.key.toLowerCase() === 'c' && selectedIds.length > 0) {
      e.preventDefault();
      editor.copySelection();
    } else if (mod && e.key.toLowerCase() === 'v') {
      e.preventDefault();
      editor.pasteClipboard();
    } else if (mod && e.key.toLowerCase() === 'd' && selectedIds.length > 0) {
      e.preventDefault();
      editor.duplicateSelection();
    } else if ((e.key === 'd' || e.key === 'D') && !mod && !e.shiftKey && selectedIds.length > 0) {
      e.preventDefault();
      editor.toggleDisabledSelection();
    } else if ((e.key === 'f' || e.key === 'F') && !mod) {
      e.preventDefault();
      fitView();
    } else if (selection && selection.kind !== 'edge' && e.key.startsWith('Arrow')) {
      e.preventDefault();
      const step = e.shiftKey ? 16 : 4;
      const ids = selectedIds.length > 0 ? selectedIds.filter((x) => kindOf(x) !== 'edge') : [selection.id];
      const before = editor.definition;
      for (const id of ids) {
        const p = posOf(id);
        const dx = e.key === 'ArrowLeft' ? -step : e.key === 'ArrowRight' ? step : 0;
        const dy = e.key === 'ArrowUp' ? -step : e.key === 'ArrowDown' ? step : 0;
        editor.dispatch({ type: 'move-item', id, x: Math.max(0, p.x + dx), y: Math.max(0, p.y + dy) });
      }
      editor.dispatch({ type: 'commit-move', before });
    }
  };

  const fitView = () => {
    const items = [
      ...definition.triggers.map((t) => ({ p: t.position ?? { x: 80, y: 80 }, h: TRIGGER_H })),
      ...definition.nodes.map((n) => ({ p: n.position ?? { x: 80, y: 80 }, h: NODE_H })),
      ...stickies.map((s) => ({ p: { x: s.x, y: s.y }, h: s.height })),
    ];
    if (items.length === 0) {
      setView({ x: 40, y: 40, k: 1 });
      return;
    }
    const W = size.w;
    const H = size.h;
    const minX = Math.min(...items.map((i) => i.p.x)) - 60;
    const minY = Math.min(...items.map((i) => i.p.y)) - 60;
    const maxX = Math.max(...items.map((i) => i.p.x + NODE_W)) + 60;
    const maxY = Math.max(...items.map((i) => i.p.y + i.h)) + 60;
    const k = Math.min(1.5, Math.max(0.3, Math.min(W / (maxX - minX), H / (maxY - minY))));
    setView({ k, x: (W - (maxX - minX) * k) / 2 - minX * k, y: (H - (maxY - minY) * k) / 2 - minY * k });
  };

  React.useEffect(() => {
    if (didInteract.current || size.w < 100) return;
    if (definition.triggers.length + definition.nodes.length === 0) return;
    fitView();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [size.w, size.h, definition.triggers.length, definition.nodes.length]);

  const centerOn = (world: { x: number; y: number }) => {
    setView((v) => ({ ...v, x: size.w / 2 - world.x * v.k, y: size.h / 2 - world.y * v.k }));
  };

  const pendingFrom = editor.pendingEdge ? posOf(editor.pendingEdge.fromId) : null;
  const visibleWorld = { x: -view.x / view.k, y: -view.y / view.k, w: size.w / view.k, h: size.h / view.k };
  const isEmpty = definition.triggers.length + definition.nodes.length === 0;
  const isSelected = (id: string) => selectedIds.includes(id);

  const openCreatorAt = (e: React.MouseEvent, connectFrom?: { id: string; port: string }) => {
    const world = toWorld(e.clientX, e.clientY);
    onAddNode?.({
      world: connectFrom
        ? { x: Math.max(0, world.x + 40), y: Math.max(0, world.y) }
        : { x: Math.max(0, Math.round(world.x - NODE_W / 2)), y: Math.max(0, Math.round(world.y - NODE_H / 2)) },
      screen: { x: e.clientX, y: e.clientY },
      connectFrom,
    });
  };

  // The toolbar's "+" drops a step in the middle of what the user can see.
  const openCreatorAtCenter = () => {
    const rect = containerRef.current?.getBoundingClientRect();
    onAddNode?.({
      world: {
        x: Math.max(0, Math.round((size.w / 2 - view.x) / view.k - NODE_W / 2)),
        y: Math.max(0, Math.round((size.h / 2 - view.y) / view.k - NODE_H / 2)),
      },
      screen: rect
        ? { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 }
        : undefined,
    });
  };

  return (
    <div
      ref={containerRef}
      className="oa-canvas relative h-full min-h-[420px] overflow-hidden"
      onKeyDown={onKeyDown}
    >
      <svg
        ref={svgRef}
        role="application"
        aria-label="Workflow canvas. Drag nodes to move them. Drag from an output port to an input port to connect. Double-click empty space to add a node. Right-click for actions."
        tabIndex={0}
        className="h-full w-full touch-none select-none focus-visible:outline-none"
        onPointerDown={onBackgroundDown}
        onPointerMove={(e) => {
          onBackgroundMove(e);
          onItemMove(e);
        }}
        onPointerUp={(e) => {
          onBackgroundUp(e);
          onItemUp();
        }}
        onDoubleClick={(e) => {
          if (readOnly) return;
          if ((e.target as Element).closest?.('[data-item-kind]')) return;
          openCreatorAt(e);
        }}
        onContextMenu={onContextMenu}
        onDragOver={(e) => e.preventDefault()}
        onDrop={onDrop}
      >
        <defs>
          <pattern id="oa-grid" width="20" height="20" patternUnits="userSpaceOnUse">
            <circle cx="1.4" cy="1.4" r="1.05" fill="currentColor" fillOpacity={0.14} />
          </pattern>
          <linearGradient id="oa-trigger-strip" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="hsl(var(--node-trigger))" stopOpacity="0.22" />
            <stop offset="100%" stopColor="hsl(var(--node-trigger))" stopOpacity="0.02" />
          </linearGradient>
          <filter id="oa-node-shadow" x="-25%" y="-25%" width="150%" height="170%">
            <feDropShadow dx="0" dy="3" stdDeviation="5" floodColor="rgb(15 23 42)" floodOpacity="0.12" />
          </filter>
          <filter id="oa-node-shadow-selected" x="-30%" y="-30%" width="160%" height="180%">
            <feDropShadow dx="0" dy="4" stdDeviation="8" floodColor="hsl(347 79% 60%)" floodOpacity="0.28" />
          </filter>
          <marker id="oa-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 1 L 9 5 L 0 9 z" className="fill-muted-foreground/70" />
          </marker>
          <marker id="oa-arrow-sel" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 1 L 9 5 L 0 9 z" className="fill-primary" />
          </marker>
        </defs>
        <g transform={`translate(${view.x},${view.y}) scale(${view.k})`}>
          <rect x={-6000} y={-6000} width={12000} height={12000} fill="url(#oa-grid)" />

          {/* sticky notes (behind nodes) */}
          {stickies.map((s) => (
            <g
              key={s.id}
              transform={`translate(${s.x},${s.y})`}
              data-item-kind="sticky"
              data-item-id={s.id}
              onPointerDown={(e) => onStickyDown(e, s)}
              className={readOnly ? '' : 'cursor-grab active:cursor-grabbing'}
            >
              <rect
                width={s.width}
                height={s.height}
                rx={8}
                fill={s.color}
                stroke="currentColor"
                strokeWidth={1}
                className="stroke-foreground/15"
                filter="url(#oa-node-shadow)"
              />
              <text x={12} y={18} fontSize={9} fontWeight={800} letterSpacing={0.6} fill="currentColor" className="fill-foreground/50">
                NOTE
              </text>
              {!readOnly && (
                <g
                  transform={`translate(${s.width - 24},10)`}
                  onPointerDown={(e) => e.stopPropagation()}
                  onClick={() => editor.deleteSticky(s.id)}
                  className="cursor-pointer"
                  role="button"
                  aria-label="Delete note"
                >
                  <rect width={16} height={16} rx={4} fill="currentColor" opacity={0.08} />
                  <X x={3} y={3} width={10} height={10} className="text-muted-foreground" />
                </g>
              )}
              <foreignObject x={10} y={24} width={s.width - 20} height={s.height - 32}>
                <textarea
                  value={noteDraft[s.id] ?? s.text}
                  readOnly={readOnly}
                  onChange={(e) => setNoteDraft((p) => ({ ...p, [s.id]: e.target.value }))}
                  onBlur={(e) => editor.updateSticky(s.id, { text: e.target.value })}
                  onKeyDown={(e) => e.stopPropagation()}
                  onPointerDown={(e) => e.stopPropagation()}
                  spellCheck={false}
                  aria-label="Sticky note text"
                  className="h-full w-full resize-none border-none bg-transparent p-0 text-xs leading-snug outline-none"
                  style={{ color: '#334155' }}
                />
              </foreignObject>
            </g>
          ))}

          {/* box selection */}
          {box && (
            <rect
              x={Math.min(box.x0, box.x1)}
              y={Math.min(box.y0, box.y1)}
              width={Math.abs(box.x1 - box.x0)}
              height={Math.abs(box.y1 - box.y0)}
              fill="none"
              strokeWidth={1.5 / view.k}
              strokeDasharray={`${6 / view.k} ${4 / view.k}`}
              className="stroke-primary fill-primary/5"
            />
          )}

          {/* edges */}
          {definition.edges.map((edge) => {
            const fromPos = posOf(edge.from);
            const { kind, outputs } = outputsOf(edge.from);
            const requested = edge.from_port ?? edge.label;
            const port = requested && outputs.includes(requested) ? requested : outputs[0] ?? 'out';
            const targetPort = edge.to_port ?? 'in';
            const a = portPosition(kind, fromPos, port, outputs);
            const b = portPosition('node', posOf(edge.to), targetPort, [targetPort]);
            const selected = isSelected(edge.id);
            return (
              <g key={edge.id} data-item-kind="edge" data-item-id={edge.id}>
                <path
                  d={edgePath(a, b)}
                  fill="none"
                  strokeWidth={selected ? 2.6 : 1.8}
                  strokeLinecap="round"
                  markerEnd={selected ? 'url(#oa-arrow-sel)' : 'url(#oa-arrow)'}
                  className={selected ? 'stroke-primary' : 'stroke-muted-foreground/55'}
                />
                <path
                  d={edgePath(a, b)}
                  fill="none"
                  stroke="transparent"
                  strokeWidth={18}
                  className="cursor-pointer"
                  onPointerDown={(e) => {
                    if (readOnly) return;
                    e.stopPropagation();
                    if (e.ctrlKey || e.metaKey) editor.toggleSelect({ kind: 'edge', id: edge.id });
                    else select({ kind: 'edge', id: edge.id });
                  }}
                >
                  <title>{`Edge ${edge.from} to ${edge.to}${edge.label ? ` (${edge.label})` : ''}`}</title>
                </path>
                {edge.label && (
                  <g transform={`translate(${(a.x + b.x) / 2},${(a.y + b.y) / 2})`}>
                    <rect x={-4 - edge.label.length * 3.1} y={-9} width={edge.label.length * 6.2 + 8} height={16} rx={8} className="fill-card stroke-border" strokeWidth={1} />
                    <text textAnchor="middle" y={3} fontSize={10} fontWeight={600} className="fill-muted-foreground">
                      {edge.label}
                    </text>
                  </g>
                )}
              </g>
            );
          })}

          {/* pending edge */}
          {editor.pendingEdge && pendingFrom && cursor && (
            <path
              d={edgePath(
                portPosition(
                  outputsOf(editor.pendingEdge.fromId).kind,
                  pendingFrom,
                  editor.pendingEdge.fromPort,
                  outputsOf(editor.pendingEdge.fromId).outputs,
                ),
                cursor,
              )}
              fill="none"
              strokeWidth={2}
              className="oa-edge-dash stroke-primary"
            />
          )}

          {/* triggers */}
          {definition.triggers.map((t) => {
            const meta = triggerMeta(t.type);
            const Icon = meta.icon;
            const p = t.position ?? { x: 80, y: 80 };
            const selected = isSelected(t.id);
            const hasError = errorIds.has(t.id);
            const status = runStatus?.[t.id];
            const itemCountValue = runItems?.[t.id];
            const errorMessage = runErrors?.[t.id];
            const isActive = activeNodeIds?.includes(t.id) ?? false;
            const out = portPosition('trigger', p, 'out', ['out']);
            const accent = categoryColor('Triggers');
            return (
              <g
                key={t.id}
                transform={`translate(${p.x},${p.y})`}
                data-item-kind="trigger"
                data-item-id={t.id}
                data-run-status={status}
                tabIndex={readOnly ? undefined : 0}
                role={readOnly ? undefined : 'button'}
                aria-label={readOnly ? undefined : `Trigger ${t.name}. Press Enter to select.`}
                onKeyDown={(e) => {
                  if (!readOnly && (e.key === 'Enter' || e.key === ' ')) {
                    e.preventDefault();
                    select({ kind: 'trigger', id: t.id });
                  }
                }}
                onPointerDown={(e) => onItemDown(e, t.id)}
                onDoubleClick={() => onOpenNodeDetail?.(t.id)}
                className={cn('group', readOnly ? '' : 'cursor-grab active:cursor-grabbing')}
              >
                <rect
                  width={NODE_W}
                  height={TRIGGER_H}
                  rx={12}
                  filter={selected ? 'url(#oa-node-shadow-selected)' : 'url(#oa-node-shadow)'}
                  className={cn(
                    'fill-card',
                    selected ? 'stroke-primary' : hasError ? 'stroke-destructive' : status === 'succeeded' ? 'stroke-emerald-500/60' : status === 'failed' ? 'stroke-destructive/70' : 'stroke-border',
                  )}
                  strokeWidth={selected ? 2.4 : 1.2}
                />
                <rect x={10} y={12} width={34} height={34} rx={10} style={{ fill: accent, fillOpacity: 0.14 }} />
                <Icon x={19} y={21} width={16} height={16} style={{ color: accent }} />
                <text x={54} y={26} fontSize={9} fontWeight={800} letterSpacing={0.8} className="fill-muted-foreground">
                  TRIGGER
                </text>
                <text x={54} y={44} fontSize={13.5} fontWeight={650} className="fill-foreground">
                  {clip(t.name, 24)}
                </text>
                <text x={54} y={61} fontSize={10.5} className="fill-muted-foreground">
                  {clip(meta.label, 26)}
                </text>
                {hasError && (
                  <g transform={`translate(${NODE_W - 16},14)`}>
                    <circle r={8} className="fill-destructive" />
                    <text textAnchor="middle" dy={3.5} fontSize={11} fontWeight={800} fill="white">!</text>
                  </g>
                )}
                {errorMessage !== undefined && status !== 'succeeded' && (
                  <text x={NODE_W - 12} y={61} textAnchor="end" fontSize={9.5} className="fill-destructive">
                    <title>{errorMessage || 'Trigger failed'}</title>
                    failed
                  </text>
                )}
                {itemCountValue !== undefined && (
                  <text
                    x={12}
                    y={TRIGGER_H - 5}
                    fontSize={9.5}
                    fontWeight={600}
                    className="fill-muted-foreground tabular-nums"
                  >
                    {itemCountValue} item{itemCountValue === 1 ? '' : 's'}
                  </text>
                )}
                {isActive && (
                  <rect
                    width={NODE_W}
                    height={TRIGGER_H}
                    rx={12}
                    fill="none"
                    strokeWidth={2}
                    strokeDasharray="7 5"
                    className="oa-edge-dash stroke-primary"
                    aria-label="Running this step"
                  />
                )}
                <RunGlyph status={status} x={NODE_W - 18} y={NODE_H - 16} />
                {!readOnly && (
                  <g
                    transform={`translate(${out.x - p.x},${out.y - p.y})`}
                    onPointerDown={(e) => onOutputDown(e, t.id, 'out')}
                    className="cursor-crosshair"
                  >
                    <circle r={11} fill="transparent" />
                    <circle r={5.5} className="fill-background stroke-primary" strokeWidth={2}>
                      <title>Drag to connect</title>
                    </circle>
                  </g>
                )}
                {!readOnly && (
                  <g
                    transform={`translate(${NODE_W + 24},${TRIGGER_H / 2})`}
                    onPointerDown={(e) => e.stopPropagation()}
                    onClick={(e) => {
                      e.stopPropagation();
                      onAddNode?.({
                        world: { x: p.x + NODE_W + 84, y: p.y },
                        screen: { x: e.clientX, y: e.clientY },
                        connectFrom: { id: t.id, port: 'out' },
                      });
                    }}
                    className="cursor-pointer opacity-0 transition-opacity group-hover:opacity-100"
                    role="button"
                    aria-label={`Add a node after ${t.name}`}
                  >
                    <circle r={11} className="fill-card stroke-primary" strokeWidth={1.6} />
                    <path d="M -4 0 H 4 M 0 -4 V 4" className="stroke-primary" strokeWidth={1.8} strokeLinecap="round" />
                  </g>
                )}
              </g>
            );
          })}

          {/* nodes */}
          {definition.nodes.map((n) => {
            const meta = nodeMeta(n.type);
            const Icon = meta.icon;
            const p = n.position ?? { x: 80, y: 80 };
            const selected = isSelected(n.id);
            const hasError = errorIds.has(n.id);
            const status = runStatus?.[n.id];
            const itemCountValue = runItems?.[n.id];
            const errorMessage = runErrors?.[n.id];
            const isActive = activeNodeIds?.includes(n.id) ?? false;
            const inp = portPosition('node', p, 'in', ['in']);
            const ports = outputPortsFor(meta, n);
            const accent = categoryColor(meta.category);
            const showPlus = !readOnly && ports.length <= 1;
            return (
              <g
                key={n.id}
                transform={`translate(${p.x},${p.y})`}
                data-item-kind="node"
                data-item-id={n.id}
                data-run-status={status}
                tabIndex={readOnly ? undefined : 0}
                role={readOnly ? undefined : 'button'}
                aria-label={readOnly ? undefined : `Node ${n.name}, type ${meta.label}${n.disabled ? ', disabled' : ''}. Press Enter to select.`}
                onKeyDown={(e) => {
                  if (!readOnly && (e.key === 'Enter' || e.key === ' ')) {
                    e.preventDefault();
                    select({ kind: 'node', id: n.id });
                  }
                }}
                onPointerDown={(e) => onItemDown(e, n.id)}
                onDoubleClick={() => onOpenNodeDetail?.(n.id)}
                className={cn('group', readOnly ? '' : 'cursor-grab active:cursor-grabbing', n.disabled && 'opacity-50')}
              >
                <rect
                  width={NODE_W}
                  height={NODE_H}
                  rx={12}
                  filter={selected ? 'url(#oa-node-shadow-selected)' : 'url(#oa-node-shadow)'}
                  className={cn(
                    'fill-card',
                    selected ? 'stroke-primary' : hasError ? 'stroke-destructive' : status === 'succeeded' ? 'stroke-emerald-500/60' : status === 'failed' ? 'stroke-destructive/70' : status === 'waiting' ? 'stroke-amber-500/70' : 'stroke-border',
                  )}
                  strokeWidth={selected ? 2.4 : 1.2}
                  strokeDasharray={n.disabled ? '5 3' : undefined}
                />
                <rect x={12} y={25} width={34} height={34} rx={10} style={{ fill: accent, fillOpacity: 0.14 }} />
                <Icon x={21} y={34} width={16} height={16} style={{ color: accent }} />
                <text x={56} y={28} fontSize={9} fontWeight={800} letterSpacing={0.7} className="fill-muted-foreground">
                  {clip(meta.label.toUpperCase(), 22)}
                </text>
                <text x={56} y={46} fontSize={13.5} fontWeight={650} className="fill-foreground">
                  {clip(n.name, 24)}
                </text>
                <text x={56} y={62} fontSize={10.5} className="fill-muted-foreground">
                  {clip(nodeSubtitle(meta.label, n), 28)}
                </text>
                {n.disabled && (
                  <text x={56} y={74} fontSize={9} fontWeight={700} letterSpacing={0.4} className="fill-muted-foreground">
                    DISABLED
                  </text>
                )}
                {!n.disabled && n.approval_required && (
                  <text x={56} y={74} fontSize={9} fontWeight={700} letterSpacing={0.2} className="fill-amber-600 dark:fill-amber-400">
                    requires approval
                  </text>
                )}
                {n.notes && (
                  <g transform={`translate(${NODE_W - 42},${NODE_H - 22})`} aria-label="Has a note">
                    <StickyNoteIcon width={13} height={13} className="text-muted-foreground/70" />
                  </g>
                )}
                {errorMessage !== undefined && status !== 'succeeded' && (
                  <text
                    x={NODE_W - 12}
                    y={28}
                    textAnchor="end"
                    fontSize={9.5}
                    className="fill-destructive"
                    aria-label={`Run error: ${errorMessage}`}
                  >
                    <title>{errorMessage || 'Node failed'}</title>
                    failed
                  </text>
                )}
                {itemCountValue !== undefined && (
                  <text
                    x={12}
                    y={NODE_H - 5}
                    fontSize={9.5}
                    fontWeight={600}
                    className="fill-muted-foreground tabular-nums"
                  >
                    <title>{`${itemCountValue} output item${itemCountValue === 1 ? '' : 's'}`}</title>
                    {itemCountValue} item{itemCountValue === 1 ? '' : 's'}
                  </text>
                )}
                {hasError && (
                  <g transform={`translate(${NODE_W - 16},14)`}>
                    <circle r={8} className="fill-destructive" />
                    <text textAnchor="middle" dy={3.5} fontSize={11} fontWeight={800} fill="white">!</text>
                  </g>
                )}
                {isActive && (
                  <rect
                    width={NODE_W}
                    height={NODE_H}
                    rx={12}
                    fill="none"
                    strokeWidth={2}
                    strokeDasharray="7 5"
                    className="oa-edge-dash stroke-primary"
                    aria-label="Running this step"
                  />
                )}
                {!hasError && <RunGlyph status={status} x={NODE_W - 18} y={NODE_H - 16} />}
                {meta.hasInput && (
                  <g
                    transform={`translate(${inp.x - p.x},${inp.y - p.y})`}
                    onPointerDown={(e) => e.stopPropagation()}
                    onPointerUp={(e) => onInputUp(e, n.id)}
                    className={readOnly ? '' : 'cursor-crosshair'}
                  >
                    <circle r={11} fill="transparent" />
                    <circle r={5.5} className="fill-background stroke-muted-foreground" strokeWidth={2}>
                      <title>Drop to connect</title>
                    </circle>
                  </g>
                )}
                {!readOnly &&
                  ports.map((port) => {
                    const o = portPosition('node', { x: 0, y: 0 }, port, ports);
                    return (
                      <g
                        key={port}
                        transform={`translate(${o.x},${o.y})`}
                        onPointerDown={(e) => onOutputDown(e, n.id, port)}
                        className="cursor-crosshair"
                      >
                        <circle r={11} fill="transparent" />
                        <circle r={5.5} className="fill-background stroke-primary" strokeWidth={2}>
                          <title>{`Drag from ${port} output`}</title>
                        </circle>
                        {ports.length > 1 && (
                          <text x={10} y={3.5} fontSize={9.5} fontWeight={700} className="fill-muted-foreground">
                            {port}
                          </text>
                        )}
                      </g>
                    );
                  })}
                {showPlus && (
                  <g
                    transform={`translate(${NODE_W + 24},${NODE_H / 2})`}
                    onPointerDown={(e) => e.stopPropagation()}
                    onClick={(e) => {
                      e.stopPropagation();
                      onAddNode?.({
                        world: { x: p.x + NODE_W + 84, y: p.y },
                        screen: { x: e.clientX, y: e.clientY },
                        connectFrom: { id: n.id, port: ports[0] ?? 'out' },
                      });
                    }}
                    className="cursor-pointer opacity-0 transition-opacity group-hover:opacity-100 focus:opacity-100"
                    role="button"
                    aria-label={`Add a node after ${n.name}`}
                  >
                    <circle r={11} className="fill-card stroke-primary" strokeWidth={1.6} />
                    <path d="M -4 0 H 4 M 0 -4 V 4" className="stroke-primary" strokeWidth={1.8} strokeLinecap="round" />
                  </g>
                )}
              </g>
            );
          })}
        </g>
      </svg>

      {/* empty state — n8n's "Add first step… or Build with AI" */}
      {isEmpty && !readOnly && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center p-6">
          <div className="pointer-events-auto flex flex-col items-center gap-4">
            <div className="flex items-center gap-4 sm:gap-6">
              <button
                type="button"
                onClick={(e) =>
                  onAddNode?.({
                    world: { x: 320, y: 240 },
                    screen: { x: e.clientX, y: e.clientY },
                  })
                }
                className="group flex flex-col items-center gap-2"
              >
                <span className="flex h-[88px] w-[88px] items-center justify-center rounded-2xl border border-dashed border-border bg-card/70 text-muted-foreground shadow-sm backdrop-blur transition-colors group-hover:border-primary/60 group-hover:text-primary">
                  <Plus className="h-9 w-9" strokeWidth={1.5} aria-hidden />
                </span>
                <span className="text-xs font-medium">Add first step…</span>
              </button>

              <span className="text-xs text-muted-foreground">or</span>

              <button
                type="button"
                onClick={() => onBuildWithAi?.()}
                disabled={!onBuildWithAi}
                className="group flex flex-col items-center gap-2 disabled:pointer-events-none disabled:opacity-50"
              >
                <span className="flex h-[88px] w-[88px] items-center justify-center rounded-2xl border border-dashed border-border bg-card/70 text-muted-foreground shadow-sm backdrop-blur transition-colors group-hover:border-primary/60 group-hover:text-primary">
                  <Sparkles className="h-9 w-9" strokeWidth={1.5} aria-hidden />
                </span>
                <span className="text-xs font-medium">Build with AI</span>
              </button>
            </div>
            <p className="max-w-[300px] text-center text-[11px] text-muted-foreground">
              Start with a trigger, or describe the automation and let the assistant
              draft it.
            </p>
          </div>
        </div>
      )}

      {/* right-side toolbar — n8n's add / search / variables / AI rail */}
      <div
        role="toolbar"
        aria-label="Canvas tools"
        aria-orientation="vertical"
        className="absolute right-3 top-1/2 z-20 flex -translate-y-1/2 flex-col items-center gap-0.5 rounded-full border border-border/70 bg-card/90 p-1 shadow-sm backdrop-blur"
      >
        <button
          type="button"
          onClick={openCreatorAtCenter}
          disabled={readOnly}
          aria-label="Add step"
          title="Add step"
          className="oa-icon-button rounded-full"
        >
          <Plus className="h-4 w-4" />
        </button>
        <button
          type="button"
          onClick={() => onSearchNodes?.()}
          disabled={!onSearchNodes}
          aria-label="Search nodes"
          title="Search nodes"
          className="oa-icon-button hidden rounded-full md:inline-flex"
        >
          <Search className="h-4 w-4" />
        </button>
        <button
          type="button"
          onClick={() => onOpenVariables?.()}
          disabled={!onOpenVariables}
          aria-label="Workflow variables"
          title="Workflow variables"
          className="oa-icon-button hidden rounded-full xl:inline-flex"
        >
          <Braces className="h-4 w-4" />
        </button>
        <span className="my-0.5 h-px w-5 bg-border" aria-hidden />
        <button
          type="button"
          onClick={() => onBuildWithAi?.()}
          disabled={!onBuildWithAi}
          aria-label="AI assistant"
          title="AI assistant"
          className="oa-icon-button rounded-full"
        >
          <Sparkles className="h-4 w-4 text-primary" />
        </button>
      </div>

      {/* controls */}
      <div className="absolute bottom-3 left-3 flex items-center gap-0.5 rounded-full border border-border/70 bg-card/90 p-1 shadow-sm backdrop-blur">
        <button
          onClick={() => {
            didInteract.current = true;
            setView((v) => ({ ...v, k: Math.min(2, v.k * 1.2) }));
          }}
          aria-label="Zoom in"
          className="rounded-full p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
        >
          <Plus className="h-4 w-4" />
        </button>
        <span className="min-w-[42px] px-1 text-center text-[11px] font-medium tabular-nums text-muted-foreground" aria-live="polite">
          {Math.round(view.k * 100)}%
        </span>
        <button
          onClick={() => {
            didInteract.current = true;
            setView((v) => ({ ...v, k: Math.max(0.25, v.k / 1.2) }));
          }}
          aria-label="Zoom out"
          className="rounded-full p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
        >
          <Minus className="h-4 w-4" />
        </button>
        <span className="mx-0.5 h-4 w-px bg-border" aria-hidden />
        <button
          onClick={() => {
            didInteract.current = true;
            fitView();
          }}
          aria-label="Fit to view (F)"
          className="rounded-full p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
        >
          <Maximize className="h-4 w-4" />
        </button>
        {!readOnly && (
          <>
            <span className="mx-0.5 h-4 w-px bg-border" aria-hidden />
            <button
              onClick={addSticky}
              aria-label="Add sticky note"
              title="Add a sticky note"
              className="rounded-full p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            >
              <StickyNoteIcon className="h-4 w-4" />
            </button>
            <button
              onClick={(e) =>
                onAddNode?.({
                  world: { x: 320, y: 240 },
                  screen: { x: e.clientX, y: e.clientY },
                })
              }
              aria-label="Add node"
              title="Add node"
              className="rounded-full p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            >
              <Plus className="h-4 w-4" />
            </button>
          </>
        )}
      </div>

      {/* help / shortcuts */}
      {/* run HUD — the synchronous engine reports per-node statuses only when
          the run returns, so this shows in-flight state without faking it. */}
      {!running && lastRun && (
        <button
          type="button"
          onClick={onOpenRunPanel}
          disabled={!onOpenRunPanel}
          className={cn(
            'absolute left-3 top-3 z-20 flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] shadow-sm backdrop-blur transition-colors',
            lastRun.failed > 0
              ? 'border-destructive/40 bg-destructive/10 text-destructive hover:bg-destructive/15'
              : 'border-emerald-500/40 bg-emerald-500/10 text-emerald-700 hover:bg-emerald-500/15 dark:text-emerald-400',
          )}
          title="Open the test run results"
          aria-label={
            `Last run ${lastRun.status}: ${lastRun.succeeded} succeeded, ${lastRun.failed} failed` +
            (onOpenRunPanel ? ' — open results' : '')
          }
        >
          {lastRun.failed > 0 ? (
            <XCircle className="h-3.5 w-3.5" aria-hidden />
          ) : (
            <CheckCircle2 className="h-3.5 w-3.5" aria-hidden />
          )}
          <span className="font-medium capitalize">{lastRun.status}</span>
          <span className="tabular-nums opacity-80">
            {lastRun.succeeded} ok
            {lastRun.failed > 0 ? ` · ${lastRun.failed} failed` : ''}
            {lastRun.waiting > 0 ? ` · ${lastRun.waiting} waiting` : ''}
            {lastRun.skipped > 0 ? ` · ${lastRun.skipped} skipped` : ''}
          </span>
        </button>
      )}
      {running && (
        <div
          className="absolute left-3 top-3 z-20 flex items-center gap-1.5 rounded-full border border-border/70 bg-card/95 px-2.5 py-1 text-[11px] text-muted-foreground shadow-sm backdrop-blur"
          role="status"
          aria-live="polite"
        >
          <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" aria-hidden />
          Running…
        </div>
      )}

      <div className="absolute right-3 top-3 flex items-center gap-1.5">
        <button
          type="button"
          onClick={() => setHelpOpen((v) => !v)}
          aria-label="Canvas help and shortcuts"
          aria-expanded={helpOpen}
          className="flex h-8 w-8 items-center justify-center rounded-full border border-border/70 bg-card/90 text-muted-foreground shadow-sm backdrop-blur transition-colors hover:text-foreground"
        >
          <HelpCircle className="h-4 w-4" />
        </button>
      </div>
      {helpOpen && (
        <div className="absolute right-3 top-14 z-20 w-72 rounded-xl border border-border bg-popover p-3 text-xs shadow-lg">
          <p className="mb-2 flex items-center gap-1.5 font-semibold">
            <MousePointer2 className="h-3.5 w-3.5 text-primary" aria-hidden /> Canvas shortcuts
          </p>
          <dl className="space-y-1.5 text-muted-foreground">
            {[
              ['Drag node', 'Move it'],
              ['Drag port → port', 'Connect nodes'],
              ['Double-click canvas', 'Add a node'],
              ['Double-click node', 'Open input/output data'],
              ['Shift + drag', 'Box select'],
              ['Ctrl/⌘ + C / V / D', 'Copy, paste, duplicate'],
              ['Ctrl/⌘ + Z / Y', 'Undo / redo'],
              ['D', 'Disable / enable selected'],
              ['Del', 'Delete selection'],
              ['Right-click → Tidy up', 'Re-flow the graph'],
              ['F', 'Fit to view'],
              ['Arrows', 'Nudge selection'],
            ].map(([k, v]) => (
              <div key={k} className="flex items-center justify-between gap-3">
                <dt className="font-medium text-foreground">{k}</dt>
                <dd>{v}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}

      {/* minimap */}
      <div className="absolute bottom-3 right-3 hidden sm:block">
        <WorkflowMinimap definition={definition} visible={visibleWorld} onNavigate={centerOn} />
      </div>

      {editor.pendingEdge && (
        <div className="absolute left-1/2 top-3 -translate-x-1/2 rounded-full border border-primary/40 bg-card/95 px-3 py-1.5 text-xs shadow-sm backdrop-blur" role="status">
          Connecting from <span className="oa-code">{editor.pendingEdge.fromId}</span> — drop on an input port, Esc to cancel
        </div>
      )}

      {menu && (
        <CanvasContextMenu
          x={menu.x}
          y={menu.y}
          target={menu.target}
          hasClipboard={editor.hasClipboard}
          onAction={onMenuAction}
          onClose={() => setMenu(null)}
        />
      )}
    </div>
  );
}
