"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { roleLabel } from "@/components/role";
import { ApiError } from "@/lib/api/client";
import type { Me } from "@/lib/api/types";
import { useTeamEvents, type LiveStatus } from "@/lib/live";
import { call, useActiveMeeting, useMe } from "@/lib/queries";
import { cn } from "@/lib/utils";

/** Header + live subscription for every team page. Children get the signed-in user. */
export function TeamShell({ teamId, children }: { teamId: string; children: (me: Me) => React.ReactNode }) {
  const me = useMe();
  const router = useRouter();
  const pathname = usePathname();
  const qc = useQueryClient();
  const live = useTeamEvents(teamId);
  const active = useActiveMeeting(teamId);

  useEffect(() => {
    if (me.error instanceof ApiError && me.error.status === 401) router.replace("/login");
  }, [me.error, router]);

  if (!me.data) return <div className="p-6"><Skeleton className="h-10 w-full" /></div>;
  const team = me.data.teams.find((t) => t.id === teamId);
  const meeting = active.data?.meeting;

  async function signOut() {
    await call.logout();
    qc.clear();
    router.replace("/login");
  }

  return (
    <div className="min-h-dvh">
      <header className="sticky top-0 z-20 border-b bg-background/95 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-3 px-4 py-2">
          <Link href={`/teams/${teamId}`} className="font-semibold tracking-tight">L10</Link>
          <Select value={team ? teamId : undefined} onValueChange={(id) => router.push(`/teams/${id}`)}>
            <SelectTrigger className="w-44" aria-label="Team" data-testid="team-switcher">
              <SelectValue placeholder="Choose a team" />
            </SelectTrigger>
            <SelectContent>
              {me.data.teams.map((t) => <SelectItem key={t.id} value={t.id}>{t.name}</SelectItem>)}
            </SelectContent>
          </Select>
          <LiveDot status={live} />
          <div className="ml-auto flex items-center gap-2">
            {team && (pathname.includes("/meeting") ? (
              <Button asChild size="sm" variant="ghost"><Link href={`/teams/${teamId}`}>← Team page</Link></Button>
            ) : meeting ? (
              <Button asChild size="sm" data-testid="join-meeting">
                <Link href={`/teams/${teamId}/meetings/${meeting.id}`}>● Meeting in progress — join</Link>
              </Button>
            ) : me.data.can_write ? (
              <Button asChild size="sm" variant="outline" data-testid="open-meeting">
                <Link href={`/teams/${teamId}/meeting`}>Start meeting</Link>
              </Button>
            ) : null)}
            <span className="hidden text-sm text-muted-foreground sm:inline">
              {me.data.user.name} · {roleLabel(me.data.user.role)}
            </span>
            {!me.data.can_write && <Badge variant="secondary">Read only</Badge>}
            <Button size="sm" variant="ghost" onClick={signOut}>Sign out</Button>
          </div>
        </div>
      </header>
      {team ? (
        <main className="mx-auto max-w-7xl min-w-0 px-4 py-6">{children(me.data)}</main>
      ) : (
        <main className="mx-auto max-w-7xl px-4 py-16 text-center">
          <h1 className="text-lg font-semibold">Team not found</h1>
          <p className="text-muted-foreground">It doesn&apos;t exist or you&apos;re not a member.</p>
        </main>
      )}
    </div>
  );
}

function LiveDot({ status }: { status: LiveStatus }) {
  const label = { connecting: "Connecting…", live: "Live", reconnecting: "Reconnecting…" }[status];
  return (
    <span className="flex items-center gap-1.5 text-xs text-muted-foreground" data-testid="live-status" data-status={status}>
      <span className={cn("size-2 rounded-full", status === "live" ? "bg-success" : "bg-warning animate-pulse")} />
      {label}
    </span>
  );
}
