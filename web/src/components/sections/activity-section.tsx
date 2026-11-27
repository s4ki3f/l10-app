"use client";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { AuditEvent } from "@/lib/api/types";
import { useActivity } from "@/lib/queries";
import { formatMoment } from "@/lib/time";

const SECTION: Record<string, string> = { scorecard: "Scorecard", ids: "IDS", conclude: "Conclude" };

function pair(changes: AuditEvent["changes"], key: string): [unknown, unknown] {
  const v = (changes as Record<string, unknown>)[key];
  return Array.isArray(v) ? [v[0], v[1]] : [undefined, undefined];
}

const priority = (p: unknown) => (p == null ? "no priority" : `priority ${p}`);

/** One line per change, in words. The server stores names as they were at the time. */
export function describe(e: AuditEvent): string {
  const label = `“${e.entity.label}”`;
  switch (e.action) {
    case "issue.created": return `created Issue ${label}`;
    case "issue.raised": return `made an Issue: ${label}`;
    case "issue.priority_changed": {
      const [a, b] = pair(e.changes, "priority");
      return `changed ${label} from ${priority(a)} to ${priority(b)}`;
    }
    case "issue.solved": return `solved ${label}`;
    case "todo.created": return `added To-Do ${label}`;
    case "todo.done": return `marked ${label} done`;
    case "todo.undone": return `reopened ${label}`;
    case "score.set": {
      const [a, b] = pair(e.changes, "value");
      return `set ${e.entity.label} to ${b}${a == null ? "" : ` (was ${a})`}`;
    }
    case "meeting.started": return `started the ${e.entity.label}`;
    case "meeting.joined": return `joined the ${e.entity.label}`;
    case "meeting.advanced": {
      const [a, b] = pair(e.changes, "section");
      return `moved the meeting from ${SECTION[String(a)] ?? a} to ${SECTION[String(b)] ?? b}`;
    }
    case "meeting.rated": {
      const [a, b] = pair(e.changes, "rating");
      return a == null ? `rated the meeting ${b}` : `changed their rating from ${a} to ${b}`;
    }
    case "meeting.ended": return `ended the ${e.entity.label}`;
    default: return `${e.action} ${label}`;
  }
}

/** Bonus: the audit trail — who changed what, when. Read-only; refreshes live with every change. */
export function ActivitySection({ teamId }: { teamId: string }) {
  const q = useActivity(teamId);
  const items = q.data?.pages.flatMap((p) => p.items) ?? [];
  return (
    <Card>
      <CardHeader>
        <CardTitle>Activity</CardTitle>
        <CardDescription>Every change, who made it and when. History can&apos;t be edited.</CardDescription>
      </CardHeader>
      <CardContent>
        {q.isLoading && <Skeleton className="h-24" />}
        {q.data && items.length === 0 && <p className="text-sm text-muted-foreground">No changes yet.</p>}
        <ol className="grid gap-1.5 text-sm" aria-label="Activity" data-testid="activity">
          {items.map((e) => (
            <li key={e.id} className="flex gap-2">
              <span className="min-w-0 flex-1"><b>{e.actor.name}</b> {describe(e)}</span>
              <time dateTime={e.at} className="shrink-0 text-xs text-muted-foreground">{formatMoment(e.at)}</time>
            </li>
          ))}
        </ol>
        {q.hasNextPage && (
          <Button variant="ghost" size="sm" className="mt-2" disabled={q.isFetchingNextPage} onClick={() => q.fetchNextPage()}>
            Show older
          </Button>
        )}
      </CardContent>
    </Card>
  );
}
