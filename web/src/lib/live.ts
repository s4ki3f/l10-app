"use client";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { AFFECTS, keys } from "./keys";

export type LiveStatus = "connecting" | "live" | "reconnecting";

/**
 * One EventSource per team page. The server sends hints ({topic}); we mark those queries stale and
 * TanStack Query refetches through the normal, permission-checked API. Every (re)connect starts with
 * "hello", and we refetch the whole team then — that is how refreshes, late joins and dropped
 * connections all recover the current state.
 */
export function useTeamEvents(teamId: string): LiveStatus {
  const qc = useQueryClient();
  const [status, setStatus] = useState<LiveStatus>("connecting");

  useEffect(() => {
    const es = new EventSource(`/api/teams/${encodeURIComponent(teamId)}/events`);
    let seenHello = false;
    es.addEventListener("hello", () => {
      setStatus("live");
      if (seenHello) qc.invalidateQueries({ queryKey: keys.team(teamId) });
      seenHello = true;
    });
    es.addEventListener("change", (ev) => {
      const { topic } = JSON.parse((ev as MessageEvent).data) as { topic: string };
      for (const t of AFFECTS[topic] ?? []) qc.invalidateQueries({ queryKey: keys.topic(teamId, t) });
    });
    es.onerror = () => setStatus("reconnecting"); // the browser retries on its own (server sends retry: 1000)
    return () => es.close();
  }, [teamId, qc]);

  return status;
}
