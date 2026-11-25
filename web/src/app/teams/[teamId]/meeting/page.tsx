"use client";
import { useParams, useRouter } from "next/navigation";
import { useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { TeamShell } from "@/components/team-shell";
import { call, useActiveMeeting, useApiMutation } from "@/lib/queries";

/** Resolves "the team's meeting": go to the live one, or offer to start it. */
export default function MeetingEntry() {
  const { teamId } = useParams<{ teamId: string }>();
  const router = useRouter();
  const active = useActiveMeeting(teamId);
  const start = useApiMutation(teamId, ["meeting"], () => call.startMeeting(teamId));
  const live = active.data?.meeting;

  useEffect(() => {
    if (live) router.replace(`/teams/${teamId}/meetings/${live.id}`);
  }, [live, teamId, router]);

  return (
    <TeamShell teamId={teamId}>
      {(me) => (
        <Card className="mx-auto max-w-lg">
          <CardHeader>
            <CardTitle>Level 10 Meeting</CardTitle>
            <CardDescription>Scorecard 5 min · IDS 60 min · Conclude 10 min</CardDescription>
          </CardHeader>
          <CardContent>
            {active.isLoading || live ? <p className="text-sm text-muted-foreground">Opening…</p> : me.can_write ? (
              <Button size="lg" disabled={start.isPending} data-testid="start-meeting"
                      onClick={() => start.mutate(undefined, { onSuccess: (m) => router.replace(`/teams/${teamId}/meetings/${m.id}`) })}>
                Start the meeting — you&apos;ll facilitate
              </Button>
            ) : <p className="text-sm text-muted-foreground">No meeting is running. An Admin or Team Member can start one.</p>}
          </CardContent>
        </Card>
      )}
    </TeamShell>
  );
}
