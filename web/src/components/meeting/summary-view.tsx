"use client";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { Summary } from "@/lib/api/types";
import { formatDay, formatMoment } from "@/lib/time";

const STATUS = {
  on_track: { label: "On track", cls: "border-success text-success" },
  off_track: { label: "Off track", cls: "border-destructive text-destructive" },
  pending: { label: "Pending", cls: "" },
} as const;

/** What attendees see when the facilitator ends the meeting (also served for any ended meeting). */
export function SummaryView({ summary: s, teamId }: { summary: Summary; teamId: string }) {
  return (
    <div className="grid gap-6" data-testid="summary">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">Meeting summary</h1>
          <p className="text-sm text-muted-foreground">
            {formatMoment(s.started_at)} – {formatMoment(s.ended_at)} · reviewed the week of {formatDay(s.scorecard_week)}
          </p>
        </div>
        <div className="text-right">
          <div className="text-xs tracking-wide text-muted-foreground uppercase">Rating</div>
          <div className="text-3xl font-semibold tabular-nums" data-testid="summary-rating">{s.rating?.toFixed(1) ?? "—"}</div>
        </div>
      </div>
      <div className="grid gap-6 md:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>Scorecard</CardTitle><CardDescription>Week of {formatDay(s.scorecard_week)}</CardDescription></CardHeader>
          <CardContent className="grid gap-1 text-sm">
            {s.measurable_status.map((m) => (
              <div key={m.id} className="flex justify-between gap-2">
                <span>{m.name}</span>
                <Badge variant="outline" className={STATUS[m.status].cls}>{STATUS[m.status].label}</Badge>
              </div>
            ))}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Attendees</CardTitle><CardDescription>{s.attendees.length} people</CardDescription></CardHeader>
          <CardContent className="text-sm">{s.attendees.map((a) => a.name).join(", ")}</CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Issues solved</CardTitle></CardHeader>
          <CardContent className="text-sm">
            {s.issues_solved.length ? <ul className="list-disc pl-5">{s.issues_solved.map((i) => <li key={i.id}>{i.name}</li>)}</ul>
              : <p className="text-muted-foreground">None.</p>}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>To-Dos created</CardTitle></CardHeader>
          <CardContent className="text-sm">
            {s.todos_created.length ? <ul className="list-disc pl-5">{s.todos_created.map((t) => <li key={t.id}>{t.name}</li>)}</ul>
              : <p className="text-muted-foreground">None.</p>}
          </CardContent>
        </Card>
      </div>
      <Link href={`/teams/${teamId}`} className="text-sm underline">Back to the team</Link>
    </div>
  );
}
