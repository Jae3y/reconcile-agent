"use client";

/**
 * The five-stage pipeline as a live node graph.
 *
 * Every node's state comes from the SSE stream, so what you see here is the run
 * actually happening, not a scripted animation. GSAP drives the reveal timeline
 * and the per-node state transitions; the travelling dashes on an edge mean
 * data is moving through that stage right now.
 *
 * (Spline was specced as an option for this view. No Spline MCP server exists in
 * this environment, so this is a bespoke 2D graph instead - which also keeps the
 * first paint fast and removes a heavy runtime dependency.)
 */
import { useEffect, useRef } from "react";
import gsap from "gsap";
import { Database, GitCompare, Scale, Route, PencilLine } from "lucide-react";

import { cn } from "@/lib/utils";
import type { StageName, StageState } from "@/lib/types";
import { STAGE_LABEL, STAGE_ORDER, STAGE_SUBTITLE } from "@/lib/types";

const ICONS: Record<StageName, React.ComponentType<{ className?: string }>> = {
  ingest: Database,
  candidates: GitCompare,
  judge: Scale,
  policy: Route,
  execute: PencilLine,
};

interface Props {
  stages: StageState[];
  activeStage: StageName | null;
  running: boolean;
}

export function SystemMap({ stages, activeStage, running }: Props) {
  const rootRef = useRef<HTMLDivElement>(null);
  const byName = new Map(stages.map((s) => [s.name, s]));

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    const ctx = gsap.context(() => {
      gsap.from("[data-node]", {
        opacity: 0,
        y: 18,
        scale: 0.96,
        duration: 0.5,
        stagger: 0.08,
        ease: "power3.out",
      });
      gsap.from("[data-edge]", {
        opacity: 0,
        duration: 0.4,
        stagger: 0.08,
        delay: 0.2,
        ease: "power2.out",
      });
    }, root);
    return () => ctx.revert();
  }, []);

  // Pop a node the moment it becomes active.
  useEffect(() => {
    if (!activeStage || !rootRef.current) return;
    const el = rootRef.current.querySelector(`[data-node="${activeStage}"]`);
    if (el) {
      gsap.fromTo(
        el,
        { scale: 1 },
        { scale: 1.04, duration: 0.22, yoyo: true, repeat: 1, ease: "power2.out" },
      );
    }
  }, [activeStage]);

  return (
    <div
      ref={rootRef}
      className="surface rounded-xl p-5 sm:p-6"
      aria-label="Pipeline system map"
    >
      <div className="mb-5 flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold tracking-tight">System map</h2>
          <p className="text-xs text-dim">
            Five deterministic stages · one LLM judgement in the middle
          </p>
        </div>
        <span
          className={cn(
            "rounded-full px-2.5 py-1 text-[11px] font-medium tabular",
            running
              ? "bg-[var(--color-accent)]/15 text-[var(--color-accent)]"
              : "sunken text-faint",
          )}
        >
          {running ? "live · streaming" : "idle"}
        </span>
      </div>

      <ol className="flex flex-col gap-3 lg:flex-row lg:items-stretch lg:gap-0">
        {STAGE_ORDER.map((name, index) => {
          const stage = byName.get(name);
          const status = stage?.status ?? "pending";
          const Icon = ICONS[name];
          const isActive = status === "active";
          const isDone = status === "done";
          const isError = status === "error";

          return (
            <li key={name} className="flex flex-1 items-stretch gap-0">
              <div
                data-node={name}
                className={cn(
                  "flex w-full flex-col rounded-lg border p-3 transition-colors duration-300",
                  isActive &&
                    "node-active border-[var(--color-accent)] bg-[var(--color-accent)]/8",
                  isDone && "border-[var(--color-ok)]/45 bg-[var(--color-ok)]/8",
                  isError && "border-[var(--color-bad)]/50 bg-[var(--color-bad)]/8",
                  !isActive && !isDone && !isError && "sunken border-[var(--border)]",
                )}
              >
                <div className="flex items-center gap-2">
                  <Icon
                    className={cn(
                      "h-4 w-4 shrink-0",
                      isActive && "text-[var(--color-accent)]",
                      isDone && "text-[var(--color-ok)]",
                      isError && "text-[var(--color-bad)]",
                      status === "pending" && "text-faint",
                    )}
                  />
                  <span className="truncate text-xs font-semibold">
                    {STAGE_LABEL[name]}
                  </span>
                  <span className="ml-auto text-[10px] text-faint tabular">
                    {index + 1}/5
                  </span>
                </div>

                <p className="mt-1 text-[11px] leading-tight text-faint">
                  {STAGE_SUBTITLE[name]}
                </p>

                <div className="mt-2 min-h-[2.25rem]">
                  {stage?.count != null && (
                    <div className="text-lg font-semibold leading-none tabular">
                      {stage.count}
                    </div>
                  )}
                  {stage?.detail && (
                    <p className="mt-1 line-clamp-2 text-[11px] leading-tight text-dim">
                      {stage.detail}
                    </p>
                  )}
                  {!stage?.detail && status === "pending" && (
                    <p className="mt-1 text-[11px] text-faint">waiting…</p>
                  )}
                </div>

                {stage?.seconds != null && (
                  <p className="mt-1 text-[10px] text-faint tabular">
                    {stage.seconds}s
                  </p>
                )}
              </div>

              {index < STAGE_ORDER.length - 1 && (
                <div
                  data-edge
                  className="hidden w-6 shrink-0 items-center justify-center lg:flex"
                  aria-hidden="true"
                >
                  <svg width="24" height="10" viewBox="0 0 24 10" fill="none">
                    <line
                      x1="0"
                      y1="5"
                      x2="24"
                      y2="5"
                      stroke={
                        isDone ? "var(--color-ok)" : isActive ? "var(--color-accent)" : "var(--border)"
                      }
                      strokeWidth="2"
                      className={cn(isActive && "edge-live")}
                    />
                  </svg>
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
