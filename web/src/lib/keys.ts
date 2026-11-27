/** Query keys. Everything for a team lives under ["team", teamId, topic] so an SSE hint maps to one prefix. */
export type Topic = "scorecard" | "issues" | "todos" | "rocks" | "meeting" | "members" | "activity";

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
  // every change is also an audit event, so every hint refreshes the Activity feed
  scorecard: ["scorecard", "activity"],
  issues: ["issues", "scorecard", "rocks", "todos", "activity"], // cells, Rocks and To-Dos show their open Issue
  todos: ["todos", "meeting", "activity"], // Conclude lists the meeting's new To-Dos
  meeting: ["meeting", "activity"],
};
