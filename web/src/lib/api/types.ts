import type { components } from "./schema";

type S = components["schemas"];
export type Me = S["MeOut"];
export type User = S["UserOut"];
export type Scorecard = S["ScorecardOut"];
export type ScorecardRow = S["ScorecardRow"];
export type Cell = S["CellOut"];
export type Issue = S["IssueOut"];
export type Todo = S["TodoOut"];
export type TodoList = S["TodoList"];
export type Rock = S["RockOut"];
export type MeetingState = S["MeetingState"];
export type Summary = S["SummaryOut"];
export type Section = MeetingState["section"];
