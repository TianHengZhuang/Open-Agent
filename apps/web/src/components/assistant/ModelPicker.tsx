'use client';

import * as React from 'react';
import Link from 'next/link';
import {
  AlertTriangle,
  Check,
  Cloud,
  Code2,
  Cpu,
  Eye,
  Loader2,
  Plug,
  RefreshCw,
  Search,
  Server,
  Settings,
  Sparkles,
  Star,
  Wrench,
  X,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { toUserMessage } from '@/lib/api';
import { cn } from '@/lib/utils';
import {
  useActivateModel,
  useKoroSettings,
  useKoroStatus,
  useLocalModels,
  useProviderModels,
  useUpdateKoroSettings,
  type LocalRuntime,
} from '@/features/koro/koro-api';
import type { ChatModelOverride } from '@/features/koro/use-chat-model';
import {
  API_PROVIDERS,
  filterModelOptions,
  loadModelPreferences,
  makeModelOption,
  rememberModel,
  resolveOptions,
  toggleModelFavorite,
  type Capability,
  type ModelOption,
  type ModelPreferences,
  type ProviderId,
} from '@/lib/ai-models';

export { API_PROVIDERS };

type Tab = 'local' | 'api';

const CAPABILITY_META: Record<Capability, { label: string; icon: typeof Cpu }> = {
  local: { label: 'Local', icon: Cpu },
  tools: { label: 'Tools', icon: Wrench },
  vision: { label: 'Vision', icon: Eye },
  code: { label: 'Code', icon: Code2 },
};

function openAiBase(runtime: LocalRuntime): string {
  const root = runtime.base_url.replace(/\/+$/, '');
  return root.endsWith('/v1') ? root : `${root}/v1`;
}

function CapabilityBadges({ capabilities }: { capabilities: Capability[] }) {
  if (capabilities.length === 0) return null;
  return (
    <span className="flex shrink-0 items-center gap-0.5">
      {capabilities.map((capability) => {
        const meta = CAPABILITY_META[capability];
        return (
          <span
            key={capability}
            title={meta.label}
            className="inline-flex items-center gap-0.5 rounded bg-muted px-1 py-0.5 text-[9px] font-medium uppercase tracking-wide text-muted-foreground"
          >
            <meta.icon className="h-2.5 w-2.5" aria-hidden />
            {meta.label}
          </span>
        );
      })}
    </span>
  );
}

/**
 * Model & API key switcher inside the composer: local runtimes, API-key
 * providers, search, favorites, recents and capability badges — without
 * leaving the chat.
 */
export function ModelPicker({
  onClose,
  override,
  onOverride,
}: {
  onClose: () => void;
  /** Model already chosen for this chat, if any. */
  override?: ChatModelOverride | null;
  /** When provided, model choices apply to this chat instead of the default. */
  onOverride?: (override: ChatModelOverride | null) => void;
}) {
  const status = useKoroStatus();
  const settings = useKoroSettings();
  const local = useLocalModels();
  const activate = useActivateModel();
  const save = useUpdateKoroSettings();
  const probe = useProviderModels();

  const [tab, setTab] = React.useState<Tab>(status.data?.local === false ? 'api' : 'local');
  const [provider, setProvider] = React.useState<ProviderId>('openai');
  const [baseUrl, setBaseUrl] = React.useState<string>('');
  const [apiKey, setApiKey] = React.useState('');
  const [models, setModels] = React.useState<string[]>([]);
  const [manualModel, setManualModel] = React.useState('');
  const [probeError, setProbeError] = React.useState<string | null>(null);
  const [notice, setNotice] = React.useState<string | null>(null);
  const [customLocal, setCustomLocal] = React.useState('');
  const [customModels, setCustomModels] = React.useState<string[]>([]);
  const [query, setQuery] = React.useState('');
  const [prefs, setPrefs] = React.useState<ModelPreferences>(() => loadModelPreferences());

  const preset = API_PROVIDERS.find((item) => item.id === provider) ?? API_PROVIDERS[0];
  const currentModel = settings.data?.model ?? status.data?.model ?? '';
  const currentBase = settings.data?.base_url ?? status.data?.base_url ?? '';
  const selectedModel = override?.model ?? currentModel;
  const selectedBase = override?.base_url ?? currentBase;
  const runtimes = React.useMemo(() => local.data?.runtimes ?? [], [local.data?.runtimes]);
  const anyLocalReady = runtimes.some((runtime) => runtime.reachable);

  React.useEffect(() => {
    setBaseUrl(preset.baseUrl);
    setApiKey('');
    setModels([]);
    setProbeError(null);
  }, [preset.baseUrl]);

  const options = React.useMemo(() => {
    const list: ModelOption[] = [];
    const push = (option: ModelOption) => {
      if (!list.some((item) => item.id === option.id)) list.push(option);
    };
    for (const runtime of runtimes) {
      for (const entry of runtime.models) {
        push(makeModelOption({ model: entry.name, baseUrl: openAiBase(runtime) }));
      }
    }
    if (currentModel) {
      push(
        makeModelOption({
          model: currentModel,
          baseUrl: currentBase,
          providerName: status.data?.provider,
        }),
      );
    }
    if (models.length > 0) {
      for (const model of models) push(makeModelOption({ model, baseUrl, providerId: provider }));
    }
    for (const model of customModels) {
      push(makeModelOption({ model, baseUrl: customLocal }));
    }
    return list;
  }, [
    runtimes,
    currentModel,
    currentBase,
    status.data?.provider,
    models,
    baseUrl,
    provider,
    customModels,
    customLocal,
  ]);

  const favorites = resolveOptions(prefs.favorites, options);
  const recents = resolveOptions(prefs.recent, options);

  const remember = (id: string) => setPrefs((current) => rememberModel(id, current));
  const star = (id: string) => setPrefs((current) => toggleModelFavorite(id, current));

  const isSelected = (model: string, url: string) =>
    model === selectedModel && url === selectedBase;

  /** Favorites and recents: local endpoints or the already-configured one. */
  const chooseOption = (option: ModelOption) => {
    remember(option.id);
    if (!onOverride) {
      void applyEndpointModel(option.baseUrl, option.model);
      return;
    }
    if (option.source === 'local' || option.baseUrl === currentBase) {
      onOverride({ model: option.model, base_url: option.baseUrl });
      setNotice(`${option.model} applies to this chat only.`);
      return;
    }
    // Another hosted provider needs its key: move the form there.
    setTab('api');
    setProvider(option.providerId);
    setBaseUrl(option.baseUrl);
    setManualModel(option.model);
    setNotice(`Add the ${option.providerName} key, then check the connection.`);
  };

  const activateLocal = async (runtime: LocalRuntime, model: string) => {
    setNotice(null);
    const url = openAiBase(runtime);
    remember(makeModelOption({ model, baseUrl: url }).id);
    if (onOverride) {
      onOverride({ model, base_url: url });
      setNotice(`${model} applies to this chat only.`);
      return;
    }
    try {
      await activate.mutateAsync({ base_url: url, model });
      setNotice(`${model} is now the assistant model.`);
    } catch (error) {
      setProbeError(toUserMessage(error));
    }
  };

  const probeEndpoint = async (url: string, key?: string, target: 'api' | 'local' = 'api') => {
    setProbeError(null);
    setNotice(null);
    try {
      const result = await probe.mutateAsync({ base_url: url, api_key: key || undefined });
      if (target === 'local') setCustomModels(result.models);
      else setModels(result.models);
      if (!result.ok) setProbeError(result.error ?? 'Could not reach the endpoint.');
    } catch (error) {
      setProbeError(toUserMessage(error));
    }
  };

  const applyEndpointModel = async (
    url: string,
    model: string,
    key?: string,
    scope: 'chat' | 'default' = 'default',
  ) => {
    setProbeError(null);
    remember(makeModelOption({ model, baseUrl: url, providerId: provider }).id);
    if (scope === 'chat' && onOverride) {
      onOverride({ model, base_url: url });
      setNotice(`${model} applies to this chat only.`);
      return;
    }
    try {
      await save.mutateAsync({ base_url: url, model, api_key: key || undefined });
      // A new key/provider is account setup: the default now matches, so the
      // per-chat override is redundant.
      onOverride?.(null);
      setNotice(`${model} is now the assistant model.`);
      setApiKey('');
    } catch (error) {
      setProbeError(toUserMessage(error));
    }
  };

  const busy = activate.isPending || save.isPending;
  const needle = query.trim().toLowerCase();
  const matches = (model: string, providerName: string) =>
    !needle || model.toLowerCase().includes(needle) || providerName.toLowerCase().includes(needle);

  const modelRow = (option: ModelOption, onChoose: () => void) => {
    const selected = isSelected(option.model, option.baseUrl);
    return (
      <div
        key={option.id}
        className={cn(
          'flex items-center gap-1.5 rounded-md border px-1.5 py-1',
          selected ? 'border-primary/40 bg-primary/[0.06]' : 'border-border',
        )}
      >
        <button
          onClick={onChoose}
          disabled={busy}
          className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
        >
          <span className="min-w-0 flex-1 truncate font-mono text-[11px]">{option.model}</span>
          <CapabilityBadges capabilities={option.capabilities} />
          {selected ? (
            <Check className="h-3 w-3 shrink-0 text-primary" aria-hidden />
          ) : (
            <span className="shrink-0 text-[10px] text-muted-foreground">Use</span>
          )}
        </button>
        <button
          onClick={() => star(option.id)}
          aria-pressed={prefs.favorites.includes(option.id)}
          aria-label={
            prefs.favorites.includes(option.id)
              ? `Remove ${option.model} from favorites`
              : `Favorite ${option.model}`
          }
          className={cn(
            'shrink-0 rounded p-0.5 hover:bg-accent',
            prefs.favorites.includes(option.id)
              ? 'text-amber-500'
              : 'text-muted-foreground/60 hover:text-foreground',
          )}
        >
          <Star
            className="h-3 w-3"
            fill={prefs.favorites.includes(option.id) ? 'currentColor' : 'none'}
            aria-hidden
          />
        </button>
      </div>
    );
  };

  return (
    <div
      className="absolute bottom-full left-0 z-40 mb-2 w-[min(440px,calc(100vw-2rem))] overflow-hidden rounded-xl border border-border bg-popover text-popover-foreground shadow-2xl"
      role="dialog"
      aria-label="Models and API keys"
    >
      <div className="flex items-center gap-2 border-b border-border/70 px-3 py-2">
        <Sparkles className="h-3.5 w-3.5 text-primary" aria-hidden />
        <p className="min-w-0 flex-1 truncate text-xs font-semibold">
          Models & API keys
          {onOverride && (
            <span className="block text-[10px] font-normal text-muted-foreground">
              Applies to this chat ·{' '}
              <Link
                href="/settings/models"
                className="text-primary hover:underline"
                onClick={onClose}
              >
                change the default
              </Link>
            </span>
          )}
        </p>
        <Link
          href="/settings/models"
          className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] text-muted-foreground hover:bg-accent hover:text-foreground"
        >
          <Settings className="h-3 w-3" aria-hidden /> Full settings
        </Link>
        <button
          onClick={onClose}
          aria-label="Close model picker"
          className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="flex gap-1 border-b border-border/70 px-2 py-1.5">
        {(
          [
            { id: 'local', label: 'Local models', icon: Server },
            { id: 'api', label: 'API key models', icon: Cloud },
          ] as const
        ).map((item) => (
          <button
            key={item.id}
            onClick={() => setTab(item.id)}
            aria-pressed={tab === item.id}
            className={cn(
              'inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px] font-medium transition-colors',
              tab === item.id
                ? 'bg-primary/10 text-primary'
                : 'text-muted-foreground hover:bg-accent hover:text-foreground',
            )}
          >
            <item.icon className="h-3 w-3" aria-hidden /> {item.label}
          </button>
        ))}
        <span
          className={cn(
            'ml-auto inline-flex items-center gap-1 rounded-full border px-1.5 text-[10px]',
            tab === 'api'
              ? settings.data?.api_key_set
                ? 'border-emerald-500/30 bg-emerald-500/[0.08] text-emerald-700 dark:text-emerald-300'
                : 'border-amber-500/30 bg-amber-500/[0.08] text-amber-700 dark:text-amber-300'
              : anyLocalReady
                ? 'border-emerald-500/30 bg-emerald-500/[0.08] text-emerald-700 dark:text-emerald-300'
                : 'border-border text-muted-foreground',
          )}
        >
          <span
            className={cn(
              'h-1.5 w-1.5 rounded-full',
              tab === 'api'
                ? settings.data?.api_key_set
                  ? 'bg-emerald-500'
                  : 'bg-amber-500'
                : anyLocalReady
                  ? 'bg-emerald-500'
                  : 'bg-muted-foreground/50',
            )}
            aria-hidden
          />
          {tab === 'api'
            ? settings.data?.api_key_set
              ? 'Key saved'
              : 'Key required'
            : anyLocalReady
              ? 'Local Ready'
              : 'Offline'}
        </span>
      </div>

      <div className="border-b border-border/70 px-2 py-1.5">
        <label className="relative block">
          <Search
            className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search models"
            aria-label="Search models"
            className="w-full rounded-md border border-input bg-background py-1 pl-7 pr-2 text-[11px] outline-none focus:border-primary/60"
          />
        </label>
      </div>

      <div className="oa-scroll max-h-[360px] overflow-y-auto p-3">
        {(favorites.length > 0 || recents.length > 0) && (
          <div className="mb-3 space-y-1.5 border-b border-border/60 pb-2.5">
            {favorites.length > 0 && (
              <div>
                <p className="pb-1 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/70">
                  Favorites
                </p>
                <div className="flex flex-wrap gap-1">
                  {favorites.map((option) => (
                    <button
                      key={option.id}
                      onClick={() => chooseOption(option)}
                      className="inline-flex max-w-full items-center gap-1 rounded-full border border-border px-2 py-0.5 text-[10px] hover:border-primary/40 hover:bg-accent"
                    >
                      <Star className="h-2.5 w-2.5 text-amber-500" fill="currentColor" aria-hidden />
                      <span className="truncate font-mono">{option.model}</span>
                      <span className="shrink-0 text-muted-foreground">{option.providerName}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}
            {recents.length > 0 && (
              <div>
                <p className="pb-1 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/70">
                  Recent
                </p>
                <div className="flex flex-wrap gap-1">
                  {recents.map((option) => (
                    <button
                      key={option.id}
                      onClick={() => chooseOption(option)}
                      className="rounded-full border border-border px-2 py-0.5 font-mono text-[10px] text-muted-foreground hover:border-primary/40 hover:bg-accent hover:text-foreground"
                    >
                      {option.model}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {tab === 'local' ? (
          <div className="space-y-3">
            <div className="flex items-center gap-2 rounded-lg border border-border/70 bg-muted/30 px-2.5 py-1.5 text-[11px]">
              <span className="truncate">
                {override ? 'This chat' : status.data?.local ? 'Active local model' : 'Active provider'}:{' '}
                <span className="font-medium">{selectedModel || 'none'}</span>
              </span>
              <button
                onClick={() => void local.refetch()}
                disabled={local.isFetching}
                className="ml-auto inline-flex shrink-0 items-center gap-1 text-[10px] text-muted-foreground hover:text-foreground disabled:opacity-50"
              >
                <RefreshCw
                  className={cn('h-3 w-3', local.isFetching && 'animate-spin')}
                  aria-hidden
                />
                Refresh models
              </button>
            </div>

            {local.isLoading ? (
              <p className="flex items-center gap-2 py-2 text-xs text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> Looking for local
                runtimes…
              </p>
            ) : runtimes.length === 0 || !anyLocalReady ? (
              <div className="space-y-1 rounded-lg border border-dashed border-border px-2.5 py-2 text-[11px] text-muted-foreground">
                <p className="font-medium text-foreground">No local runtime is reachable.</p>
                <p>
                  Start Ollama (<span className="font-mono">ollama serve</span>) or LM Studio and
                  press Refresh models. If a server is running, allow this origin in its CORS
                  settings.
                </p>
              </div>
            ) : null}

            {runtimes.map((runtime) => {
              const visible = runtime.models
                .map((entry) => entry.name)
                .filter((model) => matches(model, runtime.name));
              return (
                <section key={runtime.id} className="space-y-1">
                  <p className="flex items-center gap-1.5 text-[11px] font-semibold">
                    <span
                      className={cn(
                        'h-1.5 w-1.5 rounded-full',
                        runtime.reachable ? 'bg-emerald-500' : 'bg-muted-foreground/40',
                      )}
                      aria-hidden
                    />
                    {runtime.name}
                    <span className="ml-auto truncate font-mono text-[10px] font-normal text-muted-foreground">
                      {runtime.reachable ? openAiBase(runtime) : 'offline'}
                    </span>
                  </p>
                  {runtime.reachable && runtime.models.length === 0 && (
                    <p className="rounded border border-dashed border-border px-2 py-1 text-[10px] text-muted-foreground">
                      Connected, but no models are downloaded.
                    </p>
                  )}
                  {runtime.reachable && visible.length === 0 && needle && (
                    <p className="px-1 text-[10px] text-muted-foreground">
                      No models match “{query}”.
                    </p>
                  )}
                  <div className="space-y-1">
                    {visible.map((model) =>
                      modelRow(
                        makeModelOption({ model, baseUrl: openAiBase(runtime) }),
                        () => void activateLocal(runtime, model),
                      ),
                    )}
                  </div>
                </section>
              );
            })}

            <div className="space-y-1.5 border-t border-border/70 pt-2">
              <label className="text-[11px] font-medium" htmlFor="mp-custom-local">
                Custom local endpoint
              </label>
              <div className="flex gap-1.5">
                <input
                  id="mp-custom-local"
                  value={customLocal}
                  onChange={(event) => setCustomLocal(event.target.value)}
                  placeholder="http://127.0.0.1:8080/v1"
                  spellCheck={false}
                  className="min-w-0 flex-1 rounded-md border border-input bg-background px-2 py-1 font-mono text-[11px] outline-none focus:border-primary/60"
                />
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7 shrink-0 px-2 text-[11px]"
                  disabled={!customLocal.trim() || probe.isPending}
                  onClick={() => void probeEndpoint(customLocal.trim(), undefined, 'local')}
                >
                  {probe.isPending ? (
                    <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
                  ) : (
                    <Plug className="h-3 w-3" aria-hidden />
                  )}
                  Check
                </Button>
              </div>
              <div className="space-y-1">
                {filterModelOptions(
                  customModels.map((model) =>
                    makeModelOption({ model, baseUrl: customLocal }),
                  ),
                  query,
                ).map((option) =>
                  modelRow(option, () =>
                    void applyEndpointModel(option.baseUrl, option.model, undefined, 'chat'),
                  ),
                )}
              </div>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="flex flex-wrap gap-1">
              {API_PROVIDERS.map((item) => (
                <button
                  key={item.id}
                  onClick={() => setProvider(item.id)}
                  aria-pressed={provider === item.id}
                  className={cn(
                    'rounded-full border px-2 py-0.5 text-[11px] transition-colors',
                    provider === item.id
                      ? 'border-primary/40 bg-primary/10 text-primary'
                      : 'border-border text-muted-foreground hover:bg-accent hover:text-foreground',
                  )}
                >
                  {item.name}
                </button>
              ))}
            </div>

            <div className="space-y-1.5">
              <label className="text-[11px] font-medium" htmlFor="mp-base-url">
                Base URL
              </label>
              <input
                id="mp-base-url"
                value={baseUrl}
                onChange={(event) => setBaseUrl(event.target.value)}
                spellCheck={false}
                className="w-full rounded-md border border-input bg-background px-2 py-1 font-mono text-[11px] outline-none focus:border-primary/60"
              />
              <label className="text-[11px] font-medium" htmlFor="mp-api-key">
                API key
              </label>
              <input
                id="mp-api-key"
                type="password"
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                placeholder={
                  settings.data?.api_key_set && baseUrl === currentBase
                    ? 'Saved key — leave blank to keep it'
                    : `Paste your ${preset.name} key`
                }
                autoComplete="off"
                className="w-full rounded-md border border-input bg-background px-2 py-1 font-mono text-[11px] outline-none focus:border-primary/60"
              />
              <p className="text-[10px] text-muted-foreground">
                {preset.hint} · keys are encrypted before they are stored.
              </p>
            </div>

            <Button
              size="sm"
              variant="outline"
              className="h-7 w-full text-[11px]"
              disabled={!baseUrl.trim() || probe.isPending}
              onClick={() => void probeEndpoint(baseUrl.trim(), apiKey)}
            >
              {probe.isPending ? (
                <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
              ) : (
                <Plug className="h-3 w-3" aria-hidden />
              )}
              Check connection & list models
            </Button>

            <div className="space-y-1.5 border-t border-border/70 pt-2">
              <label className="text-[11px] font-medium" htmlFor="mp-manual-model">
                Or type a model name
              </label>
              <div className="flex gap-1.5">
                <input
                  id="mp-manual-model"
                  value={manualModel}
                  onChange={(event) => setManualModel(event.target.value)}
                  placeholder="e.g. gpt-4.1-mini"
                  spellCheck={false}
                  className="min-w-0 flex-1 rounded-md border border-input bg-background px-2 py-1 font-mono text-[11px] outline-none focus:border-primary/60"
                />
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7 shrink-0 px-2 text-[11px]"
                  disabled={!baseUrl.trim() || !manualModel.trim() || busy}
                  onClick={() => void applyEndpointModel(baseUrl.trim(), manualModel.trim(), apiKey)}
                >
                  Use
                </Button>
              </div>
            </div>

            {models.length > 0 ? (
              <div className="space-y-1">
                <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                  Choose a model
                </p>
                {filterModelOptions(
                  models.map((model) =>
                    makeModelOption({ model, baseUrl, providerId: provider }),
                  ),
                  query,
                ).map((option) =>
                  modelRow(option, () =>
                    void applyEndpointModel(baseUrl.trim(), option.model, apiKey),
                  ),
                )}
              </div>
            ) : (
              <p className="rounded-lg border border-dashed border-border px-2.5 py-2 text-[11px] text-muted-foreground">
                Check the connection to list the models this key can use.
              </p>
            )}
          </div>
        )}

        {probeError && (
          <p className="mt-2 flex items-start gap-1.5 rounded-md border border-destructive/30 bg-destructive/[0.06] px-2 py-1.5 text-[11px] text-destructive">
            <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden /> {probeError}
          </p>
        )}
        {notice && (
          <p className="mt-2 flex items-start gap-1.5 rounded-md border border-emerald-500/30 bg-emerald-500/[0.06] px-2 py-1.5 text-[11px] text-emerald-700 dark:text-emerald-300">
            <Check className="mt-0.5 h-3 w-3 shrink-0" aria-hidden /> {notice}
          </p>
        )}
      </div>
    </div>
  );
}
