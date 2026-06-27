#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from urllib import request, parse, error
from typing import Optional, Tuple


KST = timezone(timedelta(hours=9))


def read_wc_data(path: str) -> dict:
    text = open(path, "r", encoding="utf-8").read()
    m = re.search(r"window\.WC_DATA\s*=\s*(\{[\s\S]*\})\s*;\s*$", text)
    if not m:
        raise ValueError("window.WC_DATA 객체를 찾을 수 없습니다.")
    obj = m.group(1)
    json_like = re.sub(r'([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:', r'\1"\2":', obj)
    return json.loads(json_like)


def write_wc_data(path: str, data: dict) -> None:
    payload = "window.WC_DATA = " + json.dumps(data, ensure_ascii=False, indent=2) + ";\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(payload)


def fetch_json(url: str, api_key: str) -> dict:
    req = request.Request(url, headers={"X-Auth-Token": api_key})
    with request.urlopen(req, timeout=20) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body)


def tla(team: dict) -> str:
    return (team.get("tla") or "").upper()


def map_matches(api_matches: list) -> dict:
    by_pair = {}
    for m in api_matches:
        h = tla(m.get("homeTeam", {}))
        a = tla(m.get("awayTeam", {}))
        if not h or not a:
            continue
        pair = frozenset([h, a])
        by_pair.setdefault(pair, []).append(m)
    return by_pair


def pick_match(candidates: list, home_code: str, away_code: str) -> Optional[dict]:
    if not candidates:
        return None
    exact = [m for m in candidates if tla(m.get("homeTeam", {})) == home_code and tla(m.get("awayTeam", {})) == away_code]
    if exact:
        return sorted(exact, key=lambda x: x.get("utcDate", ""))[-1]
    # orientation swapped or unknown: pick latest
    return sorted(candidates, key=lambda x: x.get("utcDate", ""))[-1]


def extract_score_pair(match_obj: dict) -> Optional[Tuple[int, int]]:
    score = match_obj.get("score", {})
    for key in ("fullTime", "regularTime", "halfTime"):
        part = score.get(key, {}) or {}
        h = part.get("home")
        a = part.get("away")
        if isinstance(h, int) and isinstance(a, int):
            return h, a
    return None


def update_match_status_and_score(data: dict, api_matches: list) -> None:
    match_map = map_matches(api_matches)
    for item in data.get("matches", []):
        home_code = item.get("home", {}).get("code", "").upper()
        away_code = item.get("away", {}).get("code", "").upper()
        if not home_code or not away_code:
            continue
        candidates = match_map.get(frozenset([home_code, away_code]), [])
        m = pick_match(candidates, home_code, away_code)
        if not m:
            continue
        status = (m.get("status") or "").upper()
        score_pair = extract_score_pair(m)
        if status in ("FINISHED", "AWARDED"):
            if score_pair is not None:
                h_goal, a_goal = score_pair
                api_home = tla(m.get("homeTeam", {}))
                if api_home == home_code:
                    item["score"] = {"home": h_goal, "away": a_goal}
                else:
                    item["score"] = {"home": a_goal, "away": h_goal}
                item["status"] = "ended"
        elif status in ("IN_PLAY", "PAUSED"):
            if score_pair is not None:
                h_goal, a_goal = score_pair
                api_home = tla(m.get("homeTeam", {}))
                if api_home == home_code:
                    item["score"] = {"home": h_goal, "away": a_goal}
                else:
                    item["score"] = {"home": a_goal, "away": h_goal}
            item["status"] = "live"
        else:
            item["status"] = "upcoming"
            item.pop("score", None)


def evaluate_match_impact(home_code: str, away_code: str, home_score: int, away_score: int) -> Optional[str]:
    diff = home_score - away_score

    # E: 독일-에콰도르
    if (home_code, away_code) == ("GER", "ECU"):
        return "good" if diff >= 0 else "bad"
    # E: 코트디부아르-퀴라소 (코트디부아르 승리가 조건)
    if (home_code, away_code) == ("CIV", "CUW"):
        return "good" if diff >= 0 else "bad"
    # F: 일본-스웨덴
    if (home_code, away_code) == ("JPN", "SWE"):
        return "good" if diff >= 2 else "bad"
    # D: 호주-파라과이
    if (home_code, away_code) == ("AUS", "PAR"):
        return "good" if diff > 0 or diff <= -2 else "bad"
    # I: 세네갈-이라크
    if (home_code, away_code) == ("SEN", "IRQ"):
        if diff == 0:
            return "good"
        if diff == 1:
            return "good"
        if diff < 0 and 1 <= (-diff) <= 4:
            return "good"
        return "bad"
    # H: 스페인-우루과이
    if (home_code, away_code) == ("ESP", "URU"):
        return "good" if diff > 0 else "bad"
    # G: 이집트-이란
    if (home_code, away_code) == ("EGY", "IRN"):
        return "good" if diff > 0 else "bad"
    # J: 알제리-오스트리아
    if (home_code, away_code) == ("ALG", "AUT"):
        return "good" if diff >= 2 or diff < 0 else "bad"
    # K: 콩고민주공화국-우즈베키스탄
    if (home_code, away_code) == ("COD", "UZB"):
        return "good" if diff <= 0 else "bad"
    # L: 크로아티아-가나
    if (home_code, away_code) == ("CRO", "GHA"):
        return "good" if diff < 0 else "bad"
    # G 보조조건: 벨기에-뉴질랜드 승패가 갈려야 함
    if (home_code, away_code) == ("BEL", "NZL"):
        return "good" if diff != 0 else "bad"
    # H: 카보베르데-사우디아라비아는 한국 진출 조건과 무관 (중립)
    if (home_code, away_code) == ("CPV", "KSA"):
        return "watch"
    return None


def update_match_impact(data: dict) -> None:
    for item in data.get("matches", []):
        if item.get("status") != "ended":
            continue
        score = item.get("score") or {}
        h = score.get("home")
        a = score.get("away")
        if not isinstance(h, int) or not isinstance(a, int):
            continue
        home_code = (item.get("home", {}) or {}).get("code", "").upper()
        away_code = (item.get("away", {}) or {}).get("code", "").upper()
        if not home_code or not away_code:
            continue
        impact = evaluate_match_impact(home_code, away_code, h, a)
        if impact in ("good", "bad", "watch"):
            item["impact"] = impact


def extract_group_letter(group_name: str) -> str:
    # e.g. "GROUP_A" -> "A"
    if not group_name:
        return ""
    m = re.search(r"GROUP_([A-Z])", group_name.upper())
    return m.group(1) if m else ""


def build_group_tables(api_matches: list) -> dict:
    groups = {}

    def ensure_team(g: str, code: str, name: str) -> None:
        if g not in groups:
            groups[g] = {}
        if code not in groups[g]:
            groups[g][code] = {
                "group": g,
                "id": code,
                "team": name or code,
                "pts": 0,
                "gd": 0,
                "gf": 0,
                "played": 0,
            }

    for m in api_matches:
        if (m.get("stage") or "").upper() != "GROUP_STAGE":
            continue
        g = extract_group_letter(m.get("group") or "")
        if not g:
            continue
        home = m.get("homeTeam", {}) or {}
        away = m.get("awayTeam", {}) or {}
        hc = tla(home)
        ac = tla(away)
        if not hc or not ac:
            continue
        ensure_team(g, hc, home.get("shortName") or home.get("name") or hc)
        ensure_team(g, ac, away.get("shortName") or away.get("name") or ac)

        status = (m.get("status") or "").upper()
        if status not in ("FINISHED", "AWARDED", "IN_PLAY", "PAUSED"):
            continue
        score_pair = extract_score_pair(m)
        if score_pair is None:
            continue
        hs, as_ = score_pair
        hrow = groups[g][hc]
        arow = groups[g][ac]
        hrow["played"] += 1
        arow["played"] += 1
        hrow["gf"] += hs
        arow["gf"] += as_
        hrow["gd"] += hs - as_
        arow["gd"] += as_ - hs
        if hs > as_:
            hrow["pts"] += 3
        elif hs < as_:
            arow["pts"] += 3
        else:
            hrow["pts"] += 1
            arow["pts"] += 1

    ranked = {}
    for g, rows in groups.items():
        ranked[g] = sorted(
            rows.values(),
            key=lambda r: (-int(r["pts"]), -int(r["gd"]), -int(r["gf"]), r["id"]),
        )
    return ranked


def update_third_table(data: dict, group_tables: dict) -> None:
    existing_name_by_id = {row.get("id"): row.get("team") for row in data.get("thirdTable", [])}
    code_name_map = {}
    for m in data.get("matches", []):
        h = m.get("home", {}) or {}
        a = m.get("away", {}) or {}
        if h.get("code") and h.get("name"):
            code_name_map[h["code"].upper()] = h["name"]
        if a.get("code") and a.get("name"):
            code_name_map[a["code"].upper()] = a["name"]

    tracked_groups = sorted(group_tables.keys())
    third_rows = []
    for g in tracked_groups:
        table = group_tables.get(g) or []
        if len(table) < 3:
            continue
        third = table[2]
        tid = (third.get("id") or "").upper()
        if not tid:
            continue
        third_rows.append({
            "group": g,
            "id": tid,
            "team": existing_name_by_id.get(tid) or code_name_map.get(tid) or third.get("team") or tid,
            "pts": int(third.get("pts", 0)),
            "gd": int(third.get("gd", 0)),
            "gf": int(third.get("gf", 0)),
            "played": int(third.get("played", 0)),
        })
    if third_rows:
        data["thirdTable"] = third_rows


def prune_unused_fields(data: dict) -> None:
    data.pop("korea", None)
    summary = data.get("summary")
    if isinstance(summary, dict):
        summary.pop("securedTeams", None)

    for m in data.get("matches", []):
        if not isinstance(m, dict):
            continue
        m.pop("tier", None)
        m.pop("currentThird", None)
        m.pop("wanted", None)


def main() -> int:
    parser = argparse.ArgumentParser(description="Update WC_DATA from football-data.org")
    parser.add_argument("--file", default="data/matches.js", help="WC_DATA JS file path")
    parser.add_argument("--competition", default="WC", help="football-data competition code")
    parser.add_argument("--season", type=int, default=2026, help="season year")
    parser.add_argument("--api-key-env", default="FOOTBALL_DATA_API_KEY", help="env var containing API key")
    args = parser.parse_args()

    api_key = os.getenv(args.api_key_env, "").strip()
    if not api_key:
        print(f"ERROR: {args.api_key_env} 환경변수가 필요합니다.", file=sys.stderr)
        return 1

    try:
        data = read_wc_data(args.file)
        q = parse.urlencode({"season": args.season})
        base = f"https://api.football-data.org/v4/competitions/{args.competition}"
        matches_payload = fetch_json(f"{base}/matches?{q}", api_key)
        update_match_status_and_score(data, matches_payload.get("matches", []))
        update_match_impact(data)
        group_tables = build_group_tables(matches_payload.get("matches", []))
        update_third_table(data, group_tables)
        prune_unused_fields(data)
        data["updatedAt"] = datetime.now(KST).isoformat(timespec="seconds")
        write_wc_data(args.file, data)
        print(f"Updated {args.file} at {data['updatedAt']}")
        return 0
    except error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        print(f"HTTP ERROR {e.code}: {body}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
