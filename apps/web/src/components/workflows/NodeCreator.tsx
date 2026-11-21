'use client';

import * as React from 'react';
import {
  TRIGGER_CATALOG,
  definitionsByCategory,
  type NodeMeta,
  type TriggerMeta,
} from '@/features/workflows/node-catalog';
import type { WorkflowEditor } from '@/features/workflows/use-workflow-editor';
import { Search, Plus, X, GripVertical, CornerDownLeft } from 'lucide-react';
import { cn } from '@/lib/utils';

// n8n-style node creator: a floating command panel that opens from a "+"
// affordance on the canvas. Search, browse by category, click to add, or drag
// straight onto the canvas.

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

function tint(category: string): string {
  return `hsl(var(--node-${CATEGORY_VAR[category] ?? 'utility'}))`;
}

export interface NodeCreatorRequest {
  /** Top-left world position where the node should land. */
  world: { x: number; y: number };
  /** Client coordinates used to anchor the panel. */
  screen?: { x: number; y: number };
  /** When opened from an output "+", the node is connected afterwards. */
  connectFrom?: { id: string; port: string };
}

export function NodeCreator({
  open,
  onClose,
  editor,
  disabled = false,
  request,
}: {
  open: boolean;
  onClose: () => void;
  editor: WorkflowEditor;
  disabled?: boolean;
  request: NodeCreatorRequest | null;
}) {
  const [q, setQ] = React.useState('');
  const [category, setCategory] = React.useState('all');
  const [active, setActive] = React.useState(0);
  const inputRef = React.useRef<HTMLInputElement>(null);
  const panelRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (!open) return;
    setQ('');
    setCategory('all');
    setActive(0);
    const t = window.setTimeout(() => inputRef.current?.focus(), 20);
    return () => window.clearTimeout(t);
  }, [open]);

  React.useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  // Close when clicking outside the panel.
  React.useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      const el = panelRef.current;
      if (el && !el.contains(e.target as Node)) onClose();
    };
    // Defer so the opening click does not immediately dismiss it.
    const t = window.setTimeout(() => document.addEventListener('pointerdown', onDown), 0);
    return () => {
      window.clearTimeout(t);
      document.removeEventListener('pointerdown', onDown);
    };
  }, [open, onClose]);

  const query = q.trim().toLowerCase();
  const match = (label: string, description: string, type: string) =>
    !query ||
    label.toLowerCase().includes(query) ||
    description.toLowerCase().includes(query) ||
    type.toLowerCase().includes(query);

  const triggers = TRIGGER_CATALOG.filter((t) => match(t.label, t.description, t.type));
  const groups = definitionsByCategory()
    .map((g) => ({
      ...g,
      items: g.items.filter((n) => match(n.label, n.description, String(n.type))),
    }))
    .filter((g) => g.items.length > 0 && (category === 'all' || g.category === category));

  const flat: (TriggerMeta | NodeMeta)[] = [
    ...(category === 'all' || category === 'Triggers' ? triggers : []),
    ...groups.flatMap((g) => g.items),
  ];

  React.useEffect(() => {
    setActive((i) => (flat.length === 0 ? 0 : Math.min(i, flat.length - 1)));
  }, [flat.length]);

  if (!open) return null;

  const add = (item: TriggerMeta | NodeMeta) => {
    if (disabled) return;
    const isTrigger = TRIGGER_CATALOG.includes(item as TriggerMeta);
    const at = request?.world ?? { x: 320, y: 240 };
    const id = isTrigger
      ? editor.addTrigger((item as TriggerMeta).type, at)
      : editor.addNode((item as NodeMeta).type as never, at);
    if (request?.connectFrom) {
      editor.connect(request.connectFrom.id, id, request.connectFrom.port);
    }
    onClose();
  };

  const onDragStart = (e: React.DragEvent, item: TriggerMeta | NodeMeta) => {
    const isTrigger = TRIGGER_CATALOG.includes(item as TriggerMeta);
    e.dataTransfer.setData(
      'application/x-openagent-node',
      JSON.stringify({
        kind: isTrigger ? 'trigger' : 'node',
        nodeType: String(item.type),
        connectFrom: request?.connectFrom,
      }),
    );
    e.dataTransfer.effectAllowed = 'copy';
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActive((i) => Math.min(flat.length - 1, i + 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive((i) => Math.max(0, i - 1));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const item = flat[active];
      if (item) add(item);
    }
  };

  // Anchor near the clicked "+" while keeping the panel on screen.
  const width = 460;
  const height = 540;
  const anchor = request?.screen;
  const left = anchor ? Math.min(Math.max(12, anchor.x - 40), window.innerWidth - width - 12) : undefined;
  const top = anchor ? Math.min(Math.max(12, anchor.y - 24), window.innerHeight - height - 12) : undefined;

  return (
    <div className="pointer-events-none fixed inset-0 z-[70]" role="presentation">
      <div className="pointer-events-none absolute inset-0 bg-black/5" aria-hidden />
      <div
        ref={panelRef}
        role="dialog"
        aria-label="Add a node"
        style={anchor ? { left, top, width, maxHeight: height } : { left: '50%', top: 72, transform: 'translateX(-50%)', width, maxHeight: height }}
        className="animate-fade-in pointer-events-auto absolute flex flex-col overflow-hidden rounded-xl border border-border bg-popover shadow-2xl"
      >
        <div className="flex items-center gap-2 border-b border-border px-3 py-2.5">
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <Plus className="h-4 w-4" aria-hidden />
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-[13px] font-semibold leading-tight">Add a node</p>
            <p className="text-[11px] text-muted-foreground">
              {request?.connectFrom ? 'It will be connected to the previous step' : 'Search or browse the library'}
            </p>
          </div>
          <button onClick={onClose} aria-label="Close node creator" className="oa-icon-button">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-2 border-b border-border px-3 py-2.5">
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden />
            <input
              ref={inputRef}
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder="Search nodes, triggers, actions…"
              aria-label="Search nodes"
              className="h-9 w-full rounded-lg border border-transparent bg-muted/60 pl-8 pr-8 text-sm outline-none transition-colors focus:border-primary focus:bg-background"
            />
            <kbd className="oa-kbd absolute right-2 top-1/2 -translate-y-1/2">esc</kbd>
          </div>
          <div className="oa-scroll flex gap-1 overflow-x-auto pb-0.5">
            <CategoryChip label="All" active={category === 'all'} onClick={() => setCategory('all')} />
            <CategoryChip
              label="Triggers"
              active={category === 'Triggers'}
              onClick={() => setCategory('Triggers')}
              color={tint('Triggers')}
            />
            {definitionsByCategory().map((g) => (
              <CategoryChip
                key={g.category}
                label={g.category}
                active={category === g.category}
                onClick={() => setCategory(g.category)}
                color={tint(g.category)}
              />
            ))}
          </div>
        </div>

        <div className="oa-scroll min-h-0 flex-1 overflow-y-auto p-2">
          {flat.length === 0 && (
            <p className="px-2 py-8 text-center text-sm text-muted-foreground">
              Nothing matches “{q}”.
            </p>
          )}

          {(category === 'all' || category === 'Triggers') && triggers.length > 0 && (
            <Section title="Triggers" hint="Start the workflow">
              {triggers.map((t) => (
                <CreatorRow
                  key={`t:${t.type}`}
                  item={t}
                  category="Triggers"
                  active={flat.indexOf(t) === active}
                  disabled={disabled}
                  onHover={() => setActive(flat.indexOf(t))}
                  onAdd={() => add(t)}
                  onDragStart={(e) => onDragStart(e, t)}
                  onDragEnd={onClose}
                />
              ))}
            </Section>
          )}

          {groups.map((g) => (
            <Section key={g.category} title={g.category} dot={tint(g.category)}>
              {g.items.map((n) => (
                <CreatorRow
                  key={String(n.type)}
                  item={n}
                  category={g.category}
                  active={flat.indexOf(n) === active}
                  disabled={disabled}
                  onHover={() => setActive(flat.indexOf(n))}
                  onAdd={() => add(n)}
                  onDragStart={(e) => onDragStart(e, n)}
                  onDragEnd={onClose}
                />
              ))}
            </Section>
          ))}
        </div>

        <div className="flex items-center gap-3 border-t border-border px-3 py-2 text-[11px] text-muted-foreground">
          <span className="flex items-center gap-1">
            <CornerDownLeft className="h-3 w-3" aria-hidden /> add
          </span>
          <span>↑ ↓ to navigate</span>
          <span className="ml-auto flex items-center gap-1">
            <GripVertical className="h-3 w-3" aria-hidden /> drag to place
          </span>
        </div>
      </div>
    </div>
  );
}

function CategoryChip({
  label,
  active,
  onClick,
  color,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
  color?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        'inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium transition-colors',
        active
          ? 'border-primary/40 bg-primary/10 text-primary'
          : 'border-transparent bg-muted/50 text-muted-foreground hover:bg-accent hover:text-foreground',
      )}
    >
      {color && <span className="h-1.5 w-1.5 rounded-full" style={{ background: color }} aria-hidden />}
      {label}
    </button>
  );
}

function Section({
  title,
  hint,
  dot,
  children,
}: {
  title: string;
  hint?: string;
  dot?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mb-1.5">
      <div className="flex items-center gap-1.5 px-2 pb-1 pt-1.5">
        {dot && <span className="h-1.5 w-1.5 rounded-full" style={{ background: dot }} aria-hidden />}
        <h3 className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">{title}</h3>
        {hint && <span className="text-[10px] text-muted-foreground/70">· {hint}</span>}
      </div>
      <ul className="space-y-0.5">{children}</ul>
    </section>
  );
}

function CreatorRow({
  item,
  category,
  active,
  disabled,
  onAdd,
  onHover,
  onDragStart,
  onDragEnd,
}: {
  item: TriggerMeta | NodeMeta;
  category: string;
  active: boolean;
  disabled: boolean;
  onAdd: () => void;
  onHover: () => void;
  onDragStart: (e: React.DragEvent) => void;
  onDragEnd: () => void;
}) {
  const color = tint(category);
  const Icon = item.icon;
  return (
    <li>
      <button
        type="button"
        draggable={!disabled}
        onDragStart={onDragStart}
        onDragEnd={onDragEnd}
        onClick={onAdd}
        onMouseEnter={onHover}
        disabled={disabled}
        aria-label={`Add ${item.label} to canvas`}
        className={cn(
          'group flex w-full items-center gap-2.5 rounded-lg border px-2 py-1.5 text-left transition-colors',
          active
            ? 'border-primary/40 bg-primary/[0.06]'
            : 'border-transparent hover:border-border/70 hover:bg-accent/60',
          disabled && 'opacity-50',
        )}
      >
        <span
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-border/50"
          style={{ backgroundColor: `color-mix(in srgb, ${color} 14%, transparent)`, color }}
        >
          <Icon className="h-4 w-4" aria-hidden />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[13px] font-medium leading-tight">{item.label}</span>
          <span className="block truncate text-[11px] text-muted-foreground">{item.description}</span>
        </span>
        <span
          className={cn(
            'flex shrink-0 items-center gap-1 rounded-md border border-border/70 bg-background px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground transition-opacity',
            active ? 'opacity-100' : 'opacity-0 group-hover:opacity-100',
          )}
        >
          <Plus className="h-3 w-3" aria-hidden /> Add
        </span>
      </button>
    </li>
  );
}
