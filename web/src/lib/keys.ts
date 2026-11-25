/** Query keys. Everything for a team lives under ["team", teamId, topic] so an SSE hint maps to one prefix. */
export type Topic = "scorecard" | "issues" | "todos" | "rocks" | "meeting" | "members";

export const keys = {
  me: ["me"] as const,
  users: ["auth-users"] as const,
  team: (teamId: string) => ["team", teamId] as const,
  topic: (teamId: string, topic: Topic) => ["team", teamId, topic] as const,
  issues: (teamId: string, state: "open" | "solved") => ["team", teamId, "issues", state] as const,
  activeMeeting: (teamId: string) => ["team", teamId, "meeting", "active"] as const,
  meeting: (teamId: string, meetingId: string) => ["team", teamId, "meeting", meetingId] as const,
  summary: (teamId: string, meetingId: string) => ["team", teamId, "meeting", meetingId, "summary"] as const,
};

/** Which cached topics an SSE hint (or our own write) makes stale. */
export const AFFECTS: Record<string, Topic[]> = {
  scorecard: ["scorecard"],
  issues: ["issues", "scorecard"], // cells show their open Issue
  todos: ["todos", "meeting"], // Conclude lists the meeting's new To-Dos
  meeting: ["meeting"],
};
