"use client";

/**
 * Cmd+K command palette.
 *
 * Real actions, not a search box: start a run, start an eval, reseed the twin,
 * re-check integrations, jump to a scenario, toggle the theme. Fully keyboard
 * driven — open with Cmd/Ctrl+K, arrow to move, Enter to run, Esc to dismiss.
 */
import { useEffect, useState } from "react";
import { Command } from "cmdk";
import {
  Play,
  FlaskConical,
  Sprout,
  RefreshCw,
  Moon,
  Sun,
  Target,
} from "lucide-react";

import type { Scenario } from "@/lib/types";

export interface PaletteActions {
  startRun: () => void;
  startEval: () => void;
  reseed: () => void;
  recheck: () => void;
  toggleTheme: () => void;
  focusScenario: (name: string) => void;
}

export function CommandPalette({
  scenarios,
  actions,
  dark,
}: {
  scenarios: Scenario[];
  actions: PaletteActions;
  dark: boolean;
}) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() === "k" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setOpen((v) => !v);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const run = (fn: () => void) => {
    setOpen(false);
    fn();
  };

  return (
    <Command.Dialog
      open={open}
      onOpenChange={setOpen}
      label="Command palette"
      className="fixed inset-0 z-50"
    >
      <div
        className="absolute inset-0 bg-black/55 backdrop-blur-sm"
        onClick={() => setOpen(false)}
        aria-hidden="true"
      />
      <div className="surface absolute left-1/2 top-[12vh] w-[min(560px,92vw)] -translate-x-1/2 overflow-hidden rounded-xl shadow-2xl">
        <Command.Input
          autoFocus
          placeholder="Type a command or search a scenario…"
          className="w-full border-b bg-transparent px-4 py-3 text-sm outline-none placeholder:text-[var(--text-faint)]"
        />
        <Command.List className="max-h-[50vh] overflow-y-auto p-2">
          <Command.Empty className="px-3 py-6 text-center text-xs text-dim">
            No matching command.
          </Command.Empty>

          <Command.Group
            heading="Actions"
            className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wide [&_[cmdk-group-heading]]:text-[var(--text-faint)]"
          >
            <Item icon={Play} label="Start pipeline run" hint="run"
                  onSelect={() => run(actions.startRun)} />
            <Item icon={FlaskConical} label="Start eval (score vs scenarios)" hint="eval"
                  onSelect={() => run(actions.startEval)} />
            <Item icon={Sprout} label="Reseed fresh twin" hint="seed"
                  onSelect={() => run(actions.reseed)} />
            <Item icon={RefreshCw} label="Re-check all integrations" hint="health"
                  onSelect={() => run(actions.recheck)} />
            <Item
              icon={dark ? Sun : Moon}
              label={dark ? "Switch to light mode" : "Switch to dark mode"}
              hint="theme"
              onSelect={() => run(actions.toggleTheme)}
            />
          </Command.Group>

          {scenarios.length > 0 && (
            <Command.Group
              heading="Scenarios"
              className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wide [&_[cmdk-group-heading]]:text-[var(--text-faint)]"
            >
              {scenarios.map((s) => (
                <Item
                  key={s.n}
                  icon={Target}
                  label={`${s.n}. ${s.name}`}
                  hint={s.decoy ? "decoy" : s.expected_type.replace(/_/g, " ")}
                  onSelect={() => run(() => actions.focusScenario(s.name))}
                />
              ))}
            </Command.Group>
          )}
        </Command.List>
        <div className="flex items-center gap-3 border-t px-3 py-2 text-[10px] text-faint">
          <span>↑↓ navigate</span>
          <span>↵ select</span>
          <span>esc close</span>
        </div>
      </div>
    </Command.Dialog>
  );
}

function Item({
  icon: Icon,
  label,
  hint,
  onSelect,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  hint?: string;
  onSelect: () => void;
}) {
  return (
    <Command.Item
      onSelect={onSelect}
      className="flex cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm data-[selected=true]:bg-[var(--color-accent)]/12 data-[selected=true]:text-[var(--color-accent)]"
    >
      <Icon className="h-4 w-4 shrink-0" />
      <span className="truncate">{label}</span>
      {hint && (
        <span className="ml-auto shrink-0 text-[10px] text-faint">{hint}</span>
      )}
    </Command.Item>
  );
}
