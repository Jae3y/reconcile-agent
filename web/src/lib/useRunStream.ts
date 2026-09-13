"use client";

/**
 * Server-Sent Events transport for a live run.
 *
 * Not polling: the browser holds one connection and FastAPI pushes each phase
 * as it happens. The stream replays from seq 0 on connect, so a judge who opens
 * the dashboard halfway through a run still sees every earlier stage before
 * tailing live. If the connection drops we reconnect from the last seq seen
 * rather than from the beginning, so nothing is duplicated or lost.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "./api";
import type { RunDetail, RunEvent, RunSummary, StageName, StageState } from "./types";
import { STAGE_ORDER } from "./types";

export type StreamPhase = "idle" | "connecting" | "streaming" | "done" | "error";

export interface RunStreamState {
  phase: StreamPhase;
  events: RunEvent[];
  stages: StageState[];
  summary: RunSummary | null;
  detail: RunDetail | null;
  error: string | null;
}

function blankStages(): StageState[] {
  return STAGE_ORDER.map((name) => ({
    name,
    status: "pending",
    detail: "",
    count: null,
    seconds: null,
  }));
}

function applyEvent(stages: StageState[], event: RunEvent): StageState[] {
  if (!event.stage) return stages;
  const status = event.data?.status;
  const count = event.data?.count;
  return stages.map((stage) => {
    if (stage.name !== event.stage) return stage;
    return {
      ...stage,
      status:
        typeof status === "string" &&
        ["pending", "active", "done", "error"].includes(status)
          ? (status as StageState["status"])
          : stage.status === "pending"
            ? "active"
            : stage.status,
      detail: event.message || stage.detail,
      count: typeof count === "number" ? count : stage.count,
    };
  });
}

export function useRunStream() {
  const [state, setState] = useState<RunStreamState>({
    phase: "idle",
    events: [],
    stages: blankStages(),
    summary: null,
    detail: null,
    error: null,
  });

  const sourceRef = useRef<EventSource | null>(null);
  const seqRef = useRef(0);
  // runId is rendered, so it lives in state - reading a ref during render
  // is not guaranteed to re-render when it changes.
  const [runId, setRunId] = useState<string | null>(null);
  const onEventRef = useRef<((event: RunEvent) => void) | null>(null);

  const setOnEvent = useCallback((fn: ((event: RunEvent) => void) | null) => {
    onEventRef.current = fn;
  }, []);

  const close = useCallback(() => {
    sourceRef.current?.close();
    sourceRef.current = null;
  }, []);

  const connect = useCallback(
    (runId: string) => {
      close();
      setRunId(runId);

      const source = new EventSource(api.streamUrl(runId, seqRef.current));
      sourceRef.current = source;

      source.addEventListener("run", (raw) => {
        const event = JSON.parse((raw as MessageEvent<string>).data) as RunEvent;
        seqRef.current = event.seq;
        onEventRef.current?.(event);
        setState((prev) => ({
          ...prev,
          phase: "streaming",
          events: [...prev.events, event],
          stages: applyEvent(prev.stages, event),
        }));
      });

      source.addEventListener("done", (raw) => {
        const summary = JSON.parse(
          (raw as MessageEvent<string>).data,
        ) as RunSummary;
        close();
        setState((prev) => ({
          ...prev,
          phase: "done",
          summary,
          stages: prev.stages.map((s) =>
            s.status === "active" ? { ...s, status: "done" } : s,
          ),
        }));
        void api
          .run(summary.run_id)
          .then((detail) => setState((prev) => ({ ...prev, detail })))
          .catch(() => undefined);
      });

      source.onerror = () => {
        // EventSource retries on its own; surface it only if we never opened.
        if (source.readyState === EventSource.CLOSED) {
          setState((prev) =>
            prev.phase === "done"
              ? prev
              : { ...prev, phase: "error", error: "Live stream disconnected" },
          );
        }
      };
    },
    [close],
  );

  const start = useCallback(
    async (kind: "run" | "eval", injectFault?: string) => {
      seqRef.current = 0;
      setState({
        phase: "connecting",
        events: [],
        stages: blankStages(),
        summary: null,
        detail: null,
        error: null,
      });
      try {
        const started =
          kind === "eval"
            ? await api.startEval(injectFault)
            : await api.startRun(injectFault);
        connect(started.run_id);
        return started;
      } catch (cause) {
        const message =
          cause instanceof Error ? cause.message : "Could not start the run";
        setState((prev) => ({ ...prev, phase: "error", error: message }));
        return null;
      }
    },
    [connect],
  );

  useEffect(() => close, [close]);

  const activeStage: StageName | null =
    state.stages.find((s) => s.status === "active")?.name ?? null;

  return { ...state, activeStage, start, setOnEvent, runId };
}
