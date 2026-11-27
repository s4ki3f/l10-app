"use client";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { AFFECTS, keys } from "./keys";

export type LiveStatus = "connecting" | "live" | "reconnecting";
const POLL_WHILE_DOWN_MS = 5_000;

/**
 * One EventSource per team page. The server sends hints ({topic}); we mark those queries stale and
 * TanStack Query refetches through the normal, permission-checked API.
 *
 * Listen, then fetch: the server sends "hello" only after this browser is subscribed (and its LISTEN
 * connection is up), so refetching on EVERY hello — the first one included — picks up anything that
 * changed before the subscription existed. Skipping the first one lost changes that landed between the
 * page's initial fetch and the subscription (found by an external review).
 */
export function useTeamEvents(teamId: string): LiveStatus {
  const qc = useQueryClient();
  const [status, setStatus] = useState<LiveStatus>("connecting");

  useEffect(() => {
    const es = new EventSource(`/api/teams/${encodeURIComponent(teamId)}/events`);
    es.addEventListener("hello", () => {
      setStatus("live");
      qc.invalidateQueries({ queryKey: keys.team(teamId) });
    });
    es.addEventListener("change", (ev) => {
      const { topic } = JSON.parse((ev as MessageEvent).data) as { topic: string };
      for (const t of AFFECTS[topic] ?? []) qc.invalidateQueries({ queryKey: keys.topic(teamId, t) });
    });
    es.onerror = () => setStatus("reconnecting"); // the browser retries on its own (server sends retry: 1000)
    return () => es.close();
  }, [teamId, qc]);

  // Degrade, don't freeze: while the stream is down, refetch the team every few seconds.
  useEffect(() => {
    if (status !== "reconnecting") return;
    const t = setInterval(() => qc.invalidateQueries({ queryKey: keys.team(teamId) }), POLL_WHILE_DOWN_MS);
    return () => clearInterval(t);
  }, [status, teamId, qc]);

  return status;
}
