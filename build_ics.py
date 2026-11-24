#!/usr/bin/env python3
"""把瑣事助理的資料（items、routines 兩個 JSON Lines 檔）轉成 calendar.ics。

用法：python3 build_ics.py items.jsonl routines.jsonl calendar.ics

每一行是 db_list 輸出的一筆：{"id": ..., "data": {...}}
- items：有日期的事項都會放進去（已完成的「任務」除外）
- routines：只放 agenda=true 的（會出現在「今天」的那些，例如上課、教學）；
  練琴、通勤這種自己規劃的時間不放
"""
import json
import sys
from datetime import date, datetime, timedelta, timezone

TZ = "Asia/Taipei"
VTIMEZONE = [
    "BEGIN:VTIMEZONE", f"TZID:{TZ}",
    "BEGIN:STANDARD", "DTSTART:19700101T000000",
    "TZOFFSETFROM:+0800", "TZOFFSETTO:+0800", "TZNAME:CST",
    "END:STANDARD", "END:VTIMEZONE",
]


def load(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line.startswith("{"):
                continue
            row = json.loads(line)
            d = row.get("data") or {}
            d["id"] = row["id"]
            out.append(d)
    return out


def esc(s):
    return (str(s).replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n"))


def fold(line):
    """iCalendar 規定每行最多 75 bytes，中文要小心不要切在字的中間。"""
    out, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        limit = 75 if not out else 74
        if len(cur) + len(b) > limit:
            out.append(cur.decode("utf-8"))
            cur = b""
        cur += b
    out.append(cur.decode("utf-8"))
    return "\r\n ".join(out)


def pd(s):
    return date.fromisoformat(s)


def dt(d, hm):
    return d.strftime("%Y%m%d") + "T" + hm.replace(":", "") + "00"


def time_range(d, start, end):
    """回傳 (DTSTART, DTEND) 字串；沒有結束時間就當一小時，00:00 當成隔天午夜。"""
    sh, sm = map(int, start.split(":"))
    s = datetime(d.year, d.month, d.day, sh, sm)
    if end:
        eh, em = map(int, end.split(":"))
        e = datetime(d.year, d.month, d.day, eh, em)
        if e <= s:
            e += timedelta(days=1)
    else:
        e = s + timedelta(hours=1)
    return s.strftime("%Y%m%dT%H%M%S"), e.strftime("%Y%m%dT%H%M%S")


def item_event(it, stamp):
    if not it.get("date") or not it.get("title"):
        return None
    if it.get("done") and it.get("kind") != "sched":
        return None
    d = pd(it["date"])
    title = it["title"]
    if it.get("kind") == "task":
        title = "截止：" + title
    lines = ["BEGIN:VEVENT", f"UID:item-{it['id']}@joyce-errands", f"DTSTAMP:{stamp}",
             f"SUMMARY:{esc(title)}"]
    if it.get("time"):
        s, e = time_range(d, it["time"], it.get("end"))
        lines += [f"DTSTART;TZID={TZ}:{s}", f"DTEND;TZID={TZ}:{e}"]
    else:
        lines += [f"DTSTART;VALUE=DATE:{d.strftime('%Y%m%d')}",
                  f"DTEND;VALUE=DATE:{(d + timedelta(days=1)).strftime('%Y%m%d')}"]
    if it.get("note"):
        lines.append(f"DESCRIPTION:{esc(it['note'])}")
    lines.append("END:VEVENT")
    return lines


def routine_event(r, stamp):
    if not r.get("agenda") or not r.get("title") or not r.get("time"):
        return None
    if not isinstance(r.get("weekday"), int):
        return None
    start = pd(r["start"]) if r.get("start") else date.today()
    # 頁面用 0=週日；Python 的 weekday() 用 0=週一
    target = (r["weekday"] - 1) % 7
    first = start + timedelta(days=(target - start.weekday()) % 7)
    until = pd(r["until"]) if r.get("until") else None
    if until and first > until:
        return None
    s, e = time_range(first, r["time"], r.get("end"))
    lines = ["BEGIN:VEVENT", f"UID:routine-{r['id']}@joyce-errands", f"DTSTAMP:{stamp}",
             f"SUMMARY:{esc(r['title'])}",
             f"DTSTART;TZID={TZ}:{s}", f"DTEND;TZID={TZ}:{e}"]
    once = r.get("start") and r.get("start") == r.get("until")
    if not once:
        rule = "RRULE:FREQ=WEEKLY"
        if until:
            # UNTIL 要用 UTC：取那天 23:59 台北時間
            u = datetime(until.year, until.month, until.day, 23, 59, tzinfo=timezone(timedelta(hours=8)))
            rule += ";UNTIL=" + u.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        lines.append(rule)
        for sk in sorted(set(r.get("skip") or [])):
            lines.append(f"EXDATE;TZID={TZ}:{dt(pd(sk), r['time'])}")
    if r.get("location"):
        lines.append(f"LOCATION:{esc(r['location'])}")
    lines.append("END:VEVENT")
    return lines


def main(items_path, routines_path, out_path):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    cal = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Joyce//瑣事助理//ZH-TW",
           "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
           "X-WR-CALNAME:瑣事助理", f"X-WR-TIMEZONE:{TZ}",
           "REFRESH-INTERVAL;VALUE=DURATION:PT1H", "X-PUBLISHED-TTL:PT1H"] + VTIMEZONE
    n = 0
    for it in load(items_path):
        ev = item_event(it, stamp)
        if ev:
            cal += ev; n += 1
    for r in load(routines_path):
        ev = routine_event(r, stamp)
        if ev:
            cal += ev; n += 1
    cal.append("END:VCALENDAR")
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        f.write("\r\n".join(fold(l) for l in cal) + "\r\n")
    print(f"寫入 {n} 個事件到 {out_path}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
