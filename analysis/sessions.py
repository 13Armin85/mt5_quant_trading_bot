from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

SESSIONS={
    "Sydney":("Australia/Sydney",time(8,0),time(17,0)),
    "Tokyo":("Asia/Tokyo",time(9,0),time(18,0)),
    "London":("Europe/London",time(8,0),time(17,0)),
    "New York":("America/New_York",time(8,0),time(17,0)),
}


def active_sessions(now_utc: datetime) -> list[str]:
    active=[]
    for name,(tz,start,end) in SESSIONS.items():
        local=now_utc.astimezone(ZoneInfo(tz)).time()
        if start<=local<end: active.append(name)
    return active
