"""Small, structured progress messages shared with the dashboard through the run log."""
import json
from datetime import datetime, timezone

PREFIX = "APPLI_ACTIVITY "


def report(stage: str, message: str, job: dict | None = None, *, background: bool = False):
    event = {"stage": stage, "message": message, "at": datetime.now(timezone.utc).isoformat(),
             "background": background}
    if job:
        event.update(job_id=job["id"], company=job.get("company"), role=job.get("role"))
    print(PREFIX + json.dumps(event, ensure_ascii=True), flush=True)


def read_activity(lines: list[str]) -> dict:
    events = []
    current = None
    for line in lines:
        if not line.startswith(PREFIX):
            continue
        try:
            event = json.loads(line[len(PREFIX):])
            if not isinstance(event, dict) or not isinstance(event.get("message"), str):
                continue
        except (ValueError, TypeError):
            continue
        events.append(event)
        if not event.get("background"):
            current = event
    return {"current": current, "events": events[-8:]}
