"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, ApiError, unwrap } from "./api/client";
import type { Me, Section } from "./api/types";
import { AFFECTS, keys, type Topic } from "./keys";

const team = (team_id: string) => ({ params: { path: { team_id } } });

// ---------- reads ----------

export const useMe = () =>
  useQuery({ queryKey: keys.me, queryFn: async () => unwrap(await api.GET("/me")), retry: false });

export const useSignInUsers = () =>
  useQuery({ queryKey: keys.users, queryFn: async () => unwrap(await api.GET("/auth/users")) });

export const useMembers = (teamId: string) =>
  useQuery({
    queryKey: keys.topic(teamId, "members"),
    queryFn: async () => unwrap(await api.GET("/teams/{team_id}/members", team(teamId))),
  });

export const useScorecard = (teamId: string) =>
  useQuery({
    queryKey: keys.topic(teamId, "scorecard"),
    queryFn: async () => unwrap(await api.GET("/teams/{team_id}/scorecard", team(teamId))),
  });

export const useIssues = (teamId: string, state: "open" | "solved") =>
  useQuery({
    queryKey: keys.issues(teamId, state),
    queryFn: async () =>
      unwrap(await api.GET("/teams/{team_id}/issues", { params: { path: { team_id: teamId }, query: { state } } })),
  });

export const useTodos = (teamId: string) =>
  useQuery({
    queryKey: keys.topic(teamId, "todos"),
    queryFn: async () => unwrap(await api.GET("/teams/{team_id}/todos", team(teamId))),
  });

export const useRocks = (teamId: string) =>
  useQuery({
    queryKey: keys.topic(teamId, "rocks"),
    queryFn: async () => unwrap(await api.GET("/teams/{team_id}/rocks", team(teamId))),
  });

export const useActiveMeeting = (teamId: string) =>
  useQuery({
    queryKey: keys.activeMeeting(teamId),
    queryFn: async () => unwrap(await api.GET("/teams/{team_id}/meetings/active", team(teamId))),
  });

export const useMeeting = (teamId: string, meetingId: string) =>
  useQuery({
    queryKey: keys.meeting(teamId, meetingId),
    queryFn: async () =>
      unwrap(await api.GET("/meetings/{meeting_id}", { params: { path: { meeting_id: meetingId } } })),
  });

export const useSummary = (teamId: string, meetingId: string, enabled: boolean) =>
  useQuery({
    queryKey: keys.summary(teamId, meetingId),
    enabled,
    queryFn: async () =>
      unwrap(await api.GET("/meetings/{meeting_id}/summary", { params: { path: { meeting_id: meetingId } } })),
  });

// ---------- writes ----------

/**
 * Every write: call the API, then mark its topics stale here at once (other browsers learn via SSE).
 * Errors arrive in one shape and are shown as a toast unless the caller handles them (e.g. inline).
 */
export function useApiMutation<V, R>(
  teamId: string,
  topics: Topic[],
  fn: (vars: V) => Promise<R>,
  opts: { success?: string; quiet?: (e: ApiError) => boolean; onData?: (data: R) => void } = {},
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (data) => {
      opts.onData?.(data);
      // the same expansion the live hints use, so this browser never waits on its own echo
      const stale = new Set(topics.flatMap((t) => [t, ...(AFFECTS[t] ?? [])]));
      for (const t of stale) qc.invalidateQueries({ queryKey: keys.topic(teamId, t) });
      if (opts.success) toast.success(opts.success);
    },
    onError: (e) => {
      if (e instanceof ApiError && opts.quiet?.(e)) return;
      // validation errors carry per-field messages; show the first one instead of the generic summary
      toast.error(e instanceof ApiError ? (e.fields[0]?.message ?? e.message) : "Something went wrong");
    },
  });
}

export const call = {
  /** expectedVersion = the version on screen; a 409 stale_write means someone saved first */
  putScore: (measurableId: string, week: string, value: string, expectedVersion: number) =>
    api.PUT("/measurables/{measurable_id}/weeks/{week}/score", {
      params: { path: { measurable_id: measurableId, week } },
      body: { value, expected_version: expectedVersion },
    }).then(unwrap),
  makeIssue: (measurableId: string, week: string) =>
    api.POST("/measurables/{measurable_id}/weeks/{week}/issue", {
      params: { path: { measurable_id: measurableId, week } },
    }).then(unwrap),
  createIssue: (teamId: string, title: string, priority: number | null) =>
    api.POST("/teams/{team_id}/issues", { ...team(teamId), body: { title, priority } }).then(unwrap),
  setPriority: (issueId: string, priority: number | null, expectedVersion: number) =>
    api.PATCH("/issues/{issue_id}", {
      params: { path: { issue_id: issueId } },
      body: { priority, expected_version: expectedVersion },
    }).then(unwrap),
  solve: (issueId: string, note: string) =>
    api.POST("/issues/{issue_id}/solve", { params: { path: { issue_id: issueId } }, body: { note } }).then(unwrap),
  addTodo: (issueId: string, title: string, ownerId: string) =>
    api.POST("/issues/{issue_id}/todos", {
      params: { path: { issue_id: issueId } },
      body: { title, owner_id: ownerId },
    }).then(unwrap),
  setDone: (todoId: string, done: boolean) =>
    (done
      ? api.POST("/todos/{todo_id}/done", { params: { path: { todo_id: todoId } } })
      : api.POST("/todos/{todo_id}/undo", { params: { path: { todo_id: todoId } } })
    ).then(unwrap),
  startMeeting: (teamId: string) => api.POST("/teams/{team_id}/meetings", team(teamId)).then(unwrap),
  join: (meetingId: string) =>
    api.POST("/meetings/{meeting_id}/join", { params: { path: { meeting_id: meetingId } } }).then(unwrap),
  advance: (meetingId: string, from: Section) =>
    api.POST("/meetings/{meeting_id}/advance", {
      params: { path: { meeting_id: meetingId } },
      body: { from_section: from },
    }).then(unwrap),
  end: (meetingId: string) =>
    api.POST("/meetings/{meeting_id}/end", { params: { path: { meeting_id: meetingId } } }).then(unwrap),
  rate: (meetingId: string, rating: number) =>
    api.PUT("/meetings/{meeting_id}/rating", { params: { path: { meeting_id: meetingId } }, body: { rating } }).then(unwrap),
  login: (userId: string) => api.POST("/auth/login", { body: { user_id: userId } }).then(unwrap),
  logout: () => api.POST("/auth/logout"),
};

export const canWrite = (me: Me | undefined) => !!me?.can_write;
