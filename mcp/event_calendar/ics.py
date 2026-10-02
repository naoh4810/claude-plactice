"""iCalendar（.ics）の読み書き。connpass・Peatix・Google カレンダーが書き出す形式の、予定に必要な部分だけを扱う。"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))


def _unfold(text: str) -> list[str]:
    """折り返された行（次の行が空白で始まる）をつなげる。"""
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _unescape(value: str) -> str:
    return value.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def _parse_time(params: dict, value: str) -> tuple[date, str | None]:
    """日付と、日本時間の開始時刻（終日なら None）。"""
    if params.get("VALUE") == "DATE" or len(value) == 8:
        return datetime.strptime(value[:8], "%Y%m%d").date(), None
    dt = datetime.strptime(value.rstrip("Z")[:15], "%Y%m%dT%H%M%S")
    if value.endswith("Z"):
        dt = dt.replace(tzinfo=timezone.utc).astimezone(JST)
    # TZID 付きは、日本の交流会なら Asia/Tokyo と見なす（他のタイムゾーンは未対応）
    return dt.date(), dt.strftime("%H:%M")


def parse(text: str) -> list[dict]:
    """VEVENT ごとに {uid, title, date, start, end, place, url, description} を返す。"""
    events, current = [], None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            current = {}
            continue
        if line == "END:VEVENT":
            if current is not None and current.get("title") and current.get("date"):
                events.append(current)
            current = None
            continue
        if current is None or ":" not in line:
            continue
        head, value = line.split(":", 1)
        name, *param_list = head.split(";")
        params = dict(p.split("=", 1) for p in param_list if "=" in p)
        if name == "SUMMARY":
            current["title"] = _unescape(value).strip()
        elif name == "DTSTART":
            current["date"], current["start"] = _parse_time(params, value)
        elif name == "DTEND":
            end_date, end = _parse_time(params, value)
            current["end"] = end if end_date == current.get("date") else None
        elif name == "LOCATION":
            current["place"] = _unescape(value).strip()
        elif name == "URL":
            current["url"] = value.strip()
        elif name == "DESCRIPTION":
            current["description"] = _unescape(value).strip()
        elif name == "UID":
            current["uid"] = value.strip()
    return events


def _escape(value: str) -> str:
    return (value or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> list[str]:
    """75 バイトを超える行を折り返す（UTF-8 の文字の途中では切らない）。"""
    out, current, limit = [], "", 75
    for ch in line:
        if len((current + ch).encode("utf-8")) > limit:
            out.append(current)
            current, limit = " " + ch, 75
        else:
            current += ch
    return out + [current]


def _utc(day: str, hhmm: str) -> str:
    dt = datetime.fromisoformat(f"{day}T{hhmm}").replace(tzinfo=JST).astimezone(timezone.utc)
    return dt.strftime("%Y%m%dT%H%M%SZ")


def build(events: list[dict], stamp: datetime) -> str:
    """Google カレンダーなどに取り込める .ics。時刻は UTC で書く（タイムゾーン定義が要らない）。"""
    out = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//claude-plactice//event-calendar//JA", "CALSCALE:GREGORIAN"]
    for e in events:
        out += ["BEGIN:VEVENT", f"UID:{e['id']}@event-calendar", f"DTSTAMP:{stamp.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}"]
        if e.get("start"):
            end = e.get("end") or (datetime.fromisoformat(f"{e['date']}T{e['start']}") + timedelta(hours=2)).strftime("%H:%M")
            out += [f"DTSTART:{_utc(e['date'], e['start'])}", f"DTEND:{_utc(e['date'], end)}"]
        else:
            d = date.fromisoformat(e["date"])
            out += [f"DTSTART;VALUE=DATE:{d:%Y%m%d}", f"DTEND;VALUE=DATE:{d + timedelta(days=1):%Y%m%d}"]
        out.append(f"SUMMARY:{_escape(e['title'])}")
        if e.get("place"):
            out.append(f"LOCATION:{_escape(e['place'])}")
        if e.get("url"):
            out.append(f"URL:{e['url']}")
        desc = "\n".join(x for x in (f"主催: {e['organizer']}" if e.get("organizer") else "",
                                      f"参加費: {e['fee']}円" if e.get("fee") else "", e.get("audience", "")) if x)
        if desc:
            out.append(f"DESCRIPTION:{_escape(desc)}")
        out.append("END:VEVENT")
    out.append("END:VCALENDAR")
    return "\r\n".join(part for line in out for part in _fold(line)) + "\r\n"
