"use client";

import * as React from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { Layout } from "@/components/layout";
import { Protected } from "@/components/protected";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { StatusBadge } from "@/components/ui/status";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
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
import { WorkflowCanvas } from "@/components/workflows/WorkflowCanvas";
import { NodePalette } from "@/components/workflows/NodePalette";
import { Inspector } from "@/components/workflows/Inspector";
import { ValidationPanel } from "@/components/workflows/ValidationPanel";
import { RunPanel } from "@/components/workflows/RunPanel";
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
  Rocket,
  Play,
  Sparkles,
  Undo2,
  Redo2,
  Download,
  Upload,
  CheckCircle2,
  XCircle,
} from "lucide-react";
import { cn } from "@/lib/utils";

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
  const runStatus = React.useMemo(() => {
    const map: Record<string, string> = {};
    for (const run of runResult?.node_runs ?? []) map[run.node_id] = run.status;
    return map;
  }, [runResult]);

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
      toast({ kind: "success", title: "Published" });
    } catch (e) {
      toast({
        kind: "error",
        title: "Publish failed",
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

  return (
    <Protected>
      <Layout>
        <div className="oa-page max-w-none">
          {/* toolbar */}
          <div className="oa-panel sticky top-[68px] z-20 flex flex-wrap items-center gap-2 bg-card/90 px-3 py-2.5 backdrop-blur-xl">
            <Link
              href={`/workflows/${workflow.id}`}
              aria-label="Back to workflow detail"
              className="rounded-md p-2 hover:bg-accent"
            >
              <ArrowLeft className="h-4 w-4" />
            </Link>
            <div className="w-56 shrink-0">
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                disabled={!canEdit}
                aria-label="Workflow name"
                className="h-9 border-transparent bg-transparent px-2 text-base font-semibold hover:border-border focus:border-primary"
              />
            </div>
            <StatusBadge status={workflow.status} />
            <SaveStatus
              online={online}
              saving={saving || saveState === "saving"}
              failed={saveState === "failed"}
              dirty={editor.dirty}
              onRetry={() => doSave()}
            />
            <TagEditor tags={tags} onChange={setTags} disabled={!canEdit} />
            <span
              className="flex items-center gap-1 text-xs"
              aria-live="polite"
            >
              {editor.validation.valid ? (
                <>
                  <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />{" "}
                  Valid
                </>
              ) : (
                <>
                  <XCircle className="h-3.5 w-3.5 text-destructive" />{" "}
                  {editor.validation.errors.length} error
                  {editor.validation.errors.length === 1 ? "" : "s"}
                </>
              )}
            </span>
            <span className="ml-auto flex flex-wrap items-center gap-1.5">
              <Button
                size="sm"
                variant="outline"
                onClick={() => setKoroOpen(true)}
                title="Koro — local automation copilot"
              >
                <Sparkles className="mr-1.5 h-3.5 w-3.5 text-primary" />
                Koro
              </Button>
              <button
                onClick={editor.undo}
                disabled={!editor.canUndo || !canEdit}
                aria-label="Undo (Ctrl+Z)"
                className="rounded-md p-2 hover:bg-accent disabled:opacity-40"
              >
                <Undo2 className="h-4 w-4" />
              </button>
              <button
                onClick={editor.redo}
                disabled={!editor.canRedo || !canEdit}
                aria-label="Redo (Ctrl+Y)"
                className="rounded-md p-2 hover:bg-accent disabled:opacity-40"
              >
                <Redo2 className="h-4 w-4" />
              </button>
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
                className="rounded-md p-2 hover:bg-accent"
              >
                <Download className="h-4 w-4" />
              </button>
              <PermissionGate permission="workflow:update">
                <button
                  onClick={() => fileRef.current?.click()}
                  aria-label="Import definition from JSON"
                  className="rounded-md p-2 hover:bg-accent"
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
                <Button
                  size="sm"
                  variant="outline"
                  onClick={doPublish}
                  loading={mutations.publish.isPending}
                  disabled={!canEdit}
                >
                  <Rocket className="mr-1.5 h-3.5 w-3.5" />
                  Publish
                </Button>
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
            </span>
          </div>

          {draftNotice && (
            <div
              className="flex flex-wrap items-center gap-3 rounded-lg border border-amber-500/40 bg-amber-500/5 p-3 text-sm"
              role="alert"
            >
              <p className="flex-1">
                An autosaved draft from your last session differs from the saved
                version.
              </p>
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  editor.clearDraft();
                  if (baseline) editor.load(baseline);
                  setDraftNotice(false);
                }}
              >
                Discard draft
              </Button>
              <Button size="sm" onClick={() => setDraftNotice(false)}>
                Keep editing draft
              </Button>
            </div>
          )}

          {!canEdit && (
            <p
              className="rounded-lg border p-3 text-sm text-muted-foreground"
              role="note"
            >
              This workflow is archived — read-only. Duplicate it to keep
              iterating.
            </p>
          )}

          {/* editor grid — full-height studio on wide screens */}
          <div className="grid gap-3 lg:grid-cols-[230px_minmax(0,1fr)] xl:h-[calc(100dvh-19.5rem)] xl:min-h-[560px] xl:grid-cols-[248px_minmax(0,1fr)_336px] xl:grid-rows-[minmax(0,1fr)]">
            <div className="hidden h-[540px] min-h-0 lg:block xl:h-auto">
              <NodePalette editor={editor} disabled={!canEdit} />
            </div>
            <div className="h-[540px] min-h-0 xl:h-auto">
              <WorkflowCanvas
                editor={editor}
                readOnly={!canEdit}
                runStatus={runStatus}
              />
            </div>
            <div className="grid min-h-0 gap-3 lg:col-span-2 lg:grid-cols-2 xl:col-span-1 xl:h-full xl:grid-cols-1 xl:grid-rows-[minmax(0,1fr)_240px]">
              <div className="h-[320px] min-h-0 xl:h-auto">
                <Inspector
                  editor={editor}
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
              </div>
              <div className="h-[320px] min-h-0 xl:h-[240px]">
                <ValidationPanel editor={editor} />
              </div>
            </div>
          </div>

          <KoroPanel
            open={koroOpen}
            onClose={() => setKoroOpen(false)}
            workflowId={workflow.id}
            workflowName={workflow.name}
            onResult={(result) => {
              const action = result.actions[0];
              if (action?.status === "ok" && action.type !== "none") {
                toast({ kind: "success", title: "Koro", description: action.detail });
              }
              if (result.workflow_id && result.workflow_id !== workflow.id) {
                router.push(`/workflows/${result.workflow_id}/edit`);
                return;
              }
              if (["run", "publish", "unpublish"].includes(action?.type ?? "")) refetch();
            }}
          />

          {runOpen && (
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
          )}

          <p className="oa-caption">
            Shortcuts: Del removes · Ctrl+Z / Ctrl+Y undo/redo · Ctrl+C/V/D
            copy/paste/duplicate · Ctrl+S save · arrows nudge · Shift+drag
            box-select · F fit · Esc cancels. Version{" "}
            {workflow.latest_version?.version ?? "—"} · every save snapshots a
            new immutable version.
          </p>
        </div>
      </Layout>

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
        className="rounded-full bg-muted px-2.5 py-1 text-xs text-muted-foreground"
        role="status"
      >
        Offline — kept locally
      </span>
    );
  }
  if (saving) {
    return (
      <span
        className="text-xs text-muted-foreground"
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
        className="flex items-center gap-1.5 text-xs text-destructive"
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
        "text-xs",
        dirty ? "text-amber-600 dark:text-amber-400" : "text-muted-foreground",
      )}
      aria-live="polite"
      role="status"
    >
      {dirty ? "● Unsaved changes" : "Saved ✓"}
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
          className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-xs"
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
          className="h-6 w-24 rounded-full border border-dashed bg-transparent px-2 text-xs outline-none placeholder:text-muted-foreground focus:border-primary"
        />
      )}
    </span>
  );
}
