"use client";

/**
 * Reliability metrics, counted up with GSAP from the real eval response.
 *
 * The decoy false-action rate is deliberately the largest thing on the page:
 * it is the core reliability claim - five planted look-alikes that the agent
 * must leave alone - so it gets the visual weight rather than being one cell in
 * a uniform grid.
 *
 * If the run was invalid (a candidate never got a verdict) the panel refuses to
 * present detection numbers as a quality measurement and says why instead.
 */
import { useEffect, useRef } from "react";
import gsap from "gsap";
import { ShieldCheck, ShieldAlert, Activity } from "lucide-react";

import { cn } from "@/lib/utils";
import type { Metrics } from "@/lib/types";

function CountUp({
  value,
  suffix = "",
  decimals = 0,
  className,
}: {
  value: number;
  suffix?: string;
  decimals?: number;
  className?: string;
}) {
  const ref = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const state = { n: 0 };
    const tween = gsap.to(state, {
      n: value,
      duration: 1.1,
      ease: "power2.out",
      onUpdate: () => {
        el.textContent = `${state.n.toFixed(decimals)}${suffix}`;
      },
    });
    return () => {
      tween.kill();
    };
  }, [value, suffix, decimals]);

  return (
    <span ref={ref} className={cn("tabular", className)}>
      0{suffix}
    </span>
  );
}

function Stat({
  label,
  value,
  hint,
  tone = "neutral",
}: {
  label: string;
  value: React.ReactNode;
  hint?: string;
  tone?: "neutral" | "ok" | "warn";
}) {
  return (
    <div className="sunken rounded-lg border p-3">
      <p className="text-[11px] font-medium text-dim">{label}</p>
      <div
        className={cn(
          "mt-1 text-2xl font-semibold leading-none",
          tone === "ok" && "text-[var(--color-ok)]",
          tone === "warn" && "text-[var(--color-warn)]",
        )}
      >
        {value}
      </div>
      {hint && <p className="mt-1 text-[11px] text-faint">{hint}</p>}
    </div>
  );
}

export function MetricsPanel({
  metrics,
  loading,
}: {
  metrics: Metrics | null;
  loading: boolean;
}) {
  if (loading) {
    return (
      <div className="surface rounded-xl p-5" aria-busy="true">
        <div className="sunken mb-3 h-4 w-40 animate-pulse rounded" />
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="sunken h-[86px] animate-pulse rounded-lg" />
          ))}
        </div>
      </div>
    );
  }

  if (!metrics) {
    return (
      <div className="surface rounded-xl p-6 text-center">
        <Activity className="mx-auto h-5 w-5 text-faint" />
        <p className="mt-2 text-sm font-medium">No eval has run yet</p>
        <p className="mx-auto mt-1 max-w-sm text-xs text-dim">
          Run an eval to score the agent against the 25 planted scenarios — 20
          real discrepancies and 5 decoys it must leave untouched. Press{" "}
          <kbd className="rounded border px-1 py-0.5 text-[10px]">⌘K</kbd> to
          start one.
        </p>
      </div>
    );
  }

  const decoysClean = metrics.decoys_acted === 0;

  return (
    <div className="surface rounded-xl p-5">
      <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold tracking-tight">Reliability</h2>
        <span className="text-[11px] text-faint">
          scored against seed/scenarios.md · {metrics.real_total} real + {metrics.decoy_total} decoys
        </span>
      </div>

      {!metrics.valid && (
        <div
          role="alert"
          className="mb-4 rounded-lg border border-[var(--color-bad)]/50 bg-[var(--color-bad)]/10 p-3"
        >
          <p className="text-xs font-semibold text-[var(--color-bad)]">
            Run invalid — these are not quality numbers
          </p>
          <p className="mt-1 text-[11px] text-dim">{metrics.invalid_reason}</p>
        </div>
      )}

      {/* The headline claim. */}
      <div
        className={cn(
          "mb-4 flex items-center gap-4 rounded-xl border p-4 sm:p-5",
          decoysClean
            ? "border-[var(--color-ok)]/45 bg-[var(--color-ok)]/10"
            : "border-[var(--color-bad)]/50 bg-[var(--color-bad)]/10",
        )}
      >
        {decoysClean ? (
          <ShieldCheck className="h-8 w-8 shrink-0 text-[var(--color-ok)]" />
        ) : (
          <ShieldAlert className="h-8 w-8 shrink-0 text-[var(--color-bad)]" />
        )}
        <div className="min-w-0">
          <p className="text-[11px] font-medium uppercase tracking-wide text-dim">
            Decoy false-action rate
          </p>
          <div
            className={cn(
              "text-4xl font-bold leading-none tabular sm:text-5xl",
              decoysClean ? "text-[var(--color-ok)]" : "text-[var(--color-bad)]",
            )}
          >
            <CountUp value={metrics.decoys_acted} />
            <span className="text-2xl font-semibold sm:text-3xl">
              /{metrics.decoy_total}
            </span>
          </div>
          <p className="mt-1 text-xs text-dim">
            {decoysClean
              ? "No write was made against any of the 5 planted look-alikes."
              : "The agent acted on a decoy — this must be 0."}
          </p>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat
          label="Detection recall"
          value={<CountUp value={metrics.recall * 100} suffix="%" />}
          hint={`${metrics.detected_real}/${metrics.real_total} real discrepancies found`}
          tone={metrics.recall === 1 ? "ok" : "warn"}
        />
        <Stat
          label="Precision"
          value={<CountUp value={metrics.precision * 100} suffix="%" />}
          hint={`F1 ${metrics.f1.toFixed(2)}`}
          tone={metrics.precision === 1 ? "ok" : "warn"}
        />
        <Stat
          label="Post-state correctness"
          value={<CountUp value={metrics.post_state_correctness * 100} suffix="%" />}
          hint={`${metrics.verified_ok} writes confirmed by read-back`}
          tone={metrics.post_state_correctness === 1 ? "ok" : "warn"}
        />
        <Stat
          label="Tier routing"
          value={<CountUp value={metrics.tier_accuracy * 100} suffix="%" />}
          hint="automatic vs approval vs never"
          tone={metrics.tier_accuracy === 1 ? "ok" : "warn"}
        />
      </div>

      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-[11px] sm:grid-cols-4">
        <div className="flex justify-between gap-2">
          <dt className="text-faint">judge failures</dt>
          <dd className="tabular">{metrics.judge_failures}</dd>
        </div>
        <div className="flex justify-between gap-2">
          <dt className="text-faint">verdicts fresh</dt>
          <dd className="tabular">{metrics.fresh_verdicts}</dd>
        </div>
        <div className="flex justify-between gap-2">
          <dt className="text-faint">verdicts cached</dt>
          <dd className="tabular">{metrics.cached_verdicts}</dd>
        </div>
        <div className="flex justify-between gap-2">
          <dt className="text-faint">injected fault caught</dt>
          <dd
            className={cn(
              "tabular",
              metrics.injected_subject &&
                (metrics.injected_caught
                  ? "text-[var(--color-ok)]"
                  : "text-[var(--color-bad)]"),
            )}
          >
            {metrics.injected_subject ? (metrics.injected_caught ? "yes" : "no") : "—"}
          </dd>
        </div>
      </dl>

      {metrics.missed.length > 0 && (
        <p className="mt-3 rounded-lg border border-[var(--color-warn)]/40 bg-[var(--color-warn)]/10 p-2.5 text-[11px]">
          <span className="font-medium">Missed:</span> {metrics.missed.join(", ")}
        </p>
      )}
    </div>
  );
}
