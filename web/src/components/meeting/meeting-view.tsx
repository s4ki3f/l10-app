"use client";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { IssuesSection } from "@/components/sections/issues-section";
import { ScorecardSection } from "@/components/sections/scorecard-section";
import { ApiError } from "@/lib/api/client";
import type { MeetingState, Me } from "@/lib/api/types";
import { keys } from "@/lib/keys";
import { call, useApiMutation, useMeeting, useSummary } from "@/lib/queries";
import { formatDay, formatDuration } from "@/lib/time";
import { cn } from "@/lib/utils";
import { SummaryView } from "./summary-view";

/** The live meeting. The server owns section and timer; this view renders whatever the latest state says. */
export function MeetingView({ teamId, meetingId, me }: { teamId: string; meetingId: string; me: Me }) {
  const q = useMeeting(teamId, meetingId);
  const qc = useQueryClient();
  const state = q.data;
  const ended = !!state?.ended_at;
  const summary = useSummary(teamId, meetingId, ended);
  const joined = useRef(false);

  const attending = !!state?.attendees.some((a) => a.id === me.user.id);
  const join = () =>
    call.join(meetingId)
      .then((s) => qc.setQueryData<MeetingState>(keys.meeting(teamId, meetingId), s))
      .catch(() => { joined.current = false; }); // the Join button stays visible, so the user can retry

  // Anyone who can read the team joins automatically, once per page load (re-joining is idempotent).
  useEffect(() => {
    if (!state || ended || joined.current || attending) return;
    joined.current = true;
    join();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state, ended, attending]);

  if (q.error instanceof ApiError && q.error.status === 404) return <p className="text-center">Meeting not found.</p>;
  if (!state) return <Skeleton className="h-64" />;
  if (ended) return summary.data ? <SummaryView summary={summary.data} teamId={teamId} /> : <Skeleton className="h-64" />;

  const facilitating = state.facilitator.id === me.user.id;
  return (
    <div className="grid gap-6 [&>*]:min-w-0">
      <MeetingBar state={state} teamId={teamId} facilitating={facilitating} receivedAt={q.dataUpdatedAt}
                  attending={attending} onJoin={join} />
      {state.section === "scorecard" && <ScorecardSection teamId={teamId} canWrite={me.can_write} focusWeek="last_closed" />}
      {state.section === "ids" && <IssuesSection teamId={teamId} canWrite={me.can_write} inMeeting />}
      {state.section === "conclude" && <Conclude state={state} me={me} teamId={teamId} />}
    </div>
  );
}

function useElapsed(state: MeetingState, receivedAt: number) {
  // offset = server clock − browser clock, measured when this state arrived; a wrong laptop clock cancels out
  const offset = Date.parse(state.server_now) - receivedAt;
  const [nowMs, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(t);
  }, []);
  return Math.max(0, (nowMs + offset - Date.parse(state.section_started_at)) / 1000);
}

function MeetingBar({ state, teamId, facilitating, receivedAt, attending, onJoin }: {
  state: MeetingState; teamId: string; facilitating: boolean; receivedAt: number; attending: boolean; onJoin: () => void;
}) {
  const qc = useQueryClient();
  const keep = (s: MeetingState) => qc.setQueryData<MeetingState>(keys.meeting(teamId, s.id), s); // the server's answer is the new state
  const elapsed = useElapsed(state, receivedAt);
  const limit = state.section_limit_seconds;
  const over = elapsed > limit;
  const index = state.agenda.findIndex((a) => a.key === state.section);
  const next = state.agenda[index + 1];
  const advance = useApiMutation(teamId, ["meeting"], () => call.advance(state.id, state.section), { onData: keep });
  const end = useApiMutation(teamId, ["meeting"], () => call.end(state.id), { onData: keep });

  return (
    <section className={cn("sticky top-[53px] z-10 rounded-xl border bg-card p-4 shadow-sm", over && "border-destructive")}
             aria-label="Meeting status" data-testid="meeting-bar" data-section={state.section}>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <div>
          <div className="text-xs tracking-wide text-muted-foreground uppercase">Now</div>
          <h1 className="text-3xl font-bold tracking-tight sm:text-4xl" data-testid="current-section">
            {state.agenda[index].title}
          </h1>
        </div>
        <div aria-live="polite">
          <div className="text-xs tracking-wide text-muted-foreground uppercase">
            {over ? "Over time" : `of ${state.agenda[index].minutes} min`}
          </div>
          <div className={cn("text-3xl font-semibold tabular-nums sm:text-4xl", over && "text-destructive")} data-testid="timer">
            {over ? `+${formatDuration(elapsed - limit)}` : formatDuration(elapsed)}
          </div>
        </div>
        {over && <Badge variant="destructive" className="text-sm">Over time</Badge>}
        <ol className="flex flex-wrap gap-1 text-sm" aria-label="Agenda">
          {state.agenda.map((a, i) => (
            <li key={a.key} aria-current={i === index ? "step" : undefined}
                className={cn("rounded-full border px-3 py-1", i === index ? "border-primary bg-primary text-primary-foreground" : i < index ? "text-muted-foreground line-through" : "")}>
              {a.title} · {a.minutes}m
            </li>
          ))}
        </ol>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          {facilitating ? (
            <>
              {next && (
                <Button size="lg" onClick={() => advance.mutate(undefined)} disabled={advance.isPending} data-testid="advance">
                  Next: {next.title} →
                </Button>
              )}
              <AlertDialog>
                <AlertDialogTrigger asChild>
                  <Button size="lg" variant={next ? "outline" : "default"} data-testid="end-meeting">End meeting</Button>
                </AlertDialogTrigger>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>End the meeting?</AlertDialogTitle>
                    <AlertDialogDescription>Everyone will see the summary. Ratings close.</AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>Keep going</AlertDialogCancel>
                    <AlertDialogAction onClick={() => end.mutate(undefined)} data-testid="confirm-end">End meeting</AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            </>
          ) : (
            <span className="text-sm text-muted-foreground">Facilitator: <b>{state.facilitator.name}</b></span>
          )}
        </div>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-1.5 text-xs" aria-label="Attendees" data-testid="attendees">
        {!attending && (
          <Button size="sm" variant="outline" className="h-6 px-2 text-xs" onClick={onJoin} data-testid="join-button">Join</Button>
        )}
        {state.attendees.map((a) => (
          <span key={a.id} className="rounded-full bg-muted px-2 py-0.5">
            {a.name}{a.id === state.facilitator.id ? " (facilitator)" : a.role === "observer" ? " (observer)" : ""}
          </span>
        ))}
      </div>
    </section>
  );
}

function Conclude({ state, me, teamId }: { state: MeetingState; me: Me; teamId: string }) {
  const attending = state.attendees.some((a) => a.id === me.user.id);
  const mine = state.ratings.find((r) => r.user_id === me.user.id)?.rating;
  const qc = useQueryClient();
  const rate = useApiMutation(teamId, ["meeting"], (n: number) => call.rate(state.id, n), {
    onData: (s) => qc.setQueryData<MeetingState>(keys.meeting(teamId, s.id), s),
  });
  const names = Object.fromEntries(state.attendees.map((a) => [a.id, a.name]));

  return (
    <div className="grid gap-6 lg:grid-cols-2 [&>*]:min-w-0">
      <Card>
        <CardHeader>
          <CardTitle>New To-Dos</CardTitle>
          <CardDescription>Created during this meeting. Confirm each owner knows.</CardDescription>
        </CardHeader>
        <CardContent>
          {state.new_todos.length === 0 ? <p className="text-sm text-muted-foreground">No new To-Dos.</p> : (
            <ul className="grid gap-1 text-sm" data-testid="new-todos">
              {state.new_todos.map((t) => (
                <li key={t.id} className="flex justify-between gap-3 border-b py-1.5 last:border-0">
                  <span>{t.title}</span>
                  <span className="text-muted-foreground">{t.owner.name} · due {formatDay(t.due_date)}</span>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Rate this meeting</CardTitle>
          <CardDescription>
            1 to 10. You can change it until the meeting ends.
            {state.average_rating !== null && <> Average so far: <b>{state.average_rating.toFixed(1)}</b> ({state.ratings.length}).</>}
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          {me.can_write && attending ? (
            <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="Your rating">
              {Array.from({ length: 10 }, (_, i) => i + 1).map((n) => (
                <Button key={n} size="sm" variant={mine === n ? "default" : "outline"} role="radio" aria-checked={mine === n}
                        className="w-10" disabled={rate.isPending} onClick={() => { if (mine !== n) rate.mutate(n); }} data-testid={`rate-${n}`}>{n}</Button>
              ))}
            </div>
          ) : <p className="text-sm text-muted-foreground">{me.can_write ? "Join the meeting to rate it." : "Observers don't rate."}</p>}
          <ul className="grid gap-1 text-sm">
            {state.ratings.map((r) => (
              <li key={r.user_id} className="flex justify-between"><span>{names[r.user_id] ?? r.user_id}</span><b>{r.rating}</b></li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}
