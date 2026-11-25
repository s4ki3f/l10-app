"use client";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import type { Rock } from "@/lib/api/types";
import { useRocks } from "@/lib/queries";
import { formatDay } from "@/lib/time";
import { cn } from "@/lib/utils";

const STATUS: Record<Rock["status"], { label: string; cls: string }> = {
  on_track: { label: "On track", cls: "border-success text-success" },
  off_track: { label: "Off track", cls: "border-destructive text-destructive" },
  complete: { label: "Complete", cls: "border-primary" },
};

/** Read-only: Rocks and Milestones come from the seed. */
export function RocksSection({ teamId }: { teamId: string }) {
  const q = useRocks(teamId);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Rocks</CardTitle>
        <CardDescription>This quarter{q.data?.[0] ? ` (${q.data[0].quarter})` : ""}. Expand a Rock for its Milestones.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-2">
        {q.isLoading && <Skeleton className="h-24" />}
        {q.data?.length === 0 && <p className="text-sm text-muted-foreground">No Rocks this quarter.</p>}
        {q.data?.map((r) => (
          <Collapsible key={r.id} className="rounded-lg border" data-testid={`rock-${r.id}`}>
            <CollapsibleTrigger className="flex w-full flex-wrap items-center gap-3 p-3 text-left">
              <div className="min-w-0 flex-1">
                <div className="font-medium">{r.title}</div>
                <div className="text-xs text-muted-foreground">{r.owner.name} · {r.milestones.length} Milestones</div>
              </div>
              <div className="flex w-36 items-center gap-2">
                <Progress value={r.progress_percent} aria-label={`${r.progress_percent}% complete`} />
                <span className="w-9 text-right text-xs tabular-nums">{r.progress_percent}%</span>
              </div>
              <Badge variant="outline" className={STATUS[r.status].cls}>{STATUS[r.status].label}</Badge>
            </CollapsibleTrigger>
            <CollapsibleContent>
              <ul className="border-t px-3 py-2 text-sm">
                {r.milestones.length === 0 && <li className="text-muted-foreground">No Milestones.</li>}
                {r.milestones.map((m) => (
                  <li key={m.id} className={cn("flex items-center gap-2 py-0.5", m.done && "text-muted-foreground")}>
                    <span aria-hidden>{m.done ? "☑" : "☐"}</span>
                    <span className={cn("flex-1", m.done && "line-through")}>{m.title}</span>
                    <span className="text-xs">due {formatDay(m.due_date)}</span>
                    {m.past_due && <Badge variant="destructive">Past due</Badge>}
                    <span className="sr-only">{m.done ? "done" : "not done"}</span>
                  </li>
                ))}
              </ul>
            </CollapsibleContent>
          </Collapsible>
        ))}
      </CardContent>
    </Card>
  );
}
