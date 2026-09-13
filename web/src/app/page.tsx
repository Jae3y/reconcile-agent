"use client";

/**
 * reconcile-agent dashboard.
 *
 * Every value on this page comes from a real FastAPI response describing a real
 * run. There is no fixture data and no fallback constant anywhere in this tree:
 * when the API is unreachable, the views render their error state instead of
 * inventing something plausible.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import gsap from "gsap";
import { toast } from "sonner";
import { Play, FlaskConical, Command as CommandIcon, Moon, Sun, Sprout } from "lucide-react";

import { ActivityFeed } from "@/components/ActivityFeed";
import { CommandPalette, type PaletteActions } from "@/components/CommandPalette";
import { IntegrationHealth } from "@/components/IntegrationHealth";
import { MetricsPanel } from "@/components/MetricsPanel";
import { ResultsTable, buildRows, type ResultRow } from "@/components/ResultsTable";
import { RunHistory } from "@/components/RunHistory";
import { SystemMap } from "@/components/SystemMap";
import { api, ApiError, API_BASE } from "@/lib/api";
import { useRunStream } from "@/lib/useRunStream";
import { cn } from "@/lib/utils";
import type { Metrics, RunDetail, RunEvent, RunSummary, Scenario } from "@/lib/types";

export default function Dashboard() {
  const stream = useRunStream();

  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [runsLoading, setRunsLoading] = useState(true);
  const [rows, setRows] = useState<ResultRow[]>([]);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [dark, setDark] = useState(false);
  const [apiDown, setApiDown] = useState<string | null>(null);
  const [historical, setHistorical] = useState<RunDetail | null>(null);

  const headerRef = useRef<HTMLElement>(null);

  const running = stream.phase === "connecting" || stream.phase === "streaming";

  /* ---------------------------------------------------------------- boot */
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- async data load / DOM read on mount: state must be set after the effect runs.
    setDark(document.documentElement.classList.contains("dark"));
  }, []);

  const refreshRuns = useCallback(async () => {
    try {
      const next = await api.runs(30);
      setRuns(next);
      setApiDown(null);
    } catch (cause) {
      setApiDown(cause instanceof ApiError ? cause.message : "API unreachable");
    } finally {
      setRunsLoading(false);
    }
  }, []);

  useEffect(() => {
    /* eslint-disable react-hooks/set-state-in-effect --
       Initial data fetch on mount. These setState calls happen in promise
       callbacks after the effect body returns, which is the documented pattern
       for loading from an external system; the rule flags them conservatively. */
    void api.scenarios().then(setScenarios).catch(() => undefined);
    void refreshRuns();
    /* eslint-enable react-hooks/set-state-in-effect */
  }, [refreshRuns]);

  useEffect(() => {
    const ctx = gsap.context(() => {
      gsap.from("[data-hero]", {
        opacity: 0,
        y: -10,
        duration: 0.45,
        ease: "power2.out",
      });
    }, headerRef);
    return () => ctx.revert();
  }, []);

  /* ------------------------------------------------- live event handling */
  useEffect(() => {
    stream.setOnEvent((event: RunEvent) => {
      if (event.type === "action") {
        const status = String(event.data.status ?? "");
        if (status === "applied") toast.success(event.title, { description: event.message });
        else if (status === "verify_failed")
          toast.error(event.title, { description: event.message, duration: 9000 });
      } else if (event.type === "slack") {
        toast.warning(event.title, { description: "#approvals" });
      } else if (event.type === "run.error") {
        toast.error(event.title, { description: event.message, duration: 12000 });
      } else if (event.type === "metrics") {
        const ok = event.level === "success";
        (ok ? toast.success : toast.error)(event.title, { description: event.message });
      }
    });
    return () => stream.setOnEvent(null);
  }, [stream]);

  // Rebuild the table live from whatever the stream has delivered so far.
  useEffect(() => {
    if (!running) return;
    const judged = stream.events
      .filter((e) => e.type === "judge" && typeof e.data.is_match === "boolean")
      .map((e) => ({
        subject: String(e.data.subject ?? ""),
        discrepancy_type: String(e.data.type ?? ""),
        is_match: Boolean(e.data.is_match),
        confidence: Number(e.data.confidence ?? 0),
        reasoning: e.message,
        cached: false,
      }));
    // eslint-disable-next-line react-hooks/set-state-in-effect -- async data load / DOM read on mount: state must be set after the effect runs.
    if (judged.length) setRows(buildRows(judged, []));
  }, [stream.events, running]);

  // When a run finishes, take the authoritative detail from the API.
  useEffect(() => {
    if (stream.phase !== "done" || !stream.detail) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- async data load / DOM read on mount: state must be set after the effect runs.
    setRows(buildRows(stream.detail.judged, stream.detail.actions));
    if (stream.detail.metrics) setMetrics(stream.detail.metrics);
    setSelectedRun(stream.detail.run_id);
    void refreshRuns();
  }, [stream.phase, stream.detail, refreshRuns]);

  /* ------------------------------------------------------------- actions */
  const startRun = useCallback(
    async (kind: "run" | "eval") => {
      if (busy || running) {
        toast.warning("A run is already in progress");
        return;
      }
      setBusy(true);
      setRows([]);
      setHistorical(null);
      const started = await stream.start(kind, kind === "eval" ? "Kestrel Foods" : undefined);
      setBusy(false);
      if (started) {
        setSelectedRun(started.run_id);
        toast.info(`${kind === "eval" ? "Eval" : "Run"} started`, {
          description: `run ${started.run_id} · streaming live`,
        });
        void refreshRuns();
      } else {
        toast.error("Could not start", { description: `API at ${API_BASE}` });
      }
    },
    [busy, running, stream, refreshRuns],
  );

  const reseed = useCallback(async () => {
    setBusy(true);
    try {
      const result = await api.seed();
      toast.success(`Seeded ${result.seeded} scenarios`, {
        description: Object.entries(result.counts)
          .map(([k, v]) => `${k.split(".").pop()} ${v}`)
          .join(" · "),
      });
    } catch (cause) {
      toast.error("Seed failed", {
        description: cause instanceof ApiError ? cause.message : "unknown error",
      });
    } finally {
      setBusy(false);
    }
  }, []);

  const loadRun = useCallback(async (runId: string) => {
    try {
      const detail = await api.run(runId);
      setRows(buildRows(detail.judged, detail.actions));
      setMetrics(detail.metrics);
      setSelectedRun(runId);
      // Restore the whole picture, not just the table: a historical run should
      // show the stages it went through and the events it emitted, otherwise
      // the system map misleadingly reads "waiting" for a finished run.
      setHistorical(detail);
    } catch {
      toast.error("Could not load that run");
    }
  }, []);

  const toggleTheme = useCallback(() => {
    const next = !document.documentElement.classList.contains("dark");
    document.documentElement.classList.toggle("dark", next);
    localStorage.setItem("rc-theme", next ? "dark" : "light");
    setDark(next);
  }, []);

  const paletteActions: PaletteActions = useMemo(
    () => ({
      startRun: () => void startRun("run"),
      startEval: () => void startRun("eval"),
      reseed: () => void reseed(),
      recheck: () => window.dispatchEvent(new Event("rc-recheck")),
      toggleTheme,
      focusScenario: (name) => {
        toast.info(name, {
          description:
            scenarios.find((s) => s.name === name)?.why ||
            scenarios.find((s) => s.name === name)?.category,
        });
      },
    }),
    [startRun, reseed, toggleTheme, scenarios],
  );

  // Live stream wins while a run is in flight; otherwise show the selected
  // historical run so the map and feed always describe the same run as the table.
  const shownStages = running || !historical ? stream.stages : historical.stages;
  const shownEvents = running || !historical ? stream.events : historical.events;

  /* ---------------------------------------------------------------- view */
  return (
    <div className="mx-auto w-full max-w-[1600px] px-3 py-4 sm:px-5 sm:py-6">
      <header ref={headerRef} className="mb-4">
        <div data-hero className="flex flex-wrap items-center gap-3">
          <div className="min-w-0">
            <h1 className="truncate text-base font-semibold tracking-tight sm:text-lg">
              reconcile-agent
            </h1>
            <p className="text-xs text-dim">
              Billing ↔ CRM reconciliation · five stages, one LLM judgement,
              every write verified by read-back
            </p>
          </div>

          <div className="ml-auto flex flex-wrap items-center gap-2">
            <button
              onClick={() => void startRun("run")}
              disabled={running || busy}
              className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--color-accent)]/50 bg-[var(--color-accent)]/12 px-3 py-1.5 text-xs font-medium text-[var(--color-accent)] hover:bg-[var(--color-accent)]/20 disabled:opacity-50"
            >
              <Play className="h-3.5 w-3.5" />
              Run pipeline
            </button>
            <button
              onClick={() => void startRun("eval")}
              disabled={running || busy}
              className="inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium hover:bg-[var(--bg-sunken)] disabled:opacity-50"
            >
              <FlaskConical className="h-3.5 w-3.5" />
              Run eval
            </button>
            <button
              onClick={() => void reseed()}
              disabled={running || busy}
              aria-label="Reseed a fresh twin"
              className="inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs hover:bg-[var(--bg-sunken)] disabled:opacity-50"
            >
              <Sprout className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Reseed</span>
            </button>
            <button
              onClick={toggleTheme}
              aria-label="Toggle colour theme"
              className="rounded-lg border p-1.5 hover:bg-[var(--bg-sunken)]"
            >
              {dark ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />}
            </button>
            <kbd className="hidden items-center gap-1 rounded-lg border px-2 py-1.5 text-[10px] text-faint sm:inline-flex">
              <CommandIcon className="h-3 w-3" />K
            </kbd>
          </div>
        </div>

        {apiDown && (
          <div
            role="alert"
            className="mt-3 rounded-lg border border-[var(--color-bad)]/50 bg-[var(--color-bad)]/10 p-3"
          >
            <p className="text-xs font-semibold text-[var(--color-bad)]">
              API unreachable
            </p>
            <p className="mt-0.5 text-[11px] text-dim">
              {apiDown} — expected at <code>{API_BASE}</code>
            </p>
          </div>
        )}
      </header>

      <main id="main" className="space-y-4">
        <IntegrationHealth />

        <SystemMap
          stages={shownStages}
          activeStage={stream.activeStage}
          running={running}
        />

        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
          <div className="space-y-4">
            <MetricsPanel metrics={metrics} loading={false} />
            <ResultsTable rows={rows} loading={running && rows.length === 0} />
          </div>

          <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-1">
            <div className="min-h-[280px] xl:h-[360px]">
              <ActivityFeed events={shownEvents} live={running} />
            </div>
            <div className="min-h-[280px] xl:h-[420px]">
              <RunHistory
                runs={runs}
                loading={runsLoading}
                selectedId={selectedRun}
                onSelect={(id) => void loadRun(id)}
              />
            </div>
          </div>
        </div>

        <footer className="pb-6 pt-2 text-center text-[11px] text-faint">
          <span className={cn(running && "text-[var(--color-accent)]")}>
            {running ? "streaming live over SSE" : "idle"}
          </span>
          {" · "}API <code>{API_BASE}</code>
          {" · "}press{" "}
          <kbd className="rounded border px-1 py-0.5">⌘K</kbd> for commands
        </footer>
      </main>

      <CommandPalette scenarios={scenarios} actions={paletteActions} dark={dark} />
    </div>
  );
}
