'use client';

import * as React from 'react';
import {
  AlertTriangle,
  Check,
  Cpu,
  Download,
  ExternalLink,
  HardDrive,
  Loader2,
  Play,
  RefreshCw,
  Server,
  Trash2,
  X,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useToast } from '@/components/ui/toast';
import { toUserMessage } from '@/lib/api';
import { cn } from '@/lib/utils';
import {
  useActivateModel,
  useCancelPull,
  useDeleteModel,
  useLocalModels,
  usePullJob,
  usePullModel,
  useTestModel,
  type LocalRuntime,
  type PullJob,
} from '@/features/koro/koro-api';

/** The copilot always talks OpenAI-compatible, so endpoints end in /v1. */
function openAiBase(runtime: LocalRuntime): string {
  const root = runtime.base_url.replace(/\/+$/, '');
  return root.endsWith('/v1') ? root : `${root}/v1`;
}

function formatBytes(bytes?: number | null): string {
  if (!bytes || bytes <= 0) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`;
}

/**
 * Detects local runtimes (Ollama, LM Studio, llama.cpp, vLLM), downloads models
 * through Ollama with live progress, and points the copilot at the chosen model.
 */
export function ModelManager({
  compact = false,
  onActivated,
}: {
  compact?: boolean;
  onActivated?: (model: string) => void;
}) {
  const models = useLocalModels(true);
  const pull = usePullModel();
  const cancel = useCancelPull();
  const remove = useDeleteModel();
  const activate = useActivateModel();
  const test = useTestModel();
  const { toast } = useToast();

  const [jobId, setJobId] = React.useState<string | null>(null);
  const [busyModel, setBusyModel] = React.useState<string | null>(null);
  const job = usePullJob(jobId);

  const data = models.data;
  const runtimes = React.useMemo(() => data?.runtimes ?? [], [data?.runtimes]);
  const ollama = runtimes.find((r) => r.can_pull && r.reachable) ?? null;
  const reachable = runtimes.filter((r) => r.reachable);
  const installed = React.useMemo(() => {
    const names = new Set<string>();
    for (const runtime of runtimes) {
      for (const model of runtime.models) names.add(model.name);
    }
    return names;
  }, [runtimes]);

  const lastStatus = React.useRef<string>('');
  React.useEffect(() => {
    const status = job.data?.status;
    if (!status || status === lastStatus.current) return;
    lastStatus.current = status;
    if (status === 'done') {
      void models.refetch();
      toast({ kind: 'success', title: `${job.data?.model} is ready to use` });
    } else if (status === 'error') {
      toast({
        kind: 'error',
        title: 'Download failed',
        description: job.data?.error || 'The runtime stopped the download.',
      });
    }
  }, [job.data?.status, job.data?.model, job.data?.error, models, toast]);

  const startPull = async (model: string, baseUrl: string) => {
    setBusyModel(model);
    try {
      const created = await pull.mutateAsync({ model, base_url: baseUrl });
      setJobId(created.id);
    } catch (error) {
      toast({ kind: 'error', title: 'Could not start download', description: toUserMessage(error) });
    } finally {
      setBusyModel(null);
    }
  };

  const activateModel = async (runtime: LocalRuntime, model: string) => {
    try {
      await activate.mutateAsync({ base_url: openAiBase(runtime), model });
      toast({ kind: 'success', title: `Using ${model}`, description: `${runtime.name} · ${openAiBase(runtime)}` });
      onActivated?.(model);
    } catch (error) {
      toast({ kind: 'error', title: 'Could not switch model', description: toUserMessage(error) });
    }
  };

  const testModel = async (runtime: LocalRuntime, model: string) => {
    setBusyModel(`test:${model}`);
    try {
      const result = await test.mutateAsync({ model, base_url: openAiBase(runtime) });
      toast(
        result.ok
          ? {
              kind: 'success',
              title: `${model} responded in ${result.latency_ms} ms`,
              description: result.reply || 'The model is working.',
            }
          : {
              kind: 'error',
              title: `${model} did not respond`,
              description: result.error ?? 'Check that the runtime is running.',
            },
      );
    } catch (error) {
      toast({ kind: 'error', title: 'Test failed', description: toUserMessage(error) });
    } finally {
      setBusyModel(null);
    }
  };

  const deleteModel = async (runtime: LocalRuntime, model: string) => {
    setBusyModel(`delete:${model}`);
    try {
      const result = await remove.mutateAsync({ model, base_url: runtime.base_url });
      toast({
        kind: result.deleted ? 'success' : 'warning',
        title: result.deleted ? `Removed ${model}` : `${model} was not found`,
      });
      void models.refetch();
    } catch (error) {
      toast({ kind: 'error', title: 'Could not remove model', description: toUserMessage(error) });
    } finally {
      setBusyModel(null);
    }
  };

  const padding = compact ? 'p-3' : 'p-5';

  if (models.isLoading) {
    return (
      <div className={cn('flex items-center gap-2 text-sm text-muted-foreground', padding)}>
        <Loader2 className="h-4 w-4 animate-spin" /> Looking for local model runtimes…
      </div>
    );
  }

  if (models.isError) {
    return (
      <div className={cn('space-y-2 text-sm', padding)}>
        <p className="flex items-center gap-2 font-medium text-destructive">
          <AlertTriangle className="h-4 w-4" /> Could not check local runtimes
        </p>
        <p className="text-xs text-muted-foreground">{toUserMessage(models.error)}</p>
        <Button size="sm" variant="outline" onClick={() => void models.refetch()}>
          <RefreshCw className="mr-1.5 h-3.5 w-3.5" /> Retry
        </Button>
      </div>
    );
  }

  const activeJob: PullJob | null = job.data ?? null;
  const running = activeJob?.status === 'running';

  return (
    <div className={cn('space-y-4', padding)}>
      {/* Hardware summary */}
      {data && !compact && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
          <span className="inline-flex items-center gap-1.5">
            <Cpu className="h-3.5 w-3.5" />
            {data.hardware.cpu_count ?? '?'} cores · {data.hardware.memory_gb ?? '?'} GB RAM
          </span>
          <span className="inline-flex items-center gap-1.5">
            <HardDrive className="h-3.5 w-3.5" />
            {data.hardware.disk_free_gb ?? '?'} GB free
          </span>
          <span className="inline-flex items-center gap-1.5">
            <Server className="h-3.5 w-3.5" />
            {reachable.length} runtime{reachable.length === 1 ? '' : 's'} online
          </span>
        </div>
      )}

      {/* Nothing running: show install guidance */}
      {reachable.length === 0 && (
        <section className="rounded-xl border border-amber-500/35 bg-amber-500/[0.06] p-3">
          <p className="flex items-center gap-2 text-sm font-medium">
            <AlertTriangle className="h-4 w-4 text-amber-600 dark:text-amber-400" />
            No local model runtime found
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            Install one of these once. They run entirely on this machine and need no
            account. Then come back and download a model.
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            <a
              href={data?.install.ollama_download_url ?? 'https://ollama.com/download'}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 rounded-md border border-input px-2.5 py-1.5 text-xs font-medium hover:bg-accent"
            >
              <Download className="h-3.5 w-3.5" /> Get Ollama
              <ExternalLink className="h-3 w-3 opacity-60" />
            </a>
            <a
              href={data?.install.lmstudio_download_url ?? 'https://lmstudio.ai'}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 rounded-md border border-input px-2.5 py-1.5 text-xs font-medium hover:bg-accent"
            >
              <Download className="h-3.5 w-3.5" /> Get LM Studio
              <ExternalLink className="h-3 w-3 opacity-60" />
            </a>
            <Button size="sm" variant="ghost" onClick={() => void models.refetch()}>
              <RefreshCw className="mr-1.5 h-3.5 w-3.5" /> Check again
            </Button>
          </div>
        </section>
      )}

      {/* Running download */}
      {activeJob && activeJob.status !== 'cancelled' && (
        <section className="rounded-xl border bg-card p-3">
          <div className="flex items-center justify-between gap-2">
            <p className="min-w-0 truncate text-xs font-medium">
              {running ? 'Downloading' : activeJob.status === 'done' ? 'Downloaded' : 'Download failed'}{' '}
              <span className="font-mono">{activeJob.model}</span>
            </p>
            <span className="shrink-0 text-[11px] tabular-nums text-muted-foreground">
              {running ? `${Math.round(activeJob.progress)}%` : activeJob.status}
            </span>
          </div>
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted">
            <div
              className={cn(
                'h-full rounded-full transition-[width] duration-500',
                activeJob.status === 'error' ? 'bg-destructive' : 'bg-primary',
              )}
              style={{ width: `${Math.min(100, Math.max(2, activeJob.progress))}%` }}
            />
          </div>
          <div className="mt-1.5 flex items-center justify-between gap-2">
            <p className="min-w-0 truncate text-[11px] text-muted-foreground">
              {activeJob.status === 'error' ? activeJob.error : activeJob.detail}
              {activeJob.total > 0 && running
                ? ` · ${formatBytes(activeJob.completed)} / ${formatBytes(activeJob.total)}`
                : ''}
            </p>
            {running ? (
              <button
                onClick={() => {
                  void cancel.mutateAsync(activeJob.id);
                  setJobId(null);
                }}
                className="shrink-0 rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
                aria-label="Cancel download"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            ) : (
              <button
                onClick={() => setJobId(null)}
                className="shrink-0 rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
                aria-label="Dismiss download status"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
        </section>
      )}

      {/* Installed models, grouped by runtime */}
      {reachable.map((runtime) => (
        <section key={runtime.id} className="space-y-2">
          <div className="flex items-center gap-2">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" aria-hidden />
            <h3 className="text-xs font-semibold">{runtime.name}</h3>
            {runtime.version && (
              <span className="text-[10px] text-muted-foreground">v{runtime.version}</span>
            )}
            <span className="ml-auto font-mono text-[10px] text-muted-foreground">
              {runtime.base_url}
            </span>
          </div>
          {runtime.models.length === 0 ? (
            <p className="rounded-lg border border-dashed px-3 py-2 text-[11px] text-muted-foreground">
              No models downloaded yet. Pick one below.
            </p>
          ) : (
            <ul className="space-y-1.5">
              {runtime.models.map((model) => {
                const isActive = data?.configured.model === model.name;
                const isTesting = busyModel === `test:${model.name}`;
                const isDeleting = busyModel === `delete:${model.name}`;
                return (
                  <li
                    key={model.name}
                    className={cn(
                      'flex items-center gap-2 rounded-lg border px-2.5 py-2',
                      isActive && 'border-primary/40 bg-primary/[0.04]',
                    )}
                  >
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-mono text-xs font-medium">{model.name}</p>
                      <p className="text-[10px] text-muted-foreground">
                        {[
                          model.parameter_size,
                          model.quantization,
                          model.size_bytes ? formatBytes(model.size_bytes) : null,
                        ]
                          .filter(Boolean)
                          .join(' · ') || 'Local model'}
                      </p>
                    </div>
                    {isActive && (
                      <span className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary">
                        <Check className="h-3 w-3" /> Active
                      </span>
                    )}
                    <button
                      onClick={() => void testModel(runtime, model.name)}
                      disabled={isTesting}
                      className="rounded p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-50"
                      aria-label={`Test ${model.name}`}
                      title="Send a tiny prompt"
                    >
                      {isTesting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
                    </button>
                    {!isActive && (
                      <Button
                        size="sm"
                        variant="outline"
                        className="h-7 px-2 text-[11px]"
                        onClick={() => void activateModel(runtime, model.name)}
                      >
                        Use
                      </Button>
                    )}
                    {runtime.can_pull && (
                      <button
                        onClick={() => void deleteModel(runtime, model.name)}
                        disabled={isDeleting}
                        className="rounded p-1.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive disabled:opacity-50"
                        aria-label={`Delete ${model.name}`}
                        title="Delete from disk"
                      >
                        {isDeleting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      ))}

      {/* Recommended downloads */}
      {data && (
        <section className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <h3 className="text-xs font-semibold">Recommended models</h3>
            <button
              onClick={() => void models.refetch()}
              className="inline-flex items-center gap-1 text-[10px] text-muted-foreground hover:text-foreground"
            >
              <RefreshCw className="h-3 w-3" /> Refresh
            </button>
          </div>
          <ul className="space-y-1.5">
            {data.recommended.map((model) => {
              const isInstalled = installed.has(model.name);
              const isActive = data.configured.model === model.name;
              const isPulling = running && activeJob?.model === model.name;
              const downloading = busyModel === model.name;
              return (
                <li key={model.name} className="rounded-lg border px-2.5 py-2">
                  <div className="flex items-start gap-2">
                    <div className="min-w-0 flex-1">
                      <p className="flex items-center gap-1.5 text-xs font-medium">
                        {model.label}
                        <span className="font-mono text-[10px] font-normal text-muted-foreground">
                          {model.name}
                        </span>
                      </p>
                      <p className="mt-0.5 text-[11px] text-muted-foreground">{model.description}</p>
                      <p className="mt-1 flex flex-wrap gap-x-2 text-[10px] text-muted-foreground">
                        <span>{model.size}</span>
                        <span>≥ {model.min_ram_gb} GB RAM</span>
                        <span className="italic">best for {model.best_for}</span>
                      </p>
                    </div>
                    <div className="flex shrink-0 flex-col items-end gap-1">
                      {isActive ? (
                        <span className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary">
                          <Check className="h-3 w-3" /> Active
                        </span>
                      ) : isInstalled ? (
                        <Button
                          size="sm"
                          variant="outline"
                          className="h-7 px-2 text-[11px]"
                          onClick={() => {
                            const runtime =
                              runtimes.find((r) => r.models.some((m) => m.name === model.name)) ?? ollama;
                            if (runtime) void activateModel(runtime, model.name);
                          }}
                        >
                          Use
                        </Button>
                      ) : (
                        <Button
                          size="sm"
                          className="h-7 px-2 text-[11px]"
                          disabled={!ollama || isPulling || downloading}
                          onClick={() => ollama && void startPull(model.name, ollama.base_url)}
                          title={ollama ? 'Download with Ollama' : 'Ollama must be running to download'}
                        >
                          {isPulling || downloading ? (
                            <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                          ) : (
                            <Download className="mr-1 h-3 w-3" />
                          )}
                          {isPulling ? 'Downloading' : 'Download'}
                        </Button>
                      )}
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
          {!ollama && reachable.length > 0 && (
            <p className="text-[10px] text-muted-foreground">
              Downloads need Ollama running. The other runtimes manage models in their own app.
            </p>
          )}
        </section>
      )}
    </div>
  );
}
