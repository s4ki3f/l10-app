"use client";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ApiError } from "@/lib/api/client";
import { keys } from "@/lib/keys";
import type { Issue } from "@/lib/api/types";
import { call, useApiMutation, useIssues, useMembers } from "@/lib/queries";
import { formatDay, formatMoment } from "@/lib/time";
import { cn } from "@/lib/utils";

const NONE = "none";

/** Built once; the Team page and the meeting's IDS section both render it. */
export function IssuesSection({ teamId, canWrite, inMeeting = false }: { teamId: string; canWrite: boolean; inMeeting?: boolean }) {
  const [state, setState] = useState<"open" | "solved">("open");
  const q = useIssues(teamId, state);

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-2">
        <div>
          <CardTitle>Issues</CardTitle>
          <CardDescription>{state === "open" ? "Highest priority first, then oldest." : "Solved, newest first."}</CardDescription>
        </div>
        <Tabs value={state} onValueChange={(v) => setState(v as "open" | "solved")}>
          <TabsList>
            <TabsTrigger value="open">Open</TabsTrigger>
            <TabsTrigger value="solved">Solved</TabsTrigger>
          </TabsList>
        </Tabs>
      </CardHeader>
      <CardContent className="grid gap-3">
        {canWrite && state === "open" && <NewIssue teamId={teamId} />}
        {q.isLoading && <Skeleton className="h-24" />}
        {q.data?.length === 0 && <p className="py-6 text-center text-sm text-muted-foreground">No {state} Issues.</p>}
        <ol className="grid gap-2" data-testid={`issues-${state}`}>
          {q.data?.map((issue, i) => (
            <IssueRow key={issue.id} teamId={teamId} issue={issue} index={i} canWrite={canWrite}
                      current={inMeeting && state === "open" && i === 0} />
          ))}
        </ol>
      </CardContent>
    </Card>
  );
}

function NewIssue({ teamId }: { teamId: string }) {
  const [title, setTitle] = useState("");
  const [priority, setPriority] = useState(NONE);
  const create = useApiMutation(teamId, ["issues"],
    () => call.createIssue(teamId, title.trim(), priority === NONE ? null : Number(priority)));

  return (
    <form className="flex flex-wrap gap-2" onSubmit={(e) => {
      e.preventDefault();
      if (!title.trim()) return;
      create.mutate(undefined, { onSuccess: () => { setTitle(""); setPriority(NONE); } });
    }}>
      <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Add an Issue…" maxLength={200}
             aria-label="New Issue title" className="min-w-48 flex-1" />
      <PrioritySelect value={priority} onChange={setPriority} />
      <Button type="submit" disabled={!title.trim() || create.isPending}>Add</Button>
    </form>
  );
}

function PrioritySelect({ value, onChange, label = "Priority" }: { value: string; onChange: (v: string) => void; label?: string }) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger className="w-32" aria-label={label}><SelectValue /></SelectTrigger>
      <SelectContent>
        <SelectItem value={NONE}>No priority</SelectItem>
        {[5, 4, 3, 2, 1].map((p) => <SelectItem key={p} value={String(p)}>Priority {p}</SelectItem>)}
      </SelectContent>
    </Select>
  );
}

function IssueRow({ teamId, issue, index, canWrite, current }: { teamId: string; issue: Issue; index: number; canWrite: boolean; current: boolean }) {
  const [dialog, setDialog] = useState<null | "solve" | "todo">(null);
  const qc = useQueryClient();
  const prio = useApiMutation(teamId, ["issues"], (v: string) => call.setPriority(issue.id, v === NONE ? null : Number(v), issue.version), {
    quiet: (e) => {
      if (e.code !== "stale_write") return false;
      const p = e.current?.priority;
      toast.warning(`Someone changed this Issue to ${p ? `priority ${p}` : "no priority"} a moment ago. Your change wasn't saved.`);
      qc.invalidateQueries({ queryKey: keys.topic(teamId, "issues") });
      return true;
    },
  });
  const solved = !!issue.solved_at;

  return (
    <li className={cn("rounded-lg border p-3", current && "border-primary ring-1 ring-primary")} data-testid={`issue-${issue.id}`}>
      <div className="flex flex-wrap items-start gap-3">
        {!solved && <span className="w-5 pt-1 text-right text-sm text-muted-foreground tabular-nums">{index + 1}</span>}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{issue.title}</span>
            {current && <Badge>Discussing</Badge>}
          </div>
          <div className="mt-0.5 text-xs text-muted-foreground">
            {issue.owner.name} · raised {formatMoment(issue.created_at)}
            {issue.source && <> · from <b>{issue.source.measurable_name}</b>, week of {formatDay(issue.source.week_start)}</>}
            {issue.source_rock && <> · from Rock <b>{issue.source_rock.title}</b></>}
            {issue.source_todo && <> · from overdue To-Do <b>{issue.source_todo.title}</b></>}
          </div>
          {solved && (
            <div className="mt-2 rounded bg-muted px-2 py-1 text-sm">
              Solved {formatMoment(issue.solved_at!)} — {issue.resolution_note}
            </div>
          )}
        </div>
        {!solved && (canWrite ? (
          <div className="flex flex-wrap items-center gap-2">
            <PrioritySelect value={issue.priority ? String(issue.priority) : NONE} onChange={(v) => prio.mutate(v)}
                            label={`Priority for ${issue.title}`} />
            <Button size="sm" variant="outline" onClick={() => setDialog("todo")}>Add To-Do</Button>
            <Button size="sm" onClick={() => setDialog("solve")} data-testid={`solve-${issue.id}`}>Solve</Button>
          </div>
        ) : (
          <Badge variant="outline">{issue.priority ? `Priority ${issue.priority}` : "No priority"}</Badge>
        ))}
      </div>
      {dialog === "solve" && <SolveDialog teamId={teamId} issue={issue} onClose={() => setDialog(null)} />}
      {dialog === "todo" && <AddTodoDialog teamId={teamId} issue={issue} onClose={() => setDialog(null)} />}
    </li>
  );
}

function SolveDialog({ teamId, issue, onClose }: { teamId: string; issue: Issue; onClose: () => void }) {
  const [note, setNote] = useState("");
  const solve = useApiMutation(teamId, ["issues"], () => call.solve(issue.id, note.trim()), { success: "Issue solved" });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Solve “{issue.title}”</DialogTitle>
          <DialogDescription>Record how it was resolved. A note is required.</DialogDescription>
        </DialogHeader>
        <form id="solve" onSubmit={(e) => { e.preventDefault(); solve.mutate(undefined, { onSuccess: onClose }); }}>
          <Label htmlFor="note">Resolution note</Label>
          <Textarea id="note" value={note} onChange={(e) => setNote(e.target.value)} maxLength={2000} autoFocus className="mt-1" />
        </form>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" form="solve" disabled={!note.trim() || solve.isPending}>Solve</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function AddTodoDialog({ teamId, issue, onClose }: { teamId: string; issue: Issue; onClose: () => void }) {
  const members = useMembers(teamId);
  const owners = members.data?.filter((u) => u.role !== "observer") ?? [];
  const [title, setTitle] = useState("");
  const [owner, setOwner] = useState(issue.owner.id);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const add = useApiMutation(teamId, ["todos"], () => call.addTodo(issue.id, title.trim(), owner), {
    success: "To-Do added, due in 7 days",
    quiet: (e: ApiError) => { if (e.code === "invalid_owner") { setFieldError(e.message); return true; } return false; },
  });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add a To-Do</DialogTitle>
          <DialogDescription>For “{issue.title}”. Due in 7 days.</DialogDescription>
        </DialogHeader>
        <form id="todo" className="grid gap-3" onSubmit={(e) => { e.preventDefault(); add.mutate(undefined, { onSuccess: onClose }); }}>
          <div className="grid gap-1">
            <Label htmlFor="todo-title">Title</Label>
            <Input id="todo-title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} autoFocus />
          </div>
          <div className="grid gap-1">
            <Label>Owner</Label>
            <Select value={owner} onValueChange={(v) => { setOwner(v); setFieldError(null); }}>
              <SelectTrigger aria-label="Owner"><SelectValue placeholder="Choose an owner" /></SelectTrigger>
              <SelectContent>{owners.map((u) => <SelectItem key={u.id} value={u.id}>{u.name}</SelectItem>)}</SelectContent>
            </Select>
            {fieldError && <p className="text-sm text-destructive">{fieldError}</p>}
          </div>
        </form>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" form="todo" disabled={!title.trim() || !owner || add.isPending}>Add To-Do</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
