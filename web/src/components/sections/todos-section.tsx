"use client";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { Todo } from "@/lib/api/types";
import { call, useApiMutation, useTodos } from "@/lib/queries";
import { formatDay, formatMoment } from "@/lib/time";
import { cn } from "@/lib/utils";

export function TodosSection({ teamId, canWrite }: { teamId: string; canWrite: boolean }) {
  const q = useTodos(teamId);
  const c = q.data?.completion;
  const open = q.data?.items.filter((t) => !t.done_at) ?? [];
  const done = q.data?.items.filter((t) => t.done_at) ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle>To-Dos</CardTitle>
        <CardDescription data-testid="completion">
          {q.data && (c
            ? <>Week of {formatDay(c.week_start)}: <b>{c.done}/{c.due} done · {c.rate_percent}%</b>{" "}
                <Badge variant={c.meets_target ? "outline" : "destructive"} className={cn(c.meets_target && "border-success text-success")}>
                  {c.meets_target ? "meets 90% target" : "below 90% target"}
                </Badge></>
            : <>Week of {formatDay(q.data.last_closed_week)}: nothing was due.</>)}
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-1">
        {q.isLoading && <Skeleton className="h-24" />}
        {open.map((t) => <TodoRow key={t.id} teamId={teamId} todo={t} canWrite={canWrite} />)}
        {open.length === 0 && q.data && <p className="py-4 text-center text-sm text-muted-foreground">Nothing open.</p>}
        {done.length > 0 && (
          <details className="mt-3 group/done">
            <summary className="cursor-pointer text-xs font-medium tracking-wide text-muted-foreground uppercase">
              Done ({done.length})
            </summary>
            <div className="mt-1 grid gap-1">
              {done.map((t) => <TodoRow key={t.id} teamId={teamId} todo={t} canWrite={canWrite} />)}
            </div>
          </details>
        )}
      </CardContent>
    </Card>
  );
}

function TodoRow({ teamId, todo, canWrite }: { teamId: string; todo: Todo; canWrite: boolean }) {
  const toggle = useApiMutation(teamId, ["todos"], (done: boolean) => call.setDone(todo.id, done));
  const isDone = !!todo.done_at;
  return (
    <label className={cn("flex items-start gap-3 rounded-md px-2 py-1.5 hover:bg-muted/50", isDone && "text-muted-foreground")}
           data-testid={`todo-${todo.id}`}>
      <input type="checkbox" className="mt-1 size-4 accent-primary" checked={isDone} disabled={!canWrite || toggle.isPending}
             onChange={(e) => toggle.mutate(e.target.checked)} aria-label={`${isDone ? "Undo" : "Mark done"}: ${todo.title}`} />
      <span className="min-w-0 flex-1">
        <span className={cn(isDone && "line-through")}>{todo.title}</span>
        <span className="block text-xs text-muted-foreground">
          {todo.owner.name} · due {formatDay(todo.due_date)}
          {todo.done_at && <> · done {formatMoment(todo.done_at)}</>}
          {todo.source_issue && <> · from Issue “{todo.source_issue.title}”</>}
        </span>
      </span>
      {todo.overdue && <Badge variant="destructive">Overdue</Badge>}
    </label>
  );
}
