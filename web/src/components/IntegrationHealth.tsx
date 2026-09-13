"use client";

/**
 * Live integration health strip.
 *
 * Each dot is backed by a genuine request made seconds ago - GET /v1/customers
 * against Stripe, GET /crm/v3/objects/companies against HubSpot, auth.test
 * against Slack, and real network calls out to Lemma and Arga. Green never
 * means "an env var is set"; it means a request succeeded and we show the
 * status code and latency to prove it.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import gsap from "gsap";
import { RefreshCw, AlertTriangle } from "lucide-react";

import { api, ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { IntegrationsResponse } from "@/lib/types";

const REFRESH_MS = 30_000;

export function IntegrationHealth() {
  const [data, setData] = useState<IntegrationsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [checking, setChecking] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async (manual = false) => {
    if (manual) setChecking(true);
    try {
      const next = await api.integrations();
      setData(next);
      setError(null);
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : "Integration check failed",
      );
    } finally {
      setLoading(false);
      setChecking(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- async data load / DOM read on mount: state must be set after the effect runs.
    void load();
    const id = setInterval(() => void load(), REFRESH_MS);
    // The command palette can force an immediate re-check.
    const onRecheck = () => void load(true);
    window.addEventListener("rc-recheck", onRecheck);
    return () => {
      clearInterval(id);
      window.removeEventListener("rc-recheck", onRecheck);
    };
  }, [load]);

  // Animate the entrance ONCE. Re-running it on every 30s poll is visually
  // noisy, and worse: if ctx.revert() fires mid-tween it restores the pre-tween
  // state, which would strand chips at opacity 0. clearProps guarantees the
  // final DOM carries no GSAP inline styles at all.
  const animated = useRef(false);
  useEffect(() => {
    if (!data || !rootRef.current || animated.current) return;
    animated.current = true;
    gsap.fromTo(
      rootRef.current.querySelectorAll("[data-chip]"),
      { opacity: 0, y: 6 },
      {
        opacity: 1,
        y: 0,
        duration: 0.35,
        stagger: 0.05,
        ease: "power2.out",
        clearProps: "opacity,transform",
      },
    );
  }, [data]);

  if (loading) {
    return (
      <div className="surface flex flex-wrap gap-2 rounded-xl p-3" aria-busy="true">
        {Array.from({ length: 6 }).map((_, i) => (
          <div
            key={i}
            className="sunken h-[52px] w-[150px] animate-pulse rounded-lg"
          />
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <div
        role="alert"
        className="surface flex items-start gap-3 rounded-xl border-[var(--color-bad)]/40 p-4"
      >
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-bad)]" />
        <div className="min-w-0">
          <p className="text-sm font-medium">Integrations unreachable</p>
          <p className="mt-0.5 text-xs text-dim">{error}</p>
          <button
            onClick={() => void load(true)}
            className="mt-2 rounded-md border px-2.5 py-1 text-xs hover:bg-[var(--bg-sunken)]"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (!data) return null;

  const down = data.integrations.filter((i) => !i.ok).length;

  return (
    <div ref={rootRef} className="surface rounded-xl p-3">
      <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 px-1">
        <h2 className="text-xs font-semibold tracking-tight">Integration health</h2>
        <span className="text-[11px] text-faint">
          {down === 0
            ? `all ${data.integrations.length} reachable`
            : `${down} unreachable`}
        </span>
        <span className="text-[11px] text-faint">· {data.backend}</span>
        <button
          onClick={() => void load(true)}
          aria-label="Re-check integrations now"
          className="ml-auto inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[11px] hover:bg-[var(--bg-sunken)]"
        >
          <RefreshCw className={cn("h-3 w-3", checking && "animate-spin")} />
          re-check
        </button>
      </div>

      <ul className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        {data.integrations.map((it) => (
          <li
            key={it.name}
            data-chip
            title={`${it.detail}\n${it.endpoint}`}
            className={cn(
              "sunken rounded-lg border p-2.5",
              it.ok
                ? "border-[var(--color-ok)]/35"
                : "border-[var(--color-bad)]/45",
            )}
          >
            <div className="flex items-center gap-1.5">
              <span
                aria-hidden="true"
                className={cn(
                  "h-2 w-2 shrink-0 rounded-full",
                  it.ok ? "bg-[var(--color-ok)]" : "bg-[var(--color-bad)]",
                )}
              />
              <span className="truncate text-xs font-medium">{it.label}</span>
              <span className="sr-only">
                {it.ok ? "online" : "offline"} — {it.detail}
              </span>
              {it.latency_ms != null && (
                <span className="ml-auto text-[10px] text-faint tabular">
                  {it.latency_ms < 1000
                    ? `${Math.round(it.latency_ms)}ms`
                    : `${(it.latency_ms / 1000).toFixed(1)}s`}
                </span>
              )}
            </div>
            <p className="mt-1 line-clamp-2 text-[10px] leading-tight text-faint">
              {it.detail}
            </p>
          </li>
        ))}
      </ul>
    </div>
  );
}
