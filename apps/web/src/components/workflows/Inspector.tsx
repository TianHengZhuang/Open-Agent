'use client';

import * as React from 'react';
import { nodeMeta, triggerMeta, type ConfigField } from '@/features/workflows/node-catalog';
import { getExpressionSuggestions } from '@/features/workflows/expressions';
import type { WorkflowEditor } from '@/features/workflows/use-workflow-editor';
import type { WorkflowNodeRunResult } from '@/features/workflows/workflows-api';
import { Input } from '@/components/ui/input';
import { Textarea, Select, Switch, Field } from '@/components/ui/form';
import { Button } from '@/components/ui/button';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Trash2, Settings2, KeyRound, Play } from 'lucide-react';

export function Inspector({
  editor,
  disabled = false,
  onTestStep,
}: {
  editor: WorkflowEditor;
  disabled?: boolean;
  /** Execute this single node with the supplied input (n8n "Test step"). */
  onTestStep?: (nodeId: string, input: Record<string, unknown>) => Promise<WorkflowNodeRunResult>;
}) {
  const { definition, selection, validation, dispatch } = editor;

  if (!selection) {
    return <WorkflowSettingsPanel editor={editor} disabled={disabled} />;
  }

  if (selection.kind === 'edge') {
    const edge = definition.edges.find((e) => e.id === selection.id);
    if (!edge) return <EmptyInspector />;
    return (
      <div className="oa-panel flex h-full min-h-0 flex-col overflow-hidden">
        <InspectorHeader title="Edge" subtitle={`${edge.from} → ${edge.to}`} onDelete={disabled ? undefined : () => dispatch({ type: 'delete-selection' })} />
        <div className="oa-scroll flex-1 space-y-4 overflow-y-auto p-4">
          <Field label="Branch label" htmlFor="edge-label" help="Shown on the canvas. For condition nodes, use the output port name.">
            <Input
              id="edge-label"
              value={edge.label ?? ''}
              disabled={disabled}
              placeholder="e.g. true"
              onChange={(e) => dispatch({ type: 'update-edge', id: edge.id, patch: { label: e.target.value || undefined } })}
            />
          </Field>
          <Field label="Run when" htmlFor="edge-when">
            <Select
              id="edge-when"
              value={edge.condition?.when ?? 'always'}
              disabled={disabled}
              onChange={(e) =>
                dispatch({
                  type: 'update-edge',
                  id: edge.id,
                  patch: { condition: { ...(edge.condition ?? { when: 'always' as const }), when: e.target.value as 'always' | 'success' | 'failure' } },
                })
              }
            >
              <option value="always">Always</option>
              <option value="success">On success</option>
              <option value="failure">On failure</option>
            </Select>
          </Field>
          <Field label="Expression" htmlFor="edge-expr" help="Optional boolean expression evaluated by the execution engine.">
            <Input
              id="edge-expr"
              value={edge.condition?.expression ?? ''}
              disabled={disabled}
              placeholder="items.length > 0"
              onChange={(e) =>
                dispatch({
                  type: 'update-edge',
                  id: edge.id,
                  patch: { condition: { ...(edge.condition ?? { when: 'always' as const }), expression: e.target.value || undefined } },
                })
              }
            />
          </Field>
        </div>
      </div>
    );
  }

  const trigger = selection.kind === 'trigger'
    ? definition.triggers.find((t) => t.id === selection.id)
    : undefined;
  const node = selection.kind === 'node'
    ? definition.nodes.find((n) => n.id === selection.id)
    : undefined;
  const item = trigger ?? node;
  if (!item) return <EmptyInspector />;

  const isTrigger = trigger !== undefined;
  const fields = isTrigger
    ? triggerMeta(trigger.type).configFields
    : nodeMeta(node!.type).configFields;
  const errors = validation.errors.filter((e) => e.node_id === item.id);

  const setName = (name: string) =>
    dispatch(isTrigger
      ? { type: 'update-trigger', id: item.id, patch: { name } }
      : { type: 'update-node', id: item.id, patch: { name } });

  const setConfig = (key: string, value: unknown) => {
    const config = { ...(item.config ?? {}), [key]: value };
    dispatch(isTrigger
      ? { type: 'update-trigger', id: item.id, patch: { config } }
      : { type: 'update-node', id: item.id, patch: { config } });
  };

  return (
    <div className="oa-panel flex h-full min-h-0 flex-col overflow-hidden">
      <InspectorHeader
        title={item.name}
        subtitle={isTrigger ? `Trigger · ${trigger!.type}` : `Node · ${node!.type}`}
        onDelete={disabled ? undefined : () => dispatch({ type: 'delete-selection' })}
      />
      <div className="oa-scroll flex-1 space-y-4 overflow-y-auto p-4">
        {errors.length > 0 && (
          <Alert variant="destructive">
            <AlertDescription>
              <ul className="list-disc space-y-0.5 pl-4">
                {errors.map((e, i) => (
                  <li key={i}>{e.message}</li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        )}
        <Field label="Name" htmlFor="item-name">
          <Input id="item-name" value={item.name} disabled={disabled} onChange={(e) => setName(e.target.value)} />
        </Field>
        {fields.map((f) => (
          <ConfigInput
            key={f.key}
            field={f}
            value={item.config?.[f.key]}
            disabled={disabled}
            suggestions={getExpressionSuggestions({
              variables: definition.variables.map((v) => v.name),
              nodeIds: definition.nodes.map((n) => n.id),
              triggerIds: definition.triggers.map((t) => t.id),
            })}
            onChange={(v) => setConfig(f.key, v)}
          />
        ))}
        {isTrigger && trigger!.type === 'webhook' && <WebhookUrl path={String(trigger!.config?.path ?? '')} />}
        {!isTrigger && (
          <>
            <Field label="Enabled" htmlFor="node-enabled" help="Disabled nodes are skipped by validation and future execution.">
              <Switch
                label="Node enabled"
                checked={node!.disabled !== true}
                disabled={disabled}
                onCheckedChange={(v) =>
                  dispatch({ type: 'update-node', id: item.id, patch: { disabled: !v } })
                }
              />
            </Field>
            <Field label="Note" htmlFor="node-notes" help="Author annotation only — never executed.">
              <Textarea
                id="node-notes"
                value={node!.notes ?? ''}
                disabled={disabled}
                placeholder="Why does this step exist?"
                rows={2}
                onChange={(e) =>
                  dispatch({ type: 'update-node', id: item.id, patch: { notes: e.target.value || undefined } })
                }
              />
            </Field>
            <Field label="Timeout (seconds)" htmlFor="node-timeout" help="Empty = engine default.">
              <Input
                id="node-timeout"
                type="number"
                min={1}
                disabled={disabled}
                value={node!.timeout_seconds ?? ''}
                placeholder="Engine default"
                onChange={(e) =>
                  dispatch({
                    type: 'update-node',
                    id: item.id,
                    patch: { timeout_seconds: e.target.value === '' ? undefined : Number(e.target.value) },
                  })
                }
              />
            </Field>
            <Field
              label="On error"
              htmlFor="node-on-error"
              help="Continue passes the input to the next node; the node is still reported as failed."
            >
              <Select
                id="node-on-error"
                value={String(node!.config?.on_error ?? 'stop')}
                disabled={disabled}
                onChange={(e) => setConfig('on_error', e.target.value === 'stop' ? undefined : e.target.value)}
              >
                <option value="stop">Stop workflow</option>
                <option value="continue">Continue (pass input through)</option>
              </Select>
            </Field>
            <Field label="Max retries" htmlFor="node-retry" help="0–10 additional attempts.">
              <Input
                id="node-retry"
                type="number"
                min={0}
                max={10}
                disabled={disabled}
                value={node!.retry_policy?.max_attempts ?? 0}
                onChange={(e) =>
                  dispatch({
                    type: 'update-node',
                    id: item.id,
                    patch: { retry_policy: { max_attempts: Math.max(0, Math.min(10, Number(e.target.value) || 0)) } },
                  })
                }
              />
            </Field>
          </>
        )}
        {onTestStep && <TestStep nodeId={item.id} disabled={disabled} onTestStep={onTestStep} />}
        <p className="oa-caption">
          ID: <span className="oa-code">{item.id}</span>
        </p>
      </div>
    </div>
  );
}

function TestStep({
  nodeId,
  disabled,
  onTestStep,
}: {
  nodeId: string;
  disabled?: boolean;
  onTestStep: (nodeId: string, input: Record<string, unknown>) => Promise<WorkflowNodeRunResult>;
}) {
  const [open, setOpen] = React.useState(false);
  const [text, setText] = React.useState('{}');
  const [running, setRunning] = React.useState(false);
  const [result, setResult] = React.useState<WorkflowNodeRunResult | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  const run = async () => {
    let parsed: unknown = {};
    try {
      parsed = text.trim() ? JSON.parse(text) : {};
    } catch {
      setError('Input is not valid JSON');
      return;
    }
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
      setError('Input must be a JSON object');
      return;
    }
    setError(null);
    setRunning(true);
    try {
      setResult(await onTestStep(nodeId, parsed as Record<string, unknown>));
    } catch (e) {
      setResult(null);
      setError(e instanceof Error ? e.message : 'Test step failed');
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="rounded-xl border border-primary/20 bg-primary/[0.04] p-3">
      <div className="flex items-center justify-between gap-2">
        <div>
          <p className="flex items-center gap-1.5 text-sm font-medium">
            <Play className="h-3.5 w-3.5 text-primary" aria-hidden /> Test step
          </p>
          <p className="oa-caption">Runs only this node. Upstream nodes are not executed.</p>
        </div>
        <Button type="button" size="sm" variant="outline" onClick={() => setOpen((v) => !v)}>
          {open ? 'Hide' : 'Test step'}
        </Button>
      </div>
      {open && (
        <div className="mt-2 space-y-2">
          <label htmlFor="test-step-input" className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
            Input (JSON)
          </label>
          <textarea
            id="test-step-input"
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={3}
            spellCheck={false}
            className="w-full resize-y rounded-lg border bg-background px-2.5 py-2 font-mono text-xs outline-none focus:border-primary"
          />
          {error && <p className="text-xs text-destructive">{error}</p>}
          <div className="flex justify-end">
            <Button type="button" size="sm" onClick={run} disabled={disabled || running}>
              {running ? 'Running…' : 'Run step'}
            </Button>
          </div>
          {result && (
            <div className="space-y-1">
              <p className="text-xs">
                <span className="font-medium">{result.status}</span>
                {result.error ? ` — ${result.error}` : ''}
                {result.duration_ms !== undefined ? ` · ${result.duration_ms} ms` : ''}
              </p>
              <pre className="oa-scroll max-h-48 overflow-auto rounded-lg border bg-background p-2.5 text-[11px]">
                {JSON.stringify(result.outputs ?? {}, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function WebhookUrl({ path }: { path: string }) {
  const raw = (process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000/api/v1').replace(/\/$/, '');
  const base = /\/api\/v1$/.test(raw) ? raw : `${raw}/api/v1`;
  const suffix = path.replace(/^\/+/, '');
  return (
    <Field
      label="Production webhook URL"
      htmlFor="webhook-url"
      help="Active workflows accept POSTs here. Publish the workflow to turn it on."
    >
      <Input
        id="webhook-url"
        readOnly
        value={suffix ? `${base}/hooks/${suffix}` : ''}
        placeholder="Set a path to get a URL"
        onFocus={(e) => e.currentTarget.select()}
      />
    </Field>
  );
}

function ConfigInput({
  field,
  value,
  disabled,
  suggestions,
  onChange,
}: {
  field: ConfigField;
  value: unknown;
  disabled?: boolean;
  suggestions?: { value: string; label: string }[];
  onChange: (v: unknown) => void;
}) {
  const id = `cfg-${field.key}`;
  switch (field.type) {
    case 'boolean':
      return (
        <Field label={field.label} htmlFor={id} help={field.help}>
          <Switch
            label={field.label}
            checked={value === true}
            disabled={disabled}
            onCheckedChange={(v) => onChange(v)}
          />
        </Field>
      );
    case 'expression':
      return (
        <Field label={field.label} htmlFor={id} help={field.help ?? 'Supports {{variables.name}} and {{nodes.<id>.output}}.'} required={field.required}>
          <Input
            id={id}
            value={typeof value === 'string' ? value : ''}
            disabled={disabled}
            placeholder={field.placeholder}
            list={suggestions ? `${id}-suggestions` : undefined}
            onChange={(e) => onChange(e.target.value)}
          />
          {suggestions && (
            <datalist id={`${id}-suggestions`}>
              {suggestions.map((s) => (
                <option key={s.value} value={s.value}>{s.label}</option>
              ))}
            </datalist>
          )}
        </Field>
      );
    case 'json': {
      const text = value !== undefined ? (typeof value === 'string' ? value : JSON.stringify(value, null, 2)) : '';
      let jsonError: string | undefined;
      if (text.trim() !== '') {
        try {
          JSON.parse(text);
        } catch {
          jsonError = 'Not valid JSON yet — fix before publishing.';
        }
      }
      return (
        <Field label={field.label} htmlFor={id} help={field.help ?? 'JSON object.'} required={field.required} error={jsonError}>
          <Textarea
            id={id}
            value={text}
            disabled={disabled}
            placeholder={field.placeholder}
            rows={3}
            className="font-mono text-[13px]"
            onChange={(e) => {
              try {
                onChange(JSON.parse(e.target.value));
              } catch {
                onChange(e.target.value);
              }
            }}
          />
        </Field>
      );
    }
    case 'credential':
      return (
        <Field
          label={field.label}
          htmlFor={id}
          help={field.help ?? 'Reference id of a stored credential. Secrets are never stored inline.'}
          required={field.required}
        >
          <div className="relative">
            <KeyRound className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden />
            <Input
              id={id}
              value={typeof value === 'string' ? value : ''}
              disabled={disabled}
              placeholder={field.placeholder ?? 'credential_id or {{secrets.name}}'}
              className="pl-8 font-mono text-[13px]"
              autoComplete="off"
              spellCheck={false}
              onChange={(e) => onChange(e.target.value)}
            />
          </div>
        </Field>
      );
    case 'textarea':
      return (
        <Field label={field.label} htmlFor={id} help={field.help} required={field.required}>
          <Textarea
            id={id}
            value={typeof value === 'string' ? value : (value !== undefined ? JSON.stringify(value) : '')}
            disabled={disabled}
            placeholder={field.placeholder}
            rows={3}
            onChange={(e) => onChange(e.target.value)}
          />
        </Field>
      );
    case 'number':
      return (
        <Field label={field.label} htmlFor={id} help={field.help} required={field.required}>
          <Input
            id={id}
            type="number"
            min={field.min}
            max={field.max}
            disabled={disabled}
            value={typeof value === 'number' ? value : ''}
            placeholder={field.placeholder}
            onChange={(e) => onChange(e.target.value === '' ? undefined : Number(e.target.value))}
          />
        </Field>
      );
    case 'select':
      return (
        <Field label={field.label} htmlFor={id} help={field.help} required={field.required}>
          <Select id={id} value={typeof value === 'string' ? value : ''} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
            <option value="">Select…</option>
            {field.options?.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </Select>
        </Field>
      );
    case 'stringlist':
      return (
        <Field label={field.label} htmlFor={id} help={field.help ?? 'One entry per line.'} required={field.required}>
          <Textarea
            id={id}
            value={Array.isArray(value) ? (value as string[]).join('\n') : ''}
            disabled={disabled}
            placeholder="oncall@example.com"
            rows={3}
            onChange={(e) => onChange(e.target.value.split('\n').map((s) => s.trim()).filter(Boolean))}
          />
        </Field>
      );
    case 'keyvalue':
      return (
        <Field label={field.label} htmlFor={id} help={field.help ?? 'JSON object.'} required={field.required}>
          <Textarea
            id={id}
            value={value !== undefined ? (typeof value === 'string' ? value : JSON.stringify(value, null, 2)) : ''}
            disabled={disabled}
            placeholder={field.placeholder}
            rows={3}
            onChange={(e) => {
              try {
                onChange(JSON.parse(e.target.value));
              } catch {
                onChange(e.target.value);
              }
            }}
          />
        </Field>
      );
    default:
      return (
        <Field label={field.label} htmlFor={id} help={field.help} required={field.required}>
          <Input
            id={id}
            value={typeof value === 'string' || typeof value === 'number' ? String(value) : ''}
            disabled={disabled}
            placeholder={field.placeholder}
            onChange={(e) => onChange(e.target.value)}
          />
        </Field>
      );
  }
}

function InspectorHeader({ title, subtitle, onDelete }: { title: string; subtitle: string; onDelete?: () => void }) {
  return (
    <div className="flex items-center justify-between gap-2 border-b border-border/70 px-3 py-2.5">
      <div className="flex min-w-0 items-center gap-2.5">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <Settings2 className="h-4 w-4" aria-hidden />
        </span>
        <div className="min-w-0">
          <h2 className="truncate text-sm font-semibold leading-tight">{title}</h2>
          <p className="oa-caption truncate capitalize">{subtitle}</p>
        </div>
      </div>
      {onDelete && (
        <button
          onClick={onDelete}
          aria-label={`Delete ${title}`}
          className="shrink-0 rounded-lg p-2 text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
        >
          <Trash2 className="h-4 w-4" />
        </button>
      )}
    </div>
  );
}

function EmptyInspector() {
  return (
    <div className="oa-panel flex h-full flex-col items-center justify-center p-6 text-center">
      <Settings2 className="mb-2 h-6 w-6 text-muted-foreground" aria-hidden />
      <p className="text-sm text-muted-foreground">Select a trigger, node, or edge to configure it.</p>
    </div>
  );
}

function WorkflowSettingsPanel({ editor, disabled }: { editor: WorkflowEditor; disabled?: boolean }) {
  const { definition } = editor;
  const [varName, setVarName] = React.useState('');
  const [varType, setVarType] = React.useState<'string' | 'number' | 'boolean' | 'json'>('string');

  const setSettings = (settings: Record<string, unknown>) => {
    editor.load({ ...definition, settings });
  };

  return (
    <div className="oa-panel flex h-full min-h-0 flex-col overflow-hidden">
      <div className="border-b border-border/70 px-4 py-3">
        <h2 className="flex items-center gap-2 text-sm font-semibold">
          <span className="flex h-6 w-6 items-center justify-center rounded-md bg-primary/10 text-primary">
            <Settings2 className="h-3.5 w-3.5" aria-hidden />
          </span>
          Workflow settings
        </h2>
        <p className="oa-caption">
          {definition.triggers.length} trigger{definition.triggers.length === 1 ? '' : 's'} ·{' '}
          {definition.nodes.length} node{definition.nodes.length === 1 ? '' : 's'} ·{' '}
          {definition.edges.length} edge{definition.edges.length === 1 ? '' : 's'}
        </p>
      </div>
      <div className="oa-scroll flex-1 space-y-5 overflow-y-auto p-4">
        <section aria-label="Variables">
          <h3 className="mb-2 text-sm font-medium">Variables</h3>
          {definition.variables.length === 0 && (
            <p className="oa-caption mb-2">No variables. Reference them as {'{{name}}'} in configs.</p>
          )}
          <ul className="space-y-1.5">
            {definition.variables.map((v) => (
              <li key={v.name} className="flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-sm">
                <span className="oa-code">{v.name}</span>
                <span className="oa-caption">{v.type}{v.required ? ' · required' : ''}</span>
                {!disabled && (
                  <button
                    aria-label={`Remove variable ${v.name}`}
                    className="ml-auto rounded p-1 text-destructive hover:bg-destructive/10"
                    onClick={() => editor.load({ ...definition, variables: definition.variables.filter((x) => x.name !== v.name) })}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                )}
              </li>
            ))}
          </ul>
          {!disabled && (
            <form
              className="mt-2 flex gap-1.5"
              onSubmit={(e) => {
                e.preventDefault();
                const name = varName.trim();
                if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) return;
                if (definition.variables.some((v) => v.name === name)) return;
                editor.load({ ...definition, variables: [...definition.variables, { name, type: varType }] });
                setVarName('');
              }}
            >
              <Input value={varName} onChange={(e) => setVarName(e.target.value)} placeholder="variable_name" aria-label="New variable name" className="h-8" />
              <Select value={varType} onChange={(e) => setVarType(e.target.value as typeof varType)} aria-label="Variable type" className="h-8 w-28">
                <option value="string">string</option>
                <option value="number">number</option>
                <option value="boolean">boolean</option>
                <option value="json">json</option>
              </Select>
              <Button type="submit" size="sm" variant="outline">Add</Button>
            </form>
          )}
        </section>
        <section aria-label="Run settings">
          <h3 className="mb-2 text-sm font-medium">Run settings</h3>
          <div className="space-y-3">
            <Field label="Timezone" htmlFor="wf-tz">
              <Input
                id="wf-tz"
                value={typeof definition.settings.timezone === 'string' ? definition.settings.timezone : ''}
                disabled={disabled}
                placeholder="UTC"
                onChange={(e) => setSettings({ ...definition.settings, timezone: e.target.value || undefined })}
              />
            </Field>
            <Field label="Max concurrency" htmlFor="wf-conc" help="1–32 parallel branches.">
              <Input
                id="wf-conc"
                type="number"
                min={1}
                max={32}
                disabled={disabled}
                value={typeof definition.settings.max_concurrency === 'number' ? definition.settings.max_concurrency : ''}
                placeholder="1"
                onChange={(e) => setSettings({ ...definition.settings, max_concurrency: e.target.value === '' ? undefined : Number(e.target.value) })}
              />
            </Field>
          </div>
        </section>
      </div>
    </div>
  );
}
