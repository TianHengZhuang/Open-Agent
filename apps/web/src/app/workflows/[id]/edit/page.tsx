"use client";

import * as React from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { Layout } from "@/components/layout";
import { Protected } from "@/components/protected";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { StatusBadge } from "@/components/ui/status";
import { PermissionGate } from "@/components/PermissionGate";
import { useToast } from "@/components/ui/toast";
import { toUserMessage, ApiError } from "@/lib/api";
import { PageLoading } from "@/components/ui/loading";
import { ErrorState } from "@/components/ui/states";
import {
  useWorkflow,
  useWorkflowMutations,
  type WorkflowRunResult,
} from "@/features/workflows/workflows-api";
import { useWorkflowEditor } from "@/features/workflows/use-workflow-editor";
import { WorkflowCanvas, type AddNodeRequest } from "@/components/workflows/WorkflowCanvas";
import { NodePalette } from "@/components/workflows/NodePalette";
import { NodeCreator } from "@/components/workflows/NodeCreator";
import { Inspector } from "@/components/workflows/Inspector";
import { ValidationPanel } from "@/components/workflows/ValidationPanel";
import { RunPanel } from "@/components/workflows/RunPanel";
import { ExecutionLog } from "@/components/workflows/ExecutionLog";
import { KoroPanel } from "@/components/workflows/KoroPanel";
import {
  downloadEnvelope,
  importDefinition,
} from "@/features/workflows/serialize";
import { emptyDefinition } from "@/features/workflows/types";
import { Dialog } from "@/components/ui/dialog";
import {
  ArrowLeft,
  Save,
  Play,
  Sparkles,
  Undo2,
  Redo2,
  Download,
  Upload,
  CheckCircle2,
  XCircle,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  ListChecks,
  Workflow as WorkflowIcon,
  Settings,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";

type StudioView = "editor" | "executions";

export default function WorkflowBuilderPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const { workflow, isLoading, error, refetch } = useWorkflow(id);
  const mutations = useWorkflowMutations();
  const { toast } = useToast();
  const router = useRouter();
  const fileRef = React.useRef<HTMLInputElement>(null);

  const baseline = workflow?.latest_version?.definition ?? null;
  const editor = useWorkflowEditor({
    initial: baseline ?? emptyDefinition(),
    draftKey: `oa:wf-draft:${id}`,
    baseline,
  });
  const [name, setName] = React.useState(workflow?.name ?? "");
  const [draftNotice, setDraftNotice] = React.useState(false);
  const [runResult, setRunResult] = React.useState<WorkflowRunResult | null>(null);
  const [runOpen, setRunOpen] = React.useState(false);
  const [runError, setRunError] = React.useState<string | null>(null);
  const [koroOpen, setKoroOpen] = React.useState(false);

  // Studio chrome state.
  const [view, setView] = React.useState<StudioView>("editor");
  const [paletteOpen, setPaletteOpen] = React.useState(true);
  const [panelOpen, setPanelOpen] = React.useState(true);
  const [rightTab, setRightTab] = React.useState<"node" | "issues">("node");
  const [creator, setCreator] = React.useState<AddNodeRequest | null>(null);

  const runStatus = React.useMemo(() => {
    const map: Record<string, string> = {};
    for (const run of runResult?.node_runs ?? []) map[run.node_id] = run.status;
    return map;
  }, [runResult]);

  // Selecting something opens the node detail panel, like n8n's NDV.
  const selectionId = editor.selection?.id ?? null;
  React.useEffect(() => {
    if (!selectionId) return;
    setPanelOpen(true);
    setRightTab("node");
  }, [selectionId]);

  // Load saved definition when it arrives (once per version).
  const loadedVersion = React.useRef<string | null>(null);
  React.useEffect(() => {
    const v = workflow?.latest_version;
    if (!v || loadedVersion.current === `${workflow!.id}@${v.id}`) return;
    loadedVersion.current = `${workflow!.id}@${v.id}`;
    setName(workflow!.name);
    try {
      const raw = window.localStorage.getItem(`oa:wf-draft:${id}`);
      if (raw) {
        const draft = JSON.parse(raw);
        if (
          draft &&
          draft.schema_version === "1.0" &&
          JSON.stringify(draft) !== JSON.stringify(v.definition)
        ) {
          setDraftNotice(true);
          return; // keep autosaved draft; user chooses below
        }
      }
    } catch {
      /* fall through to saved version */
    }
    editor.load(v.definition);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflow?.latest_version?.id]);

  // Unsaved-changes guard.
  React.useEffect(() => {
    if (!editor.dirty) return;
    const onBefore = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", onBefore);
    return () => window.removeEventListener("beforeunload", onBefore);
  }, [editor.dirty]);

  // All hooks stay above the early returns (Rules of Hooks). Anything that
  // reads `workflow` uses optional chaining until it has loaded; the sync
  // effects below populate state once it arrives.
  const canEdit = workflow?.status !== "archived";
  const saving = mutations.update.isPending;
  const [saveState, setSaveState] = React.useState<
    "saved" | "saving" | "failed" | "offline"
  >("saved");
  const [online, setOnline] = React.useState(true);
  const [conflict, setConflict] = React.useState(false);
  const [tags, setTags] = React.useState<string[]>(workflow?.tags ?? []);
  const knownUpdatedAt = React.useRef<string | null>(
    workflow?.updated_at ?? null,
  );

  React.useEffect(() => {
    setTags(workflow?.tags ?? []);
    knownUpdatedAt.current = workflow?.updated_at ?? null;
  }, [workflow?.id, workflow?.updated_at]); // eslint-disable-line react-hooks/exhaustive-deps

  // Online / offline indicator (offline edits stay local + autosaved).
  React.useEffect(() => {
    const update = () => setOnline(navigator.onLine);
    update();
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    return () => {
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
    };
  }, []);

  // Ctrl/Cmd+S saves from anywhere on the builder page.
  const saveRef = React.useRef<() => Promise<boolean>>(async () => false);
  const canEditRef = React.useRef(canEdit);
  canEditRef.current = canEdit;
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        if (canEditRef.current) void saveRef.current();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  if (isLoading) {
    return (
      <Protected>
        <Layout>
          <PageLoading label="Loading builder…" />
        </Layout>
      </Protected>
    );
  }
  if (error || !workflow) {
    return (
      <Protected>
        <Layout>
          <ErrorState
            title="Workflow not found"
            description={error ?? "No access."}
            onRetry={() => refetch()}
          />
        </Layout>
      </Protected>
    );
  }

  const doSave = async (
    definition = editor.definition,
    nextName = name,
  ): Promise<boolean> => {
    if (!navigator.onLine) {
      setSaveState("offline");
      toast({
        kind: "warning",
        title: "Offline",
        description:
          "Your changes are kept locally and autosaved. Save again when reconnected.",
      });
      return false;
    }
    setSaveState("saving");
    try {
      const saved = await mutations.update.mutateAsync({
        id: workflow.id,
        name: nextName.trim() !== workflow.name ? nextName.trim() : undefined,
        tags:
          JSON.stringify([...tags].sort()) !==
          JSON.stringify([...(workflow.tags ?? [])].sort())
            ? tags
            : undefined,
        definition,
        expected_updated_at: knownUpdatedAt.current ?? undefined,
      });
      knownUpdatedAt.current = saved.updated_at ?? knownUpdatedAt.current;
      editor.markSaved(definition);
      setDraftNotice(false);
      setSaveState("saved");
      toast({ kind: "success", title: "Saved — new draft version created" });
      return true;
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setConflict(true);
        setSaveState("saved");
      } else {
        setSaveState("failed");
        toast({
          kind: "error",
          title: "Save failed",
          description: `${toUserMessage(e)} Your changes are still present locally.`,
        });
      }
      return false;
    }
  };
  saveRef.current = doSave;

  const doPublish = async () => {
    if (editor.dirty) {
      const ok = await doSave();
      if (!ok) return;
    }
    if (!editor.validation.valid) {
      toast({
        kind: "error",
        title: "Cannot publish",
        description:
          "Fix validation errors first. Drafts may stay invalid; published versions may not.",
      });
      return;
    }
    try {
      await mutations.publish.mutateAsync(workflow.id);
      toast({ kind: "success", title: "Workflow activated" });
    } catch (e) {
      toast({
        kind: "error",
        title: "Publish failed",
        description: toUserMessage(e),
      });
    }
  };

  const doUnpublish = async () => {
    try {
      await mutations.unpublish.mutateAsync(workflow.id);
      toast({ kind: "success", title: "Workflow deactivated" });
    } catch (e) {
      toast({
        kind: "error",
        title: "Could not deactivate",
        description: toUserMessage(e),
      });
    }
  };

  const doRun = async (input: Record<string, unknown> = {}) => {
    setRunOpen(true);
    setRunError(null);
    try {
      // Runs the canvas as-is — unsaved edits count, like n8n's test run.
      const result = await mutations.execute.mutateAsync({
        id: workflow.id,
        definition: editor.definition,
        input,
      });
      setRunResult(result);
      if (result.status === "failed") {
        toast({
          kind: "error",
          title: result.message,
          description: result.error_message ?? undefined,
        });
      } else if (result.status === "waiting") {
        toast({ kind: "info", title: result.message });
      } else {
        toast({
          kind: "success",
          title: result.message,
          description: `${result.summary?.succeeded ?? 0} node(s) succeeded · ${
            result.summary?.duration_ms ?? 0
          } ms`,
        });
      }
    } catch (e) {
      const message = toUserMessage(e);
      setRunError(message);
      toast({ kind: "error", title: "Run failed", description: message });
    }
  };

  const onImportFile = async (file: File) => {
    const text = await file.text();
    const res = importDefinition(text);
    if (!res.ok || !res.definition) {
      toast({ kind: "error", title: "Import failed", description: res.error });
      return;
    }
    editor.load(res.definition);
    toast({
      kind: "success",
      title: res.meta?.name
        ? `Imported “${res.meta.name}” — review, then save`
        : "Imported — review, then save",
      description: res.migrated
        ? "Migrated to the current schema version on import."
        : undefined,
    });
  };

  const isActive = workflow.status === "active";
  const issueCount =
    editor.validation.errors.length + editor.validation.warnings.length;
  const canPublish = canEdit && editor.validation.valid;

  return (
    <Protected>
      <Layout>
        <div className="flex h-full min-h-0 flex-col overflow-hidden bg-canvas">
          {/* ---------------------------------------------------------------- */}
          {/* Studio top bar                                                    */}
          {/* ---------------------------------------------------------------- */}
          <header className="flex shrink-0 flex-wrap items-center gap-1.5 border-b border-border bg-card px-2 py-1.5 sm:gap-2 sm:px-3">
            <Link
              href="/workflows"
              aria-label="Back to workflows"
              className="oa-icon-button"
            >
              <ArrowLeft className="h-4 w-4" />
            </Link>
            <span className="oa-brand hidden h-7 w-7 items-center justify-center rounded-lg text-white shadow-sm sm:flex">
              <WorkflowIcon className="h-4 w-4" aria-hidden />
            </span>

            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={!canEdit}
              aria-label="Workflow name"
              className="h-8 w-[140px] min-w-0 border-transparent bg-transparent px-1.5 text-sm font-semibold hover:border-border focus:border-primary sm:w-[220px] sm:text-[15px]"
            />
            <StatusBadge status={workflow.status} />
            <SaveStatus
              online={online}
              saving={saving || saveState === "saving"}
              failed={saveState === "failed"}
              dirty={editor.dirty}
              onRetry={() => doSave()}
            />
            <span className="hidden lg:inline-flex">
              <TagEditor tags={tags} onChange={setTags} disabled={!canEdit} />
            </span>

            <div className="mx-1 hidden h-5 w-px bg-border md:block" aria-hidden />

            <div
              role="tablist"
              aria-label="Studio view"
              className="flex items-center gap-0.5 rounded-lg bg-muted/60 p-0.5"
            >
              <button
                role="tab"
                aria-selected={view === "editor"}
                data-active={view === "editor"}
                onClick={() => setView("editor")}
                className="oa-studio-tab h-7"
              >
                Editor
              </button>
              <button
                role="tab"
                aria-selected={view === "executions"}
                data-active={view === "executions"}
                onClick={() => setView("executions")}
                className="oa-studio-tab h-7"
              >
                Executions
              </button>
            </div>

            <div className="ml-auto flex items-center gap-1">
              <button
                onClick={() => setPaletteOpen((v) => !v)}
                aria-label={paletteOpen ? "Hide node panel" : "Show node panel"}
                aria-pressed={paletteOpen}
                className="oa-icon-button hidden md:inline-flex"
              >
                {paletteOpen ? <PanelLeftClose className="h-4 w-4" /> : <PanelLeftOpen className="h-4 w-4" />}
              </button>
              <button
                onClick={() => setPanelOpen((v) => !v)}
                aria-label={panelOpen ? "Hide node detail" : "Show node detail"}
                aria-pressed={panelOpen}
                className="oa-icon-button hidden xl:inline-flex"
              >
                {panelOpen ? <PanelRightClose className="h-4 w-4" /> : <PanelRightOpen className="h-4 w-4" />}
              </button>
              <button
                onClick={() => {
                  editor.select(null);
                  setPanelOpen(true);
                  setRightTab("node");
                }}
                aria-label="Workflow settings"
                title="Workflow settings and variables"
                className="oa-icon-button hidden xl:inline-flex"
              >
                <Settings className="h-4 w-4" />
              </button>

              <span className="mx-0.5 hidden h-5 w-px bg-border sm:block" aria-hidden />

              <button
                onClick={editor.undo}
                disabled={!editor.canUndo || !canEdit}
                aria-label="Undo (Ctrl+Z)"
                className="oa-icon-button"
              >
                <Undo2 className="h-4 w-4" />
              </button>
              <button
                onClick={editor.redo}
                disabled={!editor.canRedo || !canEdit}
                aria-label="Redo (Ctrl+Y)"
                className="oa-icon-button"
              >
                <Redo2 className="h-4 w-4" />
              </button>

              <Button
                size="sm"
                variant="ghost"
                onClick={() => setKoroOpen(true)}
                title="AI Assistant — build and edit with a local model"
                className="hidden sm:inline-flex"
              >
                <Sparkles className="mr-1.5 h-3.5 w-3.5 text-primary" />
                AI Assistant
              </Button>

              <button
                onClick={() =>
                  downloadEnvelope(
                    {
                      name: workflow.name,
                      slug: workflow.slug,
                      description: workflow.description,
                      tags,
                    },
                    editor.definition,
                  )
                }
                aria-label="Export workflow as portable envelope JSON"
                className="oa-icon-button hidden sm:inline-flex"
              >
                <Download className="h-4 w-4" />
              </button>
              <PermissionGate permission="workflow:update">
                <button
                  onClick={() => fileRef.current?.click()}
                  aria-label="Import definition from JSON"
                  className="oa-icon-button hidden sm:inline-flex"
                >
                  <Upload className="h-4 w-4" />
                </button>
                <input
                  ref={fileRef}
                  type="file"
                  accept="application/json,.json"
                  className="hidden"
                  aria-hidden
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) onImportFile(f);
                    e.target.value = "";
                  }}
                />
              </PermissionGate>

              <PermissionGate permission="workflow:update">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => doSave()}
                  loading={saving}
                  disabled={!canEdit}
                >
                  <Save className="mr-1.5 h-3.5 w-3.5" />
                  Save
                </Button>
                <ActiveToggle
                  active={isActive}
                  busy={mutations.publish.isPending || mutations.unpublish.isPending}
                  disabled={!canEdit}
                  onToggle={() => (isActive ? doUnpublish() : doPublish())}
                />
              </PermissionGate>

              <PermissionGate permission="workflow:execute">
                <Button
                  size="sm"
                  onClick={() => doRun()}
                  loading={mutations.execute.isPending}
                  disabled={!canEdit}
                  title="Run the current canvas with the local execution engine"
                >
                  <Play className="mr-1.5 h-3.5 w-3.5" />
                  Test workflow
                </Button>
              </PermissionGate>
            </div>
          </header>

          {/* Banners */}
          {draftNotice && (
            <div
              className="flex shrink-0 flex-wrap items-center gap-2 border-b border-amber-500/40 bg-amber-500/10 px-3 py-1.5 text-xs"
              role="alert"
            >
              <p className="flex-1">
                An autosaved draft from your last session differs from the saved version.
              </p>
              <Button
                size="sm"
                variant="outline"
                className="h-7"
                onClick={() => {
                  editor.clearDraft();
                  if (baseline) editor.load(baseline);
                  setDraftNotice(false);
                }}
              >
                Discard draft
              </Button>
              <Button size="sm" className="h-7" onClick={() => setDraftNotice(false)}>
                Keep editing draft
              </Button>
            </div>
          )}

          {!canEdit && (
            <p
              className="shrink-0 border-b border-border bg-muted/40 px-3 py-1.5 text-xs text-muted-foreground"
              role="note"
            >
              This workflow is archived — read-only. Duplicate it to keep iterating.
            </p>
          )}

          {/* ---------------------------------------------------------------- */}
          {/* Workspace                                                         */}
          {/* ---------------------------------------------------------------- */}
          {view === "executions" ? (
            <div className="min-h-0 flex-1">
              <ExecutionLog workflowId={workflow.id} />
            </div>
          ) : (
            <div className="flex min-h-0 flex-1">
              {paletteOpen && (
                <aside
                  aria-label="Node library"
                  className="hidden w-[258px] shrink-0 border-r border-border md:block"
                >
                  <NodePalette editor={editor} disabled={!canEdit} />
                </aside>
              )}

              <div className="relative min-h-0 min-w-0 flex-1">
                <WorkflowCanvas
                  editor={editor}
                  readOnly={!canEdit}
                  runStatus={runStatus}
                  onAddNode={(request) => setCreator(request)}
                />

                {runOpen && (
                  <div className="absolute inset-x-0 bottom-0 z-30 h-[min(46%,340px)] shadow-[0_-12px_40px_-16px_rgb(15_23_42/0.35)]">
                    <RunPanel
                      result={runResult}
                      running={mutations.execute.isPending}
                      error={runError}
                      onClose={() => setRunOpen(false)}
                      onRerun={(input) => doRun(input)}
                      onSelectNode={(nodeId) =>
                        editor.select({ kind: "node", id: nodeId })
                      }
                    />
                  </div>
                )}
              </div>

              {panelOpen && (
                <aside
                  aria-label="Node detail"
                  className="hidden w-[340px] shrink-0 flex-col border-l border-border bg-card xl:flex"
                >
                  <div
                    role="tablist"
                    aria-label="Detail panel"
                    className="flex shrink-0 items-center gap-0.5 border-b border-border px-2 py-1.5"
                  >
                    <button
                      role="tab"
                      aria-selected={rightTab === "node"}
                      data-active={rightTab === "node"}
                      onClick={() => setRightTab("node")}
                      className="oa-studio-tab h-7"
                    >
                      Node
                    </button>
                    <button
                      role="tab"
                      aria-selected={rightTab === "issues"}
                      data-active={rightTab === "issues"}
                      onClick={() => setRightTab("issues")}
                      className="oa-studio-tab h-7"
                    >
                      <ListChecks className="h-3.5 w-3.5" aria-hidden />
                      Checks
                      <span
                        className={cn(
                          "ml-0.5 rounded-full px-1.5 text-[10px] font-semibold tabular-nums",
                          issueCount === 0
                            ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                            : "bg-destructive/15 text-destructive",
                        )}
                      >
                        {issueCount}
                      </span>
                    </button>
                    <button
                      onClick={() => setPanelOpen(false)}
                      aria-label="Close node detail"
                      className="oa-icon-button ml-auto"
                    >
                      <X className="h-4 w-4" />
                    </button>
                  </div>
                  <div className="min-h-0 flex-1">
                    {rightTab === "node" ? (
                      <Inspector
                        editor={editor}
                        workflowId={workflow.id}
                        disabled={!canEdit}
                        onTestStep={(nodeId, input) =>
                          mutations.executeNode.mutateAsync({
                            id: workflow.id,
                            node_id: nodeId,
                            definition: editor.definition,
                            input,
                          })
                        }
                      />
                    ) : (
                      <ValidationPanel editor={editor} />
                    )}
                  </div>
                </aside>
              )}
            </div>
          )}

          {/* Studio status bar */}
          <footer className="flex h-7 shrink-0 items-center gap-3 border-t border-border bg-card px-3 text-[11px] text-muted-foreground">
            <span className="inline-flex items-center gap-1">
              {editor.validation.valid ? (
                <>
                  <CheckCircle2 className="h-3 w-3 text-emerald-500" aria-hidden /> Valid
                </>
              ) : (
                <>
                  <XCircle className="h-3 w-3 text-destructive" aria-hidden />
                  {editor.validation.errors.length} error
                  {editor.validation.errors.length === 1 ? "" : "s"}
                </>
              )}
            </span>
            <span className="hidden sm:inline">
              Ctrl+S save · Ctrl+Z undo · F fit · Shift+drag box select
            </span>
            <span className="ml-auto hidden items-center gap-3 sm:flex">
              <span>
                {editor.definition.triggers.length} trigger
                {editor.definition.triggers.length === 1 ? "" : "s"}
              </span>
              <span>
                {editor.definition.nodes.length} node
                {editor.definition.nodes.length === 1 ? "" : "s"}
              </span>
              <span>v{workflow.latest_version?.version ?? "—"}</span>
            </span>
          </footer>
        </div>
      </Layout>

      <NodeCreator
        open={creator !== null}
        request={creator}
        editor={editor}
        disabled={!canEdit}
        onClose={() => setCreator(null)}
      />

      <KoroPanel
        open={koroOpen}
        onClose={() => setKoroOpen(false)}
        workflowId={workflow.id}
        workflowName={workflow.name}
        currentDefinition={editor.definition}
        onApply={(definition, generatedName) => {
          editor.replace(definition);
          if (generatedName) setName(generatedName);
          setPanelOpen(true);
          setRightTab("issues");
          toast({
            kind: "success",
            title: "Applied to canvas",
            description: "Review the changes, then save or test. Ctrl+Z undoes it.",
          });
        }}
        onResult={(result) => {
          const action = result.actions[0];
          if (action?.status === "ok" && action.type !== "none") {
            toast({ kind: "success", title: "Assistant", description: action.detail });
          }
          if (result.workflow_id && result.workflow_id !== workflow.id) {
            router.push(`/workflows/${result.workflow_id}/edit`);
            return;
          }
          if (["run", "publish", "unpublish", "edit"].includes(action?.type ?? "")) refetch();
        }}
      />

      <Dialog
        open={conflict}
        onClose={() => setConflict(false)}
        title="Workflow changed elsewhere"
        description="Another client saved this workflow after you opened it. Your local draft is intact."
        footer={
          <>
            <Button
              variant="outline"
              onClick={() => {
                setConflict(false);
                editor.clearDraft();
                refetch();
              }}
            >
              Reload server version
            </Button>
            <Button onClick={() => setConflict(false)}>Keep local draft</Button>
          </>
        }
      >
        <p className="text-sm text-muted-foreground">
          Saving again will overwrite the newer server state. Reload to compare,
          or keep editing — your autosaved draft is preserved either way.
        </p>
      </Dialog>
    </Protected>
  );
}

function ActiveToggle({
  active,
  busy,
  disabled,
  onToggle,
}: {
  active: boolean;
  busy: boolean;
  disabled?: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={active}
      aria-label={active ? "Deactivate workflow" : "Activate workflow"}
      onClick={onToggle}
      disabled={disabled || busy}
      title={
        active
          ? "Active — triggers are live. Click to deactivate."
          : "Inactive — click to activate and enable triggers."
      }
      className={cn(
        "inline-flex h-8 items-center gap-1.5 rounded-full border px-2.5 text-xs font-medium transition-colors disabled:pointer-events-none disabled:opacity-50",
        active
          ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
          : "border-border bg-muted/40 text-muted-foreground hover:text-foreground",
      )}
    >
      <span
        className={cn(
          "flex h-3.5 w-6 items-center rounded-full p-0.5 transition-colors",
          active ? "bg-emerald-500" : "bg-muted-foreground/30",
        )}
        aria-hidden
      >
        <span
          className={cn(
            "block h-2.5 w-2.5 rounded-full bg-white shadow-sm transition-transform",
            active && "translate-x-2.5",
          )}
        />
      </span>
      {busy ? "…" : active ? "Active" : "Inactive"}
    </button>
  );
}

function SaveStatus({
  online,
  saving,
  failed,
  dirty,
  onRetry,
}: {
  online: boolean;
  saving: boolean;
  failed: boolean;
  dirty: boolean;
  onRetry: () => void;
}) {
  if (!online) {
    return (
      <span
        className="hidden rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground sm:inline"
        role="status"
      >
        Offline — kept locally
      </span>
    );
  }
  if (saving) {
    return (
      <span
        className="hidden text-[11px] text-muted-foreground sm:inline"
        role="status"
        aria-live="polite"
      >
        Saving…
      </span>
    );
  }
  if (failed) {
    return (
      <span
        className="hidden items-center gap-1 text-[11px] text-destructive sm:flex"
        role="alert"
      >
        Save failed
        <button
          onClick={onRetry}
          className="font-medium underline hover:no-underline"
        >
          Retry
        </button>
      </span>
    );
  }
  return (
    <span
      className={cn(
        "hidden text-[11px] sm:inline",
        dirty ? "text-amber-600 dark:text-amber-400" : "text-muted-foreground",
      )}
      aria-live="polite"
      role="status"
    >
      {dirty ? "● Unsaved" : "Saved"}
    </span>
  );
}

function TagEditor({
  tags,
  onChange,
  disabled,
}: {
  tags: string[];
  onChange: (tags: string[]) => void;
  disabled?: boolean;
}) {
  const [draft, setDraft] = React.useState("");
  const add = () => {
    const t = draft.trim().toLowerCase().replace(/\s+/g, "-").slice(0, 50);
    if (t && !tags.includes(t) && tags.length < 20) onChange([...tags, t]);
    setDraft("");
  };
  return (
    <span
      className="flex flex-wrap items-center gap-1"
      aria-label="Workflow tags"
    >
      {tags.map((t) => (
        <span
          key={t}
          className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-[11px]"
        >
          {t}
          {!disabled && (
            <button
              aria-label={`Remove tag ${t}`}
              onClick={() => onChange(tags.filter((x) => x !== t))}
              className="text-muted-foreground hover:text-foreground"
            >
              ×
            </button>
          )}
        </span>
      ))}
      {!disabled && (
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add();
            }
          }}
          onBlur={add}
          placeholder={tags.length === 0 ? "Add tags…" : "+"}
          aria-label="Add a tag"
          className="h-6 w-20 rounded-full border border-dashed bg-transparent px-2 text-[11px] outline-none placeholder:text-muted-foreground focus:border-primary"
        />
      )}
    </span>
  );
}
