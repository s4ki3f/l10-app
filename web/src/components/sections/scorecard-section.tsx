"use client";
import { useEffect, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { Cell, ScorecardRow } from "@/lib/api/types";
import { call, useApiMutation, useScorecard } from "@/lib/queries";
import { formatDay } from "@/lib/time";
import { cn } from "@/lib/utils";

const STATUS = {
  on_track: { label: "On track", mark: "✓", cls: "bg-success/10 text-success" },
  off_track: { label: "Off track", mark: "✗", cls: "bg-destructive/10 text-destructive font-medium" },
  pending: { label: "Pending", mark: "·", cls: "text-muted-foreground" },
} as const;

function goalText(row: ScorecardRow) {
  const { goal, unit } = row;
  const u = unit === "count" ? "" : ` ${unit}`;
  if (goal.type === "between") return `${goal.min}–${goal.max}${u}`;
  return `${{ at_least: "≥", at_most: "≤", exactly: "=" }[goal.type]} ${goal.value}${u}`;
}

/** Built once; the Team page and the meeting's Scorecard section both render it. */
export function ScorecardSection({ teamId, canWrite, focusWeek }: { teamId: string; canWrite: boolean; focusWeek?: "last_closed" }) {
  const q = useScorecard(teamId);
  const sc = q.data;
  const scroller = useRef<HTMLDivElement>(null);
  const hasData = !!sc;
  // recent weeks matter most: start scrolled to the right edge (no-op when everything fits)
  useEffect(() => {
    if (hasData && scroller.current) scroller.current.scrollLeft = scroller.current.scrollWidth;
  }, [hasData]);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Scorecard</CardTitle>
        <CardDescription>
          Last 13 weeks. {canWrite ? "Click a score to edit it; off-track cells can become an Issue." : "Read only."}
        </CardDescription>
      </CardHeader>
      <CardContent className="px-0 sm:px-6">
        {!sc ? <Skeleton className="mx-6 h-40 sm:mx-0" /> : (
          <div ref={scroller} className="overflow-x-auto">
            <table className="w-full border-separate border-spacing-0 text-sm" data-testid="scorecard">
              <thead>
                <tr className="text-xs text-muted-foreground">
                  <th className="sticky left-0 z-10 min-w-48 bg-card px-3 py-2 text-left font-medium">Measurable</th>
                  <th className="px-2 py-2 text-left font-medium">Goal</th>
                  {sc.weeks.map((w) => (
                    <th key={w} className={cn("px-1 py-2 text-center font-medium whitespace-nowrap",
                      w === sc.last_closed_week && "rounded-t-md bg-primary/5 text-foreground")}>
                      {formatDay(w)}
                      {w === sc.current_week && <div className="text-[10px] font-normal">this week</div>}
                      {w === sc.last_closed_week && <div className="text-[10px] font-normal">last closed</div>}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sc.rows.map((row) => (
                  <tr key={row.id} className="border-t">
                    <td className="sticky left-0 z-10 border-t bg-card px-3 py-2">
                      <div className="font-medium">{row.name}</div>
                      <div className="text-xs text-muted-foreground">{row.owner.name}</div>
                    </td>
                    <td className="border-t px-2 py-2 text-xs whitespace-nowrap text-muted-foreground">{goalText(row)}</td>
                    {row.cells.map((cell) => (
                      <td key={cell.week_start} className={cn("border-t p-0.5",
                        cell.week_start === sc.last_closed_week && "bg-primary/5",
                        focusWeek === "last_closed" && cell.week_start !== sc.last_closed_week && "opacity-60")}>
                        <ScoreCell teamId={teamId} row={row} cell={cell} canWrite={canWrite} prominent={cell.week_start === sc.last_closed_week} />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function ScoreCell({ teamId, row, cell, canWrite, prominent }: { teamId: string; row: ScorecardRow; cell: Cell; canWrite: boolean; prominent: boolean }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const save = useApiMutation(teamId, ["scorecard"], (v: string) => call.putScore(row.id, cell.week_start, v));
  const makeIssue = useApiMutation(teamId, ["issues", "scorecard"], () => call.makeIssue(row.id, cell.week_start),
    { success: `“${row.name}, week of ${formatDay(cell.week_start)}” is on the Issues list` });
  const s = STATUS[cell.status];
  const label = `${row.name}, week of ${formatDay(cell.week_start)}: ${cell.value ?? "no score"} — ${s.label}`;

  function commit() {
    const v = draft.trim();
    setEditing(false);
    if (v !== "" && v !== String(cell.value ?? "")) save.mutate(v);
  }

  if (editing) {
    return (
      <Input autoFocus value={draft} inputMode="decimal" aria-label={`Score for ${row.name}, week of ${formatDay(cell.week_start)}`}
             className="h-8 w-16 px-1 text-center" onChange={(e) => setDraft(e.target.value)} onBlur={commit}
             onKeyDown={(e) => { if (e.key === "Enter") commit(); if (e.key === "Escape") setEditing(false); }} />
    );
  }

  return (
    <div className={cn("group flex min-w-14 flex-col items-center rounded-md px-1 py-1", s.cls)}
         data-testid={`cell-${row.id}-${cell.week_start}`} data-status={cell.status}>
      <Tooltip>
        <TooltipTrigger asChild>
          <button type="button" disabled={!canWrite} aria-label={label}
                  className="w-full rounded text-center tabular-nums enabled:hover:underline disabled:cursor-default"
                  onClick={() => { setDraft(cell.value === null ? "" : String(cell.value)); setEditing(true); }}>
            {cell.value ?? "—"} <span aria-hidden className="text-[10px]">{s.mark}</span>
          </button>
        </TooltipTrigger>
        <TooltipContent>{label}</TooltipContent>
      </Tooltip>
      {cell.status === "off_track" && (cell.open_issue_id ? (
        <Badge variant="outline" className="mt-0.5 h-4 px-1 text-[10px]">Issue</Badge>
      ) : canWrite ? (
        <Button size="sm" variant="ghost" className={cn("mt-0.5 h-5 px-1 text-[10px] text-destructive",
                  !prominent && "opacity-0 group-hover:opacity-100 focus-visible:opacity-100")}
                disabled={makeIssue.isPending} onClick={() => makeIssue.mutate(undefined)}
                data-testid={`make-issue-${row.id}-${cell.week_start}`}>
          Make it an Issue
        </Button>
      ) : null)}
    </div>
  );
}
