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
import { Search, Plus, Star, History } from 'lucide-react';
import type { WorkflowEditor } from '@/features/workflows/use-workflow-editor';
import { cn } from '@/lib/utils';

const RECENT_KEY = 'oa:wf-recent-nodes';
const FAV_KEY = 'oa:wf-fav-nodes';
const MAX_RECENT = 6;

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
    .map((k) => (k.startsWith('trigger:')
      ? TRIGGER_CATALOG.find((t) => `trigger:${t.type}` === k)
      : byKey.get(k)))
    .filter(Boolean) as (TriggerMeta | NodeMeta)[];
  const showRecent = query === '' && category === 'all' && recentItems.length > 0;
  const showFavs = query === '' && category === 'all' && favs.length > 0;
  const favItems = favs
    .map((k) => (k.startsWith('trigger:')
      ? TRIGGER_CATALOG.find((t) => `trigger:${t.type}` === k)
      : byKey.get(k)))
    .filter(Boolean) as (TriggerMeta | NodeMeta)[];

  const isTrigger = (x: TriggerMeta | NodeMeta): x is TriggerMeta =>
    (x as TriggerMeta).type !== undefined && TRIGGER_CATALOG.includes(x as TriggerMeta);

  return (
    <div className="oa-panel flex h-full flex-col overflow-hidden">
      <div className="space-y-2.5 border-b border-border/70 p-3">
        <div className="flex items-center gap-2.5">
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <Plus className="h-4 w-4" aria-hidden />
          </span>
          <div>
            <h2 className="text-sm font-semibold leading-tight">Palette</h2>
            <p className="oa-caption">Drag a node onto the canvas</p>
          </div>
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
        <Select value={category} onChange={(e) => setCategory(e.target.value)} aria-label="Filter by category" className="h-8 rounded-lg border-transparent bg-muted/60 text-sm">
          <option value="all">All categories</option>
          {definitionsByCategory().map((g) => (
            <option key={g.category} value={g.category}>{g.category}</option>
          ))}
        </Select>
      </div>
      <div className="oa-scroll flex-1 space-y-4 overflow-y-auto p-3">
        {showFavs && (
          <section aria-label="Favorites">
            <h3 className="oa-caption mb-1.5 flex items-center gap-1 font-medium uppercase tracking-wide">
              <Star className="h-3 w-3" aria-hidden /> Favorites
            </h3>
            <ul className="space-y-1.5">
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
            <h3 className="oa-caption mb-1.5 flex items-center gap-1 font-medium uppercase tracking-wide">
              <History className="h-3 w-3" aria-hidden /> Recent
            </h3>
            <ul className="space-y-1.5">
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
        {query === '' && category === 'all' && (
          <section aria-label="Triggers">
            <h3 className="oa-caption mb-1.5 font-medium uppercase tracking-wide">Triggers</h3>
            <ul className="space-y-1.5">
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
          </section>
        )}
        {groups.map((g) => (
          <section key={g.category} aria-label={g.category}>
            <h3 className="oa-caption mb-1.5 font-medium uppercase tracking-wide">{g.category}</h3>
            <ul className="space-y-1.5">
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
          </section>
        ))}
        {triggers.length === 0 && groups.length === 0 && (
          <p className="py-6 text-center text-sm text-muted-foreground">No palette items match.</p>
        )}
      </div>
      <p className="border-t border-border/70 p-3 text-[11px] text-muted-foreground">
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
  return (
    <li
      draggable={!disabled}
      onDragStart={onDragStart}
      className="group flex cursor-grab items-start gap-2.5 rounded-xl border border-transparent p-2 transition-colors hover:border-border/70 hover:bg-accent/60 active:cursor-grabbing"
      title={`Drag to canvas, or use the + button (${item.description})`}
    >
      <span
        className={cn(
          'mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg',
          isTrigger ? 'bg-amber-500/10 text-amber-600 dark:text-amber-400' : 'bg-primary/10 text-primary',
        )}
      >
        <item.icon className="h-4 w-4" aria-hidden />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-medium leading-tight">{item.label}</span>
        <span className="block truncate text-xs text-muted-foreground">{item.description}</span>
      </span>
      <button
        onClick={onFav}
        disabled={disabled}
        aria-label={fav ? `Remove ${item.label} from favorites` : `Add ${item.label} to favorites`}
        aria-pressed={fav}
        className={cn(
          'shrink-0 rounded-md p-1.5 transition-opacity hover:bg-background disabled:opacity-40',
          fav ? 'opacity-100' : 'opacity-60 group-hover:opacity-100 focus:opacity-100',
        )}
      >
        <Star className={cn('h-3.5 w-3.5', fav ? 'fill-amber-400 text-amber-400' : 'text-muted-foreground')} aria-hidden />
      </button>
      <button
        onClick={onAdd}
        disabled={disabled}
        aria-label={`Add ${item.label} to canvas`}
        className="shrink-0 rounded-md border border-border/70 bg-background p-1.5 text-muted-foreground opacity-60 transition-opacity hover:bg-accent hover:text-foreground group-hover:opacity-100 focus:opacity-100 disabled:opacity-40"
      >
        <Plus className="h-3.5 w-3.5" aria-hidden />
      </button>
    </li>
  );
}

