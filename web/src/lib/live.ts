"use client";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { AFFECTS, keys } from "./keys";

export type LiveStatus = "connecting" | "live" | "reconnecting";
const POLL_WHILE_DOWN_MS = 5_000;
const REOPEN_AFTER_MS = 3_000;

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
  const [generation, setGeneration] = useState(0); // bumping it opens a fresh EventSource

  useEffect(() => {
    const es = new EventSource(`/api/teams/${encodeURIComponent(teamId)}/events`);
    let reopen: ReturnType<typeof setTimeout> | undefined;
    es.addEventListener("hello", () => {
      setStatus("live");
      qc.invalidateQueries({ queryKey: keys.team(teamId) });
    });
    es.addEventListener("change", (ev) => {
      const { topic } = JSON.parse((ev as MessageEvent).data) as { topic: string };
      if (topic === "resync") {
        qc.invalidateQueries({ queryKey: keys.team(teamId) }); // the server may have missed hints for us
        return;
      }
      for (const t of AFFECTS[topic] ?? []) qc.invalidateQueries({ queryKey: keys.topic(teamId, t) });
    });
    es.onerror = () => {
      setStatus("reconnecting");
      // A dropped stream retries by itself (server sends retry: 1000). But a non-200 answer — the API
      // restarting behind the proxy, or a 503 while LISTEN is down — closes it for good, so reopen it.
      if (es.readyState === EventSource.CLOSED) reopen = setTimeout(() => setGeneration((g) => g + 1), REOPEN_AFTER_MS);
    };
    return () => {
      clearTimeout(reopen);
      es.close();
    };
  }, [teamId, qc, generation]);

  // Degrade, don't freeze: while the stream is down, refetch the team every few seconds.
  useEffect(() => {
    if (status !== "reconnecting") return;
    const t = setInterval(() => qc.invalidateQueries({ queryKey: keys.team(teamId) }), POLL_WHILE_DOWN_MS);
    return () => clearInterval(t);
  }, [status, teamId, qc]);

  return status;
}
