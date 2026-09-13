"use client";

/**
 * Live activity feed, fed by the same SSE stream that drives the system map.
 *
 * Every line is a real event emitted by the pipeline as it ran - "Slack
 * approval requested", "Write verified", "Silent failure caught and retried".
 * Nothing here is simulated or replayed from a fixture.
 */
import { useEffect, useRef } from "react";
import gsap from "gsap";
import {
  CheckCircle2,
  AlertTriangle,
  XCircle,
  Info,
  Radio,
} from "lucide-react";

import { cn } from "@/lib/utils";
import type { EventLevel, RunEvent } from "@/lib/types";

const ICON: Record<EventLevel, React.ComponentType<{ className?: string }>> = {
  success: CheckCircle2,
  warning: AlertTriangle,
  error: XCircle,
  info: Info,
};

const TONE: Record<EventLevel, string> = {
  success: "text-[var(--color-ok)]",
  warning: "text-[var(--color-warn)]",
  error: "text-[var(--color-bad)]",
  info: "text-faint",
};

export function ActivityFeed({
  events,
  live,
}: {
  events: RunEvent[];
  live: boolean;
}) {
  const listRef = useRef<HTMLUListElement>(null);
  const lastSeq = useRef(0);

  // Animate only genuinely new rows, and keep the newest in view.
  useEffect(() => {
    const list = listRef.current;
    if (!list) return;
    const newest = events[events.length - 1];
    if (!newest || newest.seq === lastSeq.current) return;
    lastSeq.current = newest.seq;

    const node = list.querySelector(`[data-seq="${newest.seq}"]`);
    if (node) {
      gsap.from(node, { opacity: 0, x: -8, duration: 0.3, ease: "power2.out" });
    }
    list.scrollTop = list.scrollHeight;
  }, [events]);

  const ordered = [...events].slice(-200);

  return (
    <div className="surface flex h-full flex-col overflow-hidden rounded-xl">
      <div className="flex items-center gap-2 border-b p-3">
        <h2 className="text-sm font-semibold tracking-tight">Activity</h2>
        {live && (
          <span className="inline-flex items-center gap-1 rounded-full bg-[var(--color-accent)]/15 px-2 py-0.5 text-[10px] font-medium text-[var(--color-accent)]">
            <Radio className="h-2.5 w-2.5 animate-pulse" />
            live
          </span>
        )}
        <span className="ml-auto text-[11px] text-faint tabular">
          {events.length}
        </span>
      </div>

      {events.length === 0 ? (
        <div className="flex flex-1 items-center justify-center px-6 py-10 text-center">
          <div>
            <Radio className="mx-auto h-5 w-5 text-faint" />
            <p className="mt-2 text-sm font-medium">No activity yet</p>
            <p className="mx-auto mt-1 max-w-[220px] text-xs text-dim">
              Events stream here over SSE the moment a run starts.
            </p>
          </div>
        </div>
      ) : (
        <ul
          ref={listRef}
          className="flex-1 overflow-y-auto"
          aria-live="polite"
          aria-label="Live run activity"
        >
          {ordered.map((event) => {
            const Icon = ICON[event.level];
            return (
              <li
                key={event.seq}
                data-seq={event.seq}
                className="flex gap-2 border-b px-3 py-2 last:border-b-0"
              >
                <Icon className={cn("mt-0.5 h-3.5 w-3.5 shrink-0", TONE[event.level])} />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-xs font-medium">{event.title}</p>
                  {event.message && (
                    <p className="mt-0.5 line-clamp-2 text-[11px] leading-tight text-faint">
                      {event.message}
                    </p>
                  )}
                </div>
                <time
                  dateTime={event.at}
                  className="shrink-0 text-[10px] text-faint tabular"
                >
                  {new Date(event.at).toLocaleTimeString(undefined, {
                    hour12: false,
                  })}
                </time>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
