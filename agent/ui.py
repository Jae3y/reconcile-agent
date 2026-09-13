"""Rich console presentation.

The demo is screen-recorded, so output is laid out as a readable report rather
than a log stream: one rule per stage, dense tables, and a verdict panel.
"""
from __future__ import annotations

import os
import sys

# Windows consoles default to cp1252, which cannot encode the box drawing and
# status glyphs below. Force UTF-8 before rich inspects the stream, and fall
# back to replacement characters rather than crashing a demo mid-run.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass

from rich.box import HEAVY_HEAD, ROUNDED
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console(highlight=False, legacy_windows=False,
                  force_terminal=None if os.getenv("NO_COLOR") else True)

# One palette, used consistently everywhere.
ACCENT = "bright_cyan"
OK = "bright_green"
WARN = "yellow"
BAD = "bright_red"
MUTED = "grey62"

_STATUS_STYLE = {
    "applied": (OK, "✓ applied"),
    "verify_failed": (BAD, "✗ verify failed"),
    "skipped": (WARN, "– skipped"),
    "denied": (BAD, "⛔ denied"),
    "timeout": (WARN, "– timeout"),
    "error": (BAD, "✗ error"),
    "no_action": (MUTED, "· no action"),
    "drafted": (OK, "✓ drafted"),
}

_TIER_STYLE = {
    "automatic": (OK, "automatic"),
    "slack_approval": (WARN, "slack approval"),
    "never": (BAD, "never"),
    "no_action": (MUTED, "no action"),
}


def banner(mode: str, backend: str, lemma: str, judge_model: str) -> None:
    body = Text()
    body.append("Billing ↔ CRM reconciliation agent\n", style="bold white")
    body.append("five deterministic stages, one LLM judgement in the middle\n\n", style=MUTED)
    for label, value in (("mode", mode), ("data backend", backend),
                         ("judge", f"{judge_model} via Antigravity CLI (no API key)"),
                         ("tracing", lemma)):
        body.append(f"{label:>14} : ", style=MUTED)
        body.append(f"{value}\n", style="white")
    console.print(Panel(body, title="[bold]reconcile-agent[/bold]", border_style=ACCENT,
                        box=ROUNDED, padding=(1, 2)))


def stage(number: int, name: str, detail: str = "") -> None:
    text = f"[bold {ACCENT}]Stage {number}[/] · [bold white]{name}[/]"
    if detail:
        text += f"  [{MUTED}]{detail}[/]"
    console.rule(text, style=ACCENT, align="left")


def counts_table(counts: dict[str, int]) -> None:
    table = Table(box=ROUNDED, border_style=MUTED, show_header=False, pad_edge=False)
    table.add_column("source", style=MUTED)
    table.add_column("n", justify="right", style="bold white")
    for key, value in counts.items():
        table.add_row(key, str(value))
    console.print(table)


def candidates_table(candidates) -> None:
    table = Table(box=HEAVY_HEAD, border_style=MUTED, header_style=f"bold {ACCENT}")
    table.add_column("#", justify="right", style=MUTED, width=3)
    table.add_column("candidate type", style="white")
    table.add_column("subject", style="bold white")
    table.add_column("key evidence", style=MUTED, overflow="fold")
    for i, c in enumerate(sorted(candidates, key=lambda x: (x.kind.value, x.subject)), 1):
        first = next(iter(c.facts.items()), ("", ""))
        table.add_row(str(i), c.kind.value, c.subject, f"{first[0]}: {first[1]}")
    console.print(table)


def judged_table(judged, failures) -> None:
    table = Table(box=HEAVY_HEAD, border_style=MUTED, header_style=f"bold {ACCENT}")
    table.add_column("subject", style="bold white")
    table.add_column("type", style="white")
    table.add_column("real?", justify="center")
    table.add_column("conf", justify="right")
    table.add_column("reasoning", style=MUTED, overflow="ellipsis", max_width=62)
    for c, j in judged:
        verdict = Text("yes", style=OK) if j.is_match else Text("no", style=MUTED)
        conf_style = OK if j.confidence >= 0.9 else WARN
        table.add_row(c.subject, c.kind.value, verdict,
                      Text(f"{j.confidence:.2f}", style=conf_style),
                      j.reasoning.replace("\n", " "))
    for f in failures:
        table.add_row(f.candidate.subject, f.candidate.kind.value,
                      Text("ERR", style=BAD), "—", Text(f.detail[:60], style=BAD))
    console.print(table)


def findings_table(findings) -> None:
    table = Table(box=HEAVY_HEAD, border_style=MUTED, header_style=f"bold {ACCENT}")
    table.add_column("subject", style="bold white")
    table.add_column("tier", justify="left")
    table.add_column("why", style=MUTED, overflow="fold")
    for f in sorted(findings, key=lambda x: (x.tier.value, x.candidate.subject)):
        style, label = _TIER_STYLE.get(f.tier.value, (MUTED, f.tier.value))
        table.add_row(f.candidate.subject, Text(label, style=style), f.tier_reason)
    console.print(table)


def actions_table(results) -> None:
    table = Table(box=HEAVY_HEAD, border_style=MUTED, header_style=f"bold {ACCENT}")
    table.add_column("subject", style="bold white")
    table.add_column("tier", style=MUTED)
    table.add_column("action", style="white", overflow="fold", max_width=44)
    table.add_column("outcome", justify="left")
    table.add_column("read-back", style=MUTED, overflow="fold", max_width=38)
    for r in sorted(results, key=lambda x: (x.tier.value, x.subject)):
        style, label = _STATUS_STYLE.get(r.status, (MUTED, r.status))
        if r.verify is None:
            readback = "—"
        elif r.verify.ok:
            readback = f"{r.verify.field} = {r.verify.observed}"
        else:
            readback = r.verify.detail
        table.add_row(r.subject, r.tier.value, r.description,
                      Text(label, style=style),
                      Text(readback, style=OK if (r.verify and r.verify.ok) else MUTED))
    console.print(table)


def summary_panel(outcome) -> None:
    applied = len(outcome.applied())
    failed = sum(1 for r in outcome.results if r.status == "verify_failed")
    skipped = sum(1 for r in outcome.results if r.status == "skipped")
    none = sum(1 for r in outcome.results if r.status == "no_action")

    left = Table(box=None, show_header=False, pad_edge=False)
    left.add_column(style=MUTED)
    left.add_column(justify="right", style="bold white")
    left.add_row("candidates", str(len(outcome.candidates)))
    left.add_row("judged", str(len(outcome.judged)))
    left.add_row("judge failures", str(len(outcome.judge_failures)))
    left.add_row("applied + verified", Text(str(applied), style=OK))
    left.add_row("verify failures caught", Text(str(failed), style=BAD if failed else MUTED))
    left.add_row("skipped", str(skipped))
    left.add_row("no action", str(none))

    right = Table(box=None, show_header=False, pad_edge=False)
    right.add_column(style=MUTED)
    right.add_column(justify="right", style="white")
    for name, seconds in outcome.timings.items():
        right.add_row(f"{name} time", f"{seconds:,.1f}s")
    right.add_row("trace id", outcome.run_id or "—")

    console.print(Panel(Group(left, Text(""), right), title="[bold]run summary[/bold]",
                        border_style=ACCENT, box=ROUNDED, padding=(1, 2)))


def note(message: str, style: str = MUTED) -> None:
    console.print(f"  [{style}]{message}[/]")


def warn(message: str) -> None:
    console.print(f"  [{WARN}]! {message}[/]")


def error(message: str) -> None:
    console.print(f"  [{BAD}]✗ {message}[/]")
