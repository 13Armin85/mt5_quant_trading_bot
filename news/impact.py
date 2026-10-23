"""Numeric economic surprises, kept separate from untrusted headline prose."""
import math
import re


def numeric_release(value, unit=""):
    if value is None:
        return None
    match = re.fullmatch(r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*([KMB%]?)\s*", str(value).replace(",", ""), re.I)
    if not match:
        return None
    number = float(match.group(1))
    suffix = match.group(2).upper() or unit.upper()
    if not math.isfinite(number):
        return None
    if suffix in {"K", "M", "B"}:
        number *= {"K":1e3,"M":1e6,"B":1e9}[suffix]
        suffix = "NUMBER"
    elif not suffix:
        suffix = "NUMBER"
    return number, suffix


def analyze_release(event, rules, now):
    result = {"source":event.source,"event":event.title,"currency":event.currency,
              "published_at":str(event.published_at or event.timestamp),"received_at":str(event.received_at),
              "updated_at":str(event.updated_at),"actual":event.actual,"forecast":event.forecast,
              "previous":event.previous,"revision":event.revision,"unit":event.unit,
              "surprise":None,"currency_bias":None,"reason":"No validated directional interpretation"}
    if event.timestamp > now or (event.published_at and event.published_at > now):
        result["reason"] = "Release not yet published"
        return result
    actual, forecast = numeric_release(event.actual,event.unit), numeric_release(event.forecast,event.unit)
    if actual is None or forecast is None or actual[1] != forecast[1]:
        result["reason"] = "Missing or incompatible actual/forecast units"
        return result
    result["surprise"] = actual[0] - forecast[0]
    matches = [r for r in rules if r["contains"].lower() in event.title.lower()]
    if len(matches) == 1 and matches[0].get("higher_is_positive") in (True,False):
        polarity = 1 if matches[0]["higher_is_positive"] else -1
        result["currency_bias"] = polarity * (1 if result["surprise"] > 0 else -1 if result["surprise"] < 0 else 0)
        result["reason"] = "Configured surprise rule; requires technical price confirmation"
    return result
