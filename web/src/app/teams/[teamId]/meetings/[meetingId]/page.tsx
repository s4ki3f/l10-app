"use client";
import { useParams } from "next/navigation";
import { MeetingView } from "@/components/meeting/meeting-view";
import { TeamShell } from "@/components/team-shell";

export default function MeetingPage() {
  const { teamId, meetingId } = useParams<{ teamId: string; meetingId: string }>();
  return <TeamShell teamId={teamId}>{(me) => <MeetingView teamId={teamId} meetingId={meetingId} me={me} />}</TeamShell>;
}
