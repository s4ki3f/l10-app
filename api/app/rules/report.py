"""The rules report (format in docs/starter-README.md). Every value comes from engine.py."""
from datetime import datetime, timezone

from . import engine
from .dataset import Dataset
from .weeks import quarter_of, local_date, scorecard_weeks


def _iso_utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rating(value):
    return None if value is None else float(value)


def build_report(data: Dataset, now: datetime) -> dict:
    weeks = scorecard_weeks(now)
    current_week = weeks[-1]
    quarter = quarter_of(local_date(now))
    teams = {}
    for team_id in data.team_ids:
        measurables = [m for m in data.measurables if m.team_id == team_id]
        todos = [t for t in data.todos if t.team_id == team_id]
        rocks = [r for r in data.rocks if r.team_id == team_id and r.quarter == quarter]
        issues = [i for i in data.issues if i.team_id == team_id]
        meetings = [m for m in data.meetings if m.team_id == team_id and engine.happened(m.ended_at, now)]

        teams[team_id] = {
            "measurable_status": {
                m.id: {
                    w.isoformat(): engine.measurable_status(m.goal, data.scores.get((m.id, w)), w, current_week)
                    for w in weeks
                }
                for m in measurables
            },
            "todo_completion": {w.isoformat(): engine.todo_completion(todos, w, now) for w in weeks},
            "overdue_todos": sorted(t.id for t in todos if t.created_at <= now and engine.is_overdue(t, now)),
            "rocks": {
                r.id: {
                    "status": engine.rock_status(r, ms := [x for x in data.milestones if x.rock_id == r.id], now),
                    "progress_percent": engine.rock_progress(r, ms),
                }
                for r in rocks
            },
            "issue_order": [i.id for i in engine.issue_order(issues, now)],
            "meetings": {
                m.id: _summary_json(engine.meeting_summary(m, data.measurables, data.scores, data.issues, data.todos))
                for m in sorted(meetings, key=lambda m: m.id)
            },
        }
    return {"now": _iso_utc(now), "teams": teams}


def _summary_json(summary: dict) -> dict:
    return {**summary, "scorecard_week": summary["scorecard_week"].isoformat(), "rating": _rating(summary["rating"])}
