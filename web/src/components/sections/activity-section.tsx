"use client";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { AuditEvent } from "@/lib/api/types";
import { useActivity, useScorecard } from "@/lib/queries";
import { formatDay, formatMoment } from "@/lib/time";
import { withUnit } from "@/lib/utils";

const SECTION: Record<string, string> = { scorecard: "Scorecard", ids: "IDS", conclude: "Conclude" };
type Ref = { id: string; name?: string; title?: string };

const priority = (p: unknown) => (p == null ? "no priority" : `priority ${p}`);

/** One line per change, in words. Labels and related names are stored as they were at the time of the change. */
export function describe(e: AuditEvent, unitOf: (measurableId: string) => string): string {
  const was = (key: string) => e.changes[key]?.[0];
  const now = (key: string) => e.changes[key]?.[1];
  const label = `“${e.entity.label}”`;
  switch (e.action) {
    case "issue.created": return `created Issue ${label}`;
    case "issue.raised": return `made an Issue: ${label}`;
    case "issue.priority_changed": return `changed ${label} from ${priority(was("priority"))} to ${priority(now("priority"))}`;
    case "issue.solved": return `solved ${label}: ${now("resolution_note")}`;
    case "todo.created": {
      const owner = now("owner") as Ref | undefined, due = now("due_date"), source = now("source_issue") as Ref | undefined;
      return `added To-Do ${label}${owner ? ` for ${owner.name}` : ""}${due ? `, due ${formatDay(String(due))}` : ""}`
        + (source ? ` (from “${source.title}”)` : "");
    }
    case "todo.done": return `marked ${label} done`;
    case "todo.undone": return `reopened ${label}`;
    case "score.set": {
      const unit = unitOf(e.entity.id.split(":")[0]);
      const v = (x: unknown) => withUnit(x as number, unit);
      return was("value") == null ? `entered ${v(now("value"))} for ${label}`
        : `changed ${label} from ${v(was("value"))} to ${v(now("value"))}`;
    }
    case "meeting.started": return `started the ${e.entity.label}`;
    case "meeting.joined": return `joined the ${e.entity.label}`;
    case "meeting.advanced": {
      const name = (s: unknown) => SECTION[String(s)] ?? String(s);
      return `moved the meeting from ${name(was("section"))} to ${name(now("section"))}`;
    }
    case "meeting.rated":
      return was("rating") == null ? `rated the meeting ${now("rating")}/10`
        : `changed their rating from ${was("rating")}/10 to ${now("rating")}/10`;
    case "meeting.ended": return `ended the ${e.entity.label}`;
    default: {
      const unphrased: never = e.action; // a new action must be put into words here: TypeScript fails until it is
      return `${unphrased} ${label}`;
    }
  }
}

/** Bonus: the audit trail — who changed what, when. Read-only; refreshes live with every change. */
export function ActivitySection({ teamId }: { teamId: string }) {
  const q = useActivity(teamId);
  // units come from the Scorecard this page already loads (measurables can't be edited, so they're as they were)
  const units = new Map((useScorecard(teamId).data?.rows ?? []).map((r) => [r.id, r.unit]));
  const items = q.data?.pages.flatMap((p) => p.items) ?? [];
  return (
    <Card data-testid="activity-card" data-state={q.status}>
      <CardHeader>
        <CardTitle>Activity</CardTitle>
        <CardDescription>Every change, who made it and when. History can&apos;t be edited.</CardDescription>
      </CardHeader>
      <CardContent>
        {q.isPending && <Skeleton className="h-24" />}
        {q.isError && !q.data && (
          <p className="text-sm text-muted-foreground">
            Couldn&apos;t load the activity.{" "}
            <Button variant="link" size="sm" className="h-auto p-0" onClick={() => q.refetch()}>Try again</Button>
          </p>
        )}
        {q.data && items.length === 0 && <p className="text-sm text-muted-foreground">No changes yet.</p>}
        {items.length > 0 && (
          // role="list": Safari drops list semantics when list-style is none
          <ol role="list" className="grid gap-1.5 text-sm" aria-label="Activity" data-testid="activity">
            {items.map((e) => {
              const text = describe(e, (id) => units.get(id) ?? "");
              return (
                <li key={e.id} className="flex gap-2">
                  <span className="line-clamp-3 min-w-0 flex-1 break-words" title={text}><b>{e.actor.name}</b> {text}</span>
                  <time dateTime={e.at} className="shrink-0 text-xs text-muted-foreground">{formatMoment(e.at)}</time>
                </li>
              );
            })}
          </ol>
        )}
        {q.hasNextPage && (
          <Button variant="ghost" size="sm" className="mt-2" disabled={q.isFetchingNextPage} onClick={() => q.fetchNextPage()}>
            Show older
          </Button>
        )}
        {q.isFetchNextPageError && <p className="text-xs text-destructive">Couldn&apos;t load older changes; try again.</p>}
      </CardContent>
    </Card>
  );
}
