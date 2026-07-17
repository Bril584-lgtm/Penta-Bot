"""Rocket Rivals Season 8 schedule data + helpers.

Data lives in rr_s8_schedule.json. The repo copy is the baseline parsed from
the official RRS8 division sheets on 2026-07-17; the rr_sync cog refreshes a
copy in STATE_DIR from the live sheets and hot-reloads this module. Matchups
use team abbreviations as shown on the league banners; full names are
included where the league publishes them.
"""
import json
import os
import re
from datetime import date, datetime

from statepath import state_file

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_JSON = os.path.join(_HERE, "rr_s8_schedule.json")
_STATE_JSON = state_file("rr_s8_schedule.json")


def _load() -> dict:
    for path in (_STATE_JSON, _REPO_JSON):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("divisions"):
                return data
        except (OSError, ValueError):
            continue
    raise RuntimeError("no usable rr_s8_schedule.json found")


DATA = _load()
SEASON_YEAR = DATA["year"]
DIVISIONS = list(DATA["divisions"].keys())  # Challengers, Legends, Titans


def reload() -> None:
    """Re-read the schedule JSON (called by rr_sync after a successful sync)."""
    global DATA
    DATA = _load()

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"])}


def parse_sheet_date(label: str) -> date | None:
    """'July 25th' or 'July 25th/26th' -> date(2026, 7, 25) (first day)."""
    m = re.match(r"(\w+) (\d+)", label or "")
    if not m or m.group(1) not in _MONTHS:
        return None
    return date(SEASON_YEAR, _MONTHS[m.group(1)], int(m.group(2)))


def team_display(division: str, abbrev: str) -> str:
    name = DATA["divisions"][division]["teams"].get(abbrev)
    return f"{name} ({abbrev})" if name else abbrev


def resolve_team(division: str, query: str) -> str | None:
    """Match user input against abbreviations and full names."""
    q = query.strip().lower()
    teams = DATA["divisions"][division]["teams"]
    for ab in teams:
        if ab.lower() == q:
            return ab
    for ab, name in teams.items():
        if name and (q == name.lower() or q in name.lower()):
            return ab
    return None


def stages(division: str) -> list:
    return DATA["divisions"][division]["stages"]


def _time_key(m: dict) -> int:
    t = re.match(r"(\d+)\s*(am|pm)", m.get("time", ""), re.I)
    if not t:
        return 99
    h = int(t.group(1)) % 12
    return h + (12 if t.group(2).lower() == "pm" else 0)


def matches_by_date(division: str, stage_name: str | None = None,
                    team: str | None = None) -> list:
    """Returns [(stage, date_label, day, [match, ...]), ...] in schedule order."""
    out = []
    for st in stages(division):
        if stage_name and st["stage"] != stage_name:
            continue
        groups: dict[tuple, list] = {}
        for m in st["matches"]:
            if team and team not in m["teams"]:
                continue
            groups.setdefault((m["date"], m.get("day")), []).append(m)
        for (dlabel, day), ms in groups.items():
            ms.sort(key=_time_key)
            out.append((st["stage"], dlabel, day, ms))
    out.sort(key=lambda g: (parse_sheet_date(g[1]) or date.max, g[2] or ""))
    return out


def upcoming(division: str, team: str | None = None, today: date | None = None) -> list:
    today = today or datetime.now().date()
    return [g for g in matches_by_date(division, team=team)
            if (parse_sheet_date(g[1]) or date.max) >= today]


def unscheduled_stages(division: str) -> list:
    """Stages with published dates but no matchups yet, dates in chronological order."""
    out = []
    for st in stages(division):
        if not st["scheduled"] and st["dates"]:
            ds = sorted(st["dates"], key=lambda d: parse_sheet_date(d) or date.max)
            out.append((st["stage"], ds))
    return out


def standings(division: str, stage: str | None = None) -> list:
    """Sorted standings rows for one stage, or aggregated across all played
    stages when stage is None. Rows: {team, pts, w, l, gw, gl, gf, ga}."""
    blocks = DATA["divisions"][division].get("standings", {})
    if stage:
        rows = [dict(r) for r in blocks.get(stage, [])]
    else:
        agg: dict[str, dict] = {}
        for block in blocks.values():
            for r in block:
                a = agg.setdefault(r["team"], {"team": r["team"], "pts": 0, "w": 0, "l": 0,
                                               "gw": 0, "gl": 0, "gf": 0, "ga": 0})
                for k in ("pts", "w", "l", "gw", "gl", "gf", "ga"):
                    a[k] += r[k]
        rows = list(agg.values())
    rows.sort(key=lambda r: (-r["pts"], -(r["w"] - r["l"]), -(r["gw"] - r["gl"]),
                             -(r["gf"] - r["ga"])))
    return rows


def standings_stages(division: str) -> list:
    return list(DATA["divisions"][division].get("standings", {}).keys())


def _opponent_records(division: str) -> dict:
    """team -> list of opponents faced in completed matches."""
    faced = {}
    for st in stages(division):
        for m in st["matches"]:
            if "result" not in m:
                continue
            a, b = m["teams"]
            faced.setdefault(a, []).append(b)
            faced.setdefault(b, []).append(a)
    return faced


def power_rankings(division: str) -> list:
    """Computed rating per team, best first.

    rating = 100*win% + 12*(game diff per match) + 4*(goal +/- per game)
             + 25*(avg opponent win% — strength of schedule)
    """
    rows = standings(division)
    winpct = {r["team"]: (r["w"] / (r["w"] + r["l"]) if r["w"] + r["l"] else 0.0) for r in rows}
    faced = _opponent_records(division)
    ranked = []
    for r in rows:
        gp = r["w"] + r["l"]
        if gp == 0:
            ranked.append({**r, "rating": 0.0, "sos": 0.0})
            continue
        game_diff = (r["gw"] - r["gl"]) / gp
        games = r["gw"] + r["gl"]
        goal_pm = (r["gf"] - r["ga"]) / games if games else 0.0
        opps = faced.get(r["team"], [])
        sos = sum(winpct.get(o, 0.0) for o in opps) / len(opps) if opps else 0.0
        rating = 100 * winpct[r["team"]] + 12 * game_diff + 4 * goal_pm + 25 * sos
        ranked.append({**r, "rating": round(rating, 1), "sos": round(sos, 2)})
    ranked.sort(key=lambda r: -r["rating"])
    return ranked


def format_result(m: dict) -> str | None:
    """'AKG 3-1 BD' / 'AKG W-FF BD' for a completed match, else None."""
    res = m.get("result")
    if not res:
        return None
    a, b = m["teams"]
    if res["ff"]:
        wa = res["winner"] == a
        return f"{a} {'W-FF' if wa else 'FF-W'} {b}"
    s = res["score"]
    return f"{a} {s[0]}-{s[1]} {b}"


def context_text() -> str:
    """Compact schedule dump appended to the /ask system prompt."""
    lines = ["", "=== ROCKET RIVALS SEASON 8 SCHEDULES (matchups use team abbreviations) ==="]
    for div, d in DATA["divisions"].items():
        teams = ", ".join(f"{ab}={name}" if name else ab for ab, name in d["teams"].items())
        lines.append(f"\n--- {div} Division — Teams: {teams} ---")
        for st in d["stages"]:
            if st["matches"]:
                lines.append(f"[{st['stage']}]")
                for group in matches_by_date(div, st["stage"]):
                    _, dlabel, day, ms = group
                    day_txt = f" ({day})" if day else ""
                    pairs = "; ".join(
                        (f"{format_result(m)} FINAL" if m.get("result")
                         else f"{m['teams'][0]} vs {m['teams'][1]}") + f" @{m['time']}"
                        for m in ms)
                    lines.append(f"  {dlabel}{day_txt}: {pairs}")
            elif st["dates"]:
                lines.append(f"[{st['stage']}] dates: {', '.join(st['dates'])} — matchups TBD")
            else:
                lines.append(f"[{st['stage']}] dates + matchups TBD")
        played = standings_stages(div)
        if played:
            lines.append(f"[Standings — points/W-L/game diff, stages played: {', '.join(played)}]")
            for i, r in enumerate(standings(div), 1):
                lines.append(f"  {i}. {r['team']}: {int(r['pts'])}pts {int(r['w'])}-{int(r['l'])}"
                             f" games {int(r['gw'])}-{int(r['gl'])} goals {int(r['gf'])}-{int(r['ga'])}")
    lines.append("\nAll times are EST. Source: official RRS8 division sheets, auto-synced (last: "
                 + DATA["source_updated"] + "). Matches marked FINAL include the series score "
                 "(W-FF = forfeit). Standings are the league's official numbers summed across "
                 "played stages; 2 points per match win.")
    return "\n".join(lines)
