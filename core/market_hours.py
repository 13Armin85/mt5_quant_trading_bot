"""Configured quote schedules explain closures; they never prove that quotes are fresh."""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def quote_session(schedule, now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Quote schedule requires timezone-aware now")
    if not schedule:
        return {"scheduled_closed":False,"schedule_known":False,"next_open_utc":None,"next_open_tehran":None}
    kind = schedule["type"]
    zone = ZoneInfo(schedule["timezone"])
    local = now.astimezone(zone)
    if kind not in {"litefinance_forex","weekend_only"}:
        raise ValueError("Unsupported quote schedule")
    closed = local.weekday() >= 5
    next_open = None
    if kind == "litefinance_forex":
        monday_open = time(0,5,1)
        closed = closed or (local.weekday()==0 and local.time()<monday_open)
        if closed:
            days = (7-local.weekday()) % 7
            date = local.date()+timedelta(days=days)
            # Construct in the date's own timezone: DST may change over the weekend.
            next_open = datetime.combine(date,monday_open,tzinfo=zone).astimezone(timezone.utc)
    return {"scheduled_closed":closed,"schedule_known":True,"schedule_type":kind,
            "schedule_note":"Weekly schedule only; holidays and symbol-specific session breaks may differ",
            "next_open_utc":next_open.isoformat() if next_open else None,
            "next_open_tehran":next_open.astimezone(ZoneInfo("Asia/Tehran")).isoformat() if next_open else None}
