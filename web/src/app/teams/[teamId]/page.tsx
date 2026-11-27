"use client";
import { useParams } from "next/navigation";
import { ActivitySection } from "@/components/sections/activity-section";
import { IssuesSection } from "@/components/sections/issues-section";
import { RocksSection } from "@/components/sections/rocks-section";
import { ScorecardSection } from "@/components/sections/scorecard-section";
import { TodosSection } from "@/components/sections/todos-section";
import { TeamShell } from "@/components/team-shell";

/** Team page: the week's loop on one screen — Measure (Scorecard), Resolve (Issues), Commit (To-Dos, Rocks). */
export default function TeamPage() {
  const { teamId } = useParams<{ teamId: string }>();
  return (
    <TeamShell teamId={teamId}>
      {(me) => (
        <div className="grid gap-6 [&>*]:min-w-0">
          <ScorecardSection teamId={teamId} canWrite={me.can_write} />
          <div className="grid gap-6 lg:grid-cols-2 [&>*]:min-w-0">
            <IssuesSection teamId={teamId} canWrite={me.can_write} />
            <TodosSection teamId={teamId} canWrite={me.can_write} />
          </div>
          <div className="grid gap-6 lg:grid-cols-2 [&>*]:min-w-0">
            <RocksSection teamId={teamId} canWrite={me.can_write} />
            <ActivitySection teamId={teamId} />
          </div>
        </div>
      )}
    </TeamShell>
  );
}
