"use client";

/**
 * Live results table: one row per scenario the agent reasoned about.
 *
 * Rows expand with a GSAP height tween to reveal the judge's actual reasoning
 * text - the verbatim string the model returned, not a summary of it. Sortable
 * by confidence or entity, filterable by outcome, and fully keyboard operable
 * (Enter/Space toggles a row).
 */
import { useEffect, useMemo, useRef, useState } from "react";
import gsap from "gsap";
import { ChevronRight, Search, Inbox } from "lucide-react";

import { cn } from "@/lib/utils";
import type { ActionItem, JudgedItem } from "@/lib/types";

export interface ResultRow {
  subject: string;
  type: string;
  isMatch: boolean;
  confidence: number;
  reasoning: string;
  tier: string;
  status: string;
  description: string;
  verified: boolean | null;
  verifyField: string;
  verifyExpected: string | number | boolean | null;
  verifyObserved: string | number | boolean | null;
}

export function buildRows(
  judged: JudgedItem[],
  actions: ActionItem[],
): ResultRow[] {
  const bySubject = new Map<string, ActionItem>();
  for (const a of actions) bySubject.set(a.subject, a);

  return judged.map((j) => {
    const a = bySubject.get(j.subject);
    return {
      subject: j.subject,
      type: j.discrepancy_type,
      isMatch: j.is_match,
      confidence: j.confidence,
      reasoning: j.reasoning,
      tier: a?.tier ?? "—",
      status: a?.status ?? "—",
      description: a?.description ?? "",
      verified: a?.verified ?? null,
      verifyField: a?.verify_field ?? "",
      verifyExpected: a?.verify_expected ?? null,
      verifyObserved: a?.verify_observed ?? null,
    };
  });
}

type SortKey = "subject" | "confidence" | "type";
type Filter = "all" | "applied" | "approval" | "no_action" | "failed";

const STATUS_STYLE: Record<string, string> = {
  applied: "text-[var(--color-ok)] border-[var(--color-ok)]/40 bg-[var(--color-ok)]/10",
  verify_failed: "text-[var(--color-bad)] border-[var(--color-bad)]/40 bg-[var(--color-bad)]/10",
  skipped: "text-[var(--color-warn)] border-[var(--color-warn)]/40 bg-[var(--color-warn)]/10",
  no_action: "text-faint border-[var(--border)]",
};

function Row({ row }: { row: ResultRow }) {
  const [open, setOpen] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = bodyRef.current;
    if (!el) return;
    if (open) {
      gsap.fromTo(
        el,
        { height: 0, opacity: 0 },
        { height: "auto", opacity: 1, duration: 0.32, ease: "power2.out" },
      );
    }
  }, [open]);

  return (
    <div className="border-b last:border-b-0">
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="grid w-full grid-cols-[16px_1fr_auto] items-center gap-2 px-3 py-2.5 text-left hover:bg-[var(--bg-sunken)] sm:grid-cols-[16px_minmax(0,1.4fr)_minmax(0,1fr)_auto_auto] sm:gap-3"
      >
        <ChevronRight
          className={cn(
            "h-4 w-4 shrink-0 text-faint transition-transform duration-200",
            open && "rotate-90",
          )}
          aria-hidden="true"
        />
        <span className="truncate text-sm font-medium">{row.subject}</span>
        <span className="hidden truncate text-xs text-dim sm:block">
          {row.type.replace(/_/g, " ")}
        </span>
        <span
          className={cn(
            "hidden rounded-md border px-1.5 py-0.5 text-[10px] font-medium sm:inline-block",
            STATUS_STYLE[row.status] ?? "text-faint border-[var(--border)]",
          )}
        >
          {row.status.replace(/_/g, " ")}
        </span>
        <span
          className={cn(
            "text-xs tabular",
            row.confidence >= 0.9 ? "text-[var(--color-ok)]" : "text-[var(--color-warn)]",
          )}
        >
          {row.confidence.toFixed(2)}
        </span>
      </button>

      {open && (
        <div ref={bodyRef} className="overflow-hidden">
          <div className="sunken space-y-2 px-3 pb-3 pt-2 sm:px-10">
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-wide text-faint">
                Judge reasoning · verbatim
              </p>
              <p className="mt-1 text-xs leading-relaxed text-dim">
                {row.reasoning || "—"}
              </p>
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-wide text-faint">
                  Action
                </p>
                <p className="mt-0.5 text-xs text-dim">
                  {row.description || "no action taken"}
                </p>
                <p className="mt-0.5 text-[11px] text-faint">tier: {row.tier}</p>
              </div>
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-wide text-faint">
                  Read-back verification
                </p>
                {row.verified === null ? (
                  <p className="mt-0.5 text-xs text-faint">not applicable</p>
                ) : (
                  <p
                    className={cn(
                      "mt-0.5 text-xs",
                      row.verified ? "text-[var(--color-ok)]" : "text-[var(--color-bad)]",
                    )}
                  >
                    {row.verified
                      ? `confirmed · ${row.verifyField} = ${String(row.verifyObserved)}`
                      : `MISMATCH · expected ${String(row.verifyExpected)}, record reads ${String(row.verifyObserved)}`}
                  </p>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export function ResultsTable({
  rows,
  loading,
}: {
  rows: ResultRow[];
  loading: boolean;
}) {
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<SortKey>("confidence");
  const [filter, setFilter] = useState<Filter>("all");

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    let out = rows.filter(
      (r) =>
        !q ||
        r.subject.toLowerCase().includes(q) ||
        r.type.toLowerCase().includes(q),
    );
    if (filter === "applied") out = out.filter((r) => r.status === "applied");
    if (filter === "approval") out = out.filter((r) => r.tier === "slack_approval");
    if (filter === "no_action") out = out.filter((r) => r.status === "no_action");
    if (filter === "failed") out = out.filter((r) => r.status === "verify_failed");

    return [...out].sort((a, b) => {
      if (sort === "confidence") return b.confidence - a.confidence;
      if (sort === "type") return a.type.localeCompare(b.type);
      return a.subject.localeCompare(b.subject);
    });
  }, [rows, query, sort, filter]);

  const filters: { key: Filter; label: string }[] = [
    { key: "all", label: "All" },
    { key: "applied", label: "Applied" },
    { key: "approval", label: "Approval" },
    { key: "no_action", label: "No action" },
    { key: "failed", label: "Failed" },
  ];

  return (
    <div className="surface overflow-hidden rounded-xl">
      <div className="flex flex-wrap items-center gap-2 border-b p-3">
        <h2 className="text-sm font-semibold tracking-tight">Results</h2>
        <span className="text-[11px] text-faint">
          {visible.length} of {rows.length}
        </span>

        <div className="ml-auto flex flex-wrap items-center gap-2">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-faint" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Filter entities…"
              aria-label="Filter results by entity or type"
              className="sunken w-[150px] rounded-md border py-1 pl-7 pr-2 text-xs outline-none focus:border-[var(--color-accent)]"
            />
          </div>
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value as SortKey)}
            aria-label="Sort results"
            className="sunken rounded-md border px-2 py-1 text-xs outline-none"
          >
            <option value="confidence">Confidence</option>
            <option value="subject">Entity</option>
            <option value="type">Type</option>
          </select>
        </div>

        <div className="flex w-full flex-wrap gap-1 sm:w-auto">
          {filters.map((f) => (
            <button
              key={f.key}
              onClick={() => setFilter(f.key)}
              aria-pressed={filter === f.key}
              className={cn(
                "rounded-md border px-2 py-1 text-[11px]",
                filter === f.key
                  ? "border-[var(--color-accent)] bg-[var(--color-accent)]/12 text-[var(--color-accent)]"
                  : "hover:bg-[var(--bg-sunken)]",
              )}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="divide-y" aria-busy="true">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="flex items-center gap-3 px-3 py-3">
              <div className="sunken h-3 w-3 animate-pulse rounded-full" />
              <div className="sunken h-3 w-40 animate-pulse rounded" />
              <div className="sunken ml-auto h-3 w-10 animate-pulse rounded" />
            </div>
          ))}
        </div>
      ) : rows.length === 0 ? (
        <div className="px-6 py-10 text-center">
          <Inbox className="mx-auto h-5 w-5 text-faint" />
          <p className="mt-2 text-sm font-medium">Nothing judged yet</p>
          <p className="mx-auto mt-1 max-w-xs text-xs text-dim">
            Start a run and rows will stream in here as the judge returns each
            verdict.
          </p>
        </div>
      ) : visible.length === 0 ? (
        <div className="px-6 py-10 text-center">
          <p className="text-sm font-medium">No rows match</p>
          <button
            onClick={() => {
              setQuery("");
              setFilter("all");
            }}
            className="mt-2 rounded-md border px-2.5 py-1 text-xs hover:bg-[var(--bg-sunken)]"
          >
            Clear filters
          </button>
        </div>
      ) : (
        <div>
          {visible.map((row) => (
            <Row key={`${row.subject}:${row.type}`} row={row} />
          ))}
        </div>
      )}
    </div>
  );
}
