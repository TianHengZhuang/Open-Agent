'use client';

import * as React from 'react';
import {
  TRIGGER_CATALOG,
  definitionsByCategory,
  type NodeMeta,
  type TriggerMeta,
} from '@/features/workflows/node-catalog';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/form';
import { Search, Plus, Star, History, ChevronRight, GripVertical } from 'lucide-react';
import type { WorkflowEditor } from '@/features/workflows/use-workflow-editor';
import { cn } from '@/lib/utils';

const RECENT_KEY = 'oa:wf-recent-nodes';
const FAV_KEY = 'oa:wf-fav-nodes';
const MAX_RECENT = 6;

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

function readList(key: string): string[] {
  try {
    const raw = window.localStorage.getItem(key);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((x) => typeof x === 'string') : [];
  } catch {
    return [];
  }
}

function writeList(key: string, list: string[]) {
  try {
    window.localStorage.setItem(key, JSON.stringify(list.slice(0, 20)));
  } catch {
    /* quota — ignore */
  }
}

export function NodePalette({ editor, disabled = false }: { editor: WorkflowEditor; disabled?: boolean }) {
  const [q, setQ] = React.useState('');
  const [category, setCategory] = React.useState<string>('all');
  const [recent, setRecent] = React.useState<string[]>([]);
  const [favs, setFavs] = React.useState<string[]>([]);
  const query = q.trim().toLowerCase();

  React.useEffect(() => {
    setRecent(readList(RECENT_KEY));
    setFavs(readList(FAV_KEY));
  }, []);

  const remember = (key: string) => {
    setRecent((prev) => {
      const next = [key, ...prev.filter((x) => x !== key)].slice(0, MAX_RECENT);
      writeList(RECENT_KEY, next);
      return next;
    });
  };

  const toggleFav = (key: string) => {
    setFavs((prev) => {
      const next = prev.includes(key) ? prev.filter((x) => x !== key) : [...prev, key];
      writeList(FAV_KEY, next);
      return next;
    });
  };

  const dragStart = (e: React.DragEvent, kind: 'trigger' | 'node', nodeType: string) => {
    e.dataTransfer.setData('application/x-openagent-node', JSON.stringify({ kind, nodeType }));
    e.dataTransfer.effectAllowed = 'copy';
  };

  const addTrigger = (t: TriggerMeta) => {
    if (disabled) return;
    editor.addTrigger(t.type);
    remember(`trigger:${t.type}`);
  };
  const addNode = (n: NodeMeta) => {
    if (disabled) return;
    editor.addNode(n.type as never);
    remember(`node:${n.type}`);
  };

  const matchQ = (label: string, description: string, type: string) =>
    !query ||
    label.toLowerCase().includes(query) ||
    description.toLowerCase().includes(query) ||
    type.toLowerCase().includes(query);

  const triggers = TRIGGER_CATALOG.filter((t) => matchQ(t.label, t.description, t.type));
  const groups = definitionsByCategory()
    .map((g) => ({
      ...g,
      items: g.items.filter((n) => matchQ(n.label, n.description, String(n.type))),
    }))
    .filter((g) => g.items.length > 0 && (category === 'all' || g.category === category));

  const allNodes = definitionsByCategory().flatMap((g) => g.items);
  const byKey = new Map<string, NodeMeta>(allNodes.map((n) => [`node:${n.type}`, n]));
  const recentItems = recent
    .map((k) =>
      k.startsWith('trigger:')
        ? TRIGGER_CATALOG.find((t) => `trigger:${t.type}` === k)
        : byKey.get(k),
    )
    .filter(Boolean) as (TriggerMeta | NodeMeta)[];
  const showRecent = query === '' && category === 'all' && recentItems.length > 0;
  const showFavs = query === '' && category === 'all' && favs.length > 0;
  const favItems = favs
    .map((k) =>
      k.startsWith('trigger:')
        ? TRIGGER_CATALOG.find((t) => `trigger:${t.type}` === k)
        : byKey.get(k),
    )
    .filter(Boolean) as (TriggerMeta | NodeMeta)[];

  const isTrigger = (x: TriggerMeta | NodeMeta): x is TriggerMeta =>
    (x as TriggerMeta).type !== undefined && TRIGGER_CATALOG.includes(x as TriggerMeta);

  const forceOpen = query !== '' || category !== 'all';
  const total = triggers.length + groups.reduce((sum, g) => sum + g.items.length, 0);

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-card">
      <div className="space-y-2 border-b border-border px-3 py-3">
        <div className="flex items-center justify-between gap-2">
          <div>
            <h2 className="text-[13px] font-semibold leading-tight">Palette</h2>
            <p className="text-[11px] text-muted-foreground">Drag a node onto the canvas</p>
          </div>
          <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium tabular-nums text-muted-foreground">
            {total}
          </span>
        </div>
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search nodes…"
            aria-label="Search node palette"
            className="h-8 rounded-lg border-transparent bg-muted/60 pl-8 text-sm focus:border-primary"
          />
        </div>
        <Select
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          aria-label="Filter by category"
          className="h-8 rounded-lg border-transparent bg-muted/60 text-sm"
        >
          <option value="all">All categories</option>
          {definitionsByCategory().map((g) => (
            <option key={g.category} value={g.category}>
              {g.category}
            </option>
          ))}
        </Select>
      </div>

      <div className="oa-scroll flex-1 space-y-3 overflow-y-auto p-2">
        {showFavs && (
          <section aria-label="Favorites">
            <h3 className="mb-1 flex items-center gap-1 px-2 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
              <Star className="h-3 w-3" aria-hidden /> Favorites
            </h3>
            <ul className="space-y-0.5">
              {favItems.map((item) => (
                <PaletteRow
                  key={favKey(item)}
                  item={item}
                  isTrigger={isTrigger(item)}
                  fav
                  disabled={disabled}
                  onAdd={() => (isTrigger(item) ? addTrigger(item) : addNode(item))}
                  onFav={() => toggleFav(favKey(item))}
                  onDragStart={(e) => dragStart(e, isTrigger(item) ? 'trigger' : 'node', String(item.type))}
                />
              ))}
            </ul>
          </section>
        )}

        {showRecent && (
          <section aria-label="Recent">
            <h3 className="mb-1 flex items-center gap-1 px-2 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
              <History className="h-3 w-3" aria-hidden /> Recent
            </h3>
            <ul className="space-y-0.5">
              {recentItems.map((item) => (
                <PaletteRow
                  key={favKey(item)}
                  item={item}
                  isTrigger={isTrigger(item)}
                  fav={favs.includes(favKey(item))}
                  disabled={disabled}
                  onAdd={() => (isTrigger(item) ? addTrigger(item) : addNode(item))}
                  onFav={() => toggleFav(favKey(item))}
                  onDragStart={(e) => dragStart(e, isTrigger(item) ? 'trigger' : 'node', String(item.type))}
                />
              ))}
            </ul>
          </section>
        )}

        {triggers.length > 0 && (category === 'all' || category === 'Triggers') && (
          <details open={forceOpen || undefined} className="group/details">
            <summary className="flex cursor-pointer list-none items-center gap-1.5 rounded-md px-2 py-1.5 hover:bg-accent/50 [&::-webkit-details-marker]:hidden">
              <ChevronRight className="h-3 w-3 shrink-0 text-muted-foreground transition-transform group-open/details:rotate-90" aria-hidden />
              <h3 className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Triggers</h3>
              <span className="ml-auto text-[10px] tabular-nums text-muted-foreground" aria-hidden>{triggers.length}</span>
            </summary>
            <ul className="mt-0.5 space-y-0.5">
              {triggers.map((t) => (
                <PaletteRow
                  key={t.type}
                  item={t}
                  isTrigger
                  fav={favs.includes(`trigger:${t.type}`)}
                  disabled={disabled}
                  onAdd={() => addTrigger(t)}
                  onFav={() => toggleFav(`trigger:${t.type}`)}
                  onDragStart={(e) => dragStart(e, 'trigger', t.type)}
                />
              ))}
            </ul>
          </details>
        )}

        {groups.map((g) => (
          <details key={g.category} open={forceOpen || undefined} className="group/details">
            <summary className="flex cursor-pointer list-none items-center gap-1.5 rounded-md px-2 py-1.5 hover:bg-accent/50 [&::-webkit-details-marker]:hidden">
              <ChevronRight className="h-3 w-3 shrink-0 text-muted-foreground transition-transform group-open/details:rotate-90" aria-hidden />
              <span className="oa-category-dot" style={{ background: tint(g.category) }} aria-hidden />
              <h3 className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">{g.category}</h3>
              <span className="ml-auto text-[10px] tabular-nums text-muted-foreground" aria-hidden>{g.items.length}</span>
            </summary>
            <ul className="mt-0.5 space-y-0.5">
              {g.items.map((n) => (
                <PaletteRow
                  key={String(n.type)}
                  item={n}
                  isTrigger={false}
                  fav={favs.includes(`node:${n.type}`)}
                  disabled={disabled}
                  onAdd={() => addNode(n)}
                  onFav={() => toggleFav(`node:${n.type}`)}
                  onDragStart={(e) => dragStart(e, 'node', String(n.type))}
                />
              ))}
            </ul>
          </details>
        ))}

        {triggers.length === 0 && groups.length === 0 && (
          <p className="py-6 text-center text-sm text-muted-foreground">No palette items match.</p>
        )}
      </div>

      <p className="border-t border-border px-3 py-2 text-[11px] text-muted-foreground">
        <span className="oa-kbd mr-1">+</span> adds at center · drag to place freely
      </p>
    </div>
  );
}

function favKey(item: TriggerMeta | NodeMeta): string {
  return TRIGGER_CATALOG.includes(item as TriggerMeta)
    ? `trigger:${(item as TriggerMeta).type}`
    : `node:${(item as NodeMeta).type}`;
}

function PaletteRow({
  item,
  isTrigger,
  fav,
  disabled,
  onAdd,
  onFav,
  onDragStart,
}: {
  item: TriggerMeta | NodeMeta;
  isTrigger: boolean;
  fav: boolean;
  disabled: boolean;
  onAdd: () => void;
  onFav: () => void;
  onDragStart: (e: React.DragEvent) => void;
}) {
  const category = isTrigger ? 'Triggers' : (item as NodeMeta).category;
  const color = tint(category);
  return (
    <li
      draggable={!disabled}
      onDragStart={onDragStart}
      className={cn(
        'group flex cursor-grab items-center gap-2 rounded-lg border border-transparent px-1.5 py-1.5 transition-colors hover:border-border/70 hover:bg-accent/50 active:cursor-grabbing',
        disabled && 'opacity-60',
      )}
      title={`Drag to canvas, or use the + button (${item.description})`}
    >
      <GripVertical className="h-3 w-3 shrink-0 text-transparent transition-colors group-hover:text-muted-foreground/50" aria-hidden />
      <span
        className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-border/50"
        style={{ background: color, backgroundColor: `color-mix(in srgb, ${color} 14%, transparent)`, color }}
      >
        <item.icon className="h-3.5 w-3.5" aria-hidden />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[12.5px] font-medium leading-tight">{item.label}</span>
        <span className="block truncate text-[11px] text-muted-foreground">{item.description}</span>
      </span>
      <button
        onClick={onFav}
        disabled={disabled}
        aria-label={fav ? `Remove ${item.label} from favorites` : `Add ${item.label} to favorites`}
        aria-pressed={fav}
        className={cn(
          'shrink-0 rounded-md p-1 transition-opacity hover:bg-background disabled:opacity-40',
          fav ? 'opacity-100' : 'opacity-0 group-hover:opacity-100 focus:opacity-100',
        )}
      >
        <Star className={cn('h-3 w-3', fav ? 'fill-amber-400 text-amber-400' : 'text-muted-foreground')} aria-hidden />
      </button>
      <button
        onClick={onAdd}
        disabled={disabled}
        aria-label={`Add ${item.label} to canvas`}
        className="shrink-0 rounded-md border border-border/70 bg-background p-1 text-muted-foreground opacity-0 transition-opacity hover:bg-accent hover:text-foreground group-hover:opacity-100 focus:opacity-100 disabled:opacity-40"
      >
        <Plus className="h-3 w-3" aria-hidden />
      </button>
    </li>
  );
}
