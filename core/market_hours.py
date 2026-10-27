"""Configured quote schedules explain closures; they never prove that quotes are fresh."""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def _utc(value):
    if value.tzinfo is None:
        raise ValueError("Market times require timezone-aware datetimes")
    return value.astimezone(timezone.utc)


def _boundary(day, text, zone, opening):
    if text=="24:00":
        day+=timedelta(days=1);text="00:00"
    wall=datetime.combine(day,time.fromisoformat(text))
    candidates=[]
    for fold in (0,1):
        candidate=wall.replace(tzinfo=zone,fold=fold).astimezone(timezone.utc)
        if candidate.astimezone(zone).replace(tzinfo=None)==wall:
            candidates.append(candidate)
    if not candidates:
        raise ValueError("Session boundary falls in a nonexistent DST hour")
    return max(candidates) if opening else min(candidates)


def _windows(schedule, day):
    zone=ZoneInfo(schedule["timezone"])
    kind=schedule["type"]
    overrides=schedule.get("exceptions",{})
    if day.isoformat() in overrides:
        intervals=overrides[day.isoformat()]
    elif kind=="weekly_sessions":
        intervals=schedule["weekly"].get(day.weekday(),schedule["weekly"].get(str(day.weekday()),[]))
    elif day.weekday()>=5:
        intervals=[]
    else:
        start="00:05:01" if kind=="litefinance_forex" and day.weekday()==0 else "00:00"
        intervals=[[start,"24:00"]]
    if not isinstance(intervals,list) or any(not isinstance(pair,list) or len(pair)!=2 or not all(isinstance(t,str) for t in pair) for pair in intervals):
        raise ValueError("Session intervals must be lists of start/end clock strings")
    result=[]
    for start,end in intervals:
        opened,closed=_boundary(day,start,zone,True),_boundary(day,end,zone,False)
        if closed<=opened:
            raise ValueError("Session end must follow start; split overnight sessions by weekday")
        result.append((opened,closed))
    result.sort()
    if any(result[i][0]<result[i-1][1] for i in range(1,len(result))):
        raise ValueError("Configured sessions overlap")
    return result


def validate_schedule(schedule):
    if schedule.get("type") not in {"litefinance_forex","weekend_only","weekly_sessions"}:
        raise ValueError("Unsupported quote schedule")
    ZoneInfo(schedule["timezone"])
    if schedule["type"]=="weekly_sessions":
        weekly=schedule.get("weekly")
        if not isinstance(weekly,dict) or not weekly or any(str(k) not in set("0123456") for k in weekly):
            raise ValueError("weekly_sessions requires weekday keys 0..6")
        for key in weekly:
            _windows(schedule,datetime(2026,1,5).date()+timedelta(days=int(key)))
    for date in schedule.get("exceptions",{}):
        _windows(schedule,datetime.fromisoformat(date).date())


def open_seconds_between(schedule, start, end):
    """Elapsed trading time; unknown sessions receive no discount for stale data."""
    start,end=_utc(start),_utc(end)
    if end<start:
        raise ValueError("Trading interval ends before it starts")
    if not schedule or end-start>timedelta(days=8):
        return (end-start).total_seconds()
    zone=ZoneInfo(schedule["timezone"])
    day,last=start.astimezone(zone).date(),end.astimezone(zone).date()
    total=0.
    while day<=last:
        for opened,closed in _windows(schedule,day):
            total+=max(0.,(min(end,closed)-max(start,opened)).total_seconds())
        day+=timedelta(days=1)
    return total


def bar_close_utc(opened, seconds, zone=timezone.utc):
    """Broker wall-clock bar boundaries include 23/25-hour DST days."""
    opened=_utc(opened)
    return (opened.astimezone(zone)+timedelta(seconds=seconds)).astimezone(timezone.utc)


def quote_session(schedule, now=None):
    now=_utc(now or datetime.now(timezone.utc))
    if not schedule:
        return {"scheduled_closed":False,"schedule_known":False,"next_open_utc":None,"next_open_tehran":None}
    kind = schedule["type"]
    zone = ZoneInfo(schedule["timezone"])
    local = now.astimezone(zone)
    if kind not in {"litefinance_forex","weekend_only","weekly_sessions"}:
        raise ValueError("Unsupported quote schedule")
    if kind=="weekly_sessions" or schedule.get("exceptions"):
        windows=[window for offset in range(8) for window in _windows(schedule,local.date()+timedelta(days=offset))]
        closed=not any(opened<=now<end for opened,end in windows)
        next_open=next((opened for opened,end in windows if opened>now),None) if closed else None
        return {"scheduled_closed":closed,"schedule_known":True,"schedule_type":kind,
            "schedule_note":"Configured sessions; holidays require explicit exceptions and fresh broker quotes",
            "schedule_source":schedule.get("source"),
            "next_open_utc":next_open.isoformat() if next_open else None,
            "next_open_tehran":next_open.astimezone(ZoneInfo("Asia/Tehran")).isoformat() if next_open else None}
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
