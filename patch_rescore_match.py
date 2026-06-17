#!/usr/bin/env python3
"""
Force-rescore any match by scanning ESPN directly for a date range.
Bypasses the scorer's finalization check and status filter.

Usage:
  python patch_rescore_match.py --match-id 21 --start 20260611 --end 20260617
"""

import os
import sys
import argparse
import datetime
import logging

import requests
import firebase_admin
from firebase_admin import credentials, db

ESPN_BASE    = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world"
FIREBASE_URL = os.environ.get(
    "FIREBASE_DB_URL",
    "https://wc2026-fantasy-m47-default-rtdb.firebaseio.com"
)

SCHEDULE = [
    {"id": 1,  "home": "Mexico",         "away": "South Africa"},
    {"id": 2,  "home": "South Korea",    "away": "Czech Republic"},
    {"id": 3,  "home": "Canada",         "away": "Bosnia & Herz."},
    {"id": 4,  "home": "USA",            "away": "Paraguay"},
    {"id": 5,  "home": "Brazil",         "away": "Morocco"},
    {"id": 6,  "home": "Australia",      "away": "Turkey"},
    {"id": 7,  "home": "Qatar",          "away": "Switzerland"},
    {"id": 8,  "home": "Haiti",          "away": "Scotland"},
    {"id": 9,  "home": "Germany",        "away": "Curazao"},
    {"id": 10, "home": "Ivory Coast",    "away": "Ecuador"},
    {"id": 11, "home": "Netherlands",    "away": "Japan"},
    {"id": 12, "home": "Sweden",         "away": "Tunisia"},
    {"id": 13, "home": "Spain",          "away": "Cabo Verde"},
    {"id": 14, "home": "Belgium",        "away": "Egypt"},
    {"id": 15, "home": "Saudi Arabia",   "away": "Uruguay"},
    {"id": 16, "home": "Iran",           "away": "New Zealand"},
    {"id": 17, "home": "France",         "away": "Senegal"},
    {"id": 18, "home": "Iraq",           "away": "Norway"},
    {"id": 19, "home": "Austria",        "away": "Jordan"},
    {"id": 20, "home": "Argentina",      "away": "Algeria"},
    {"id": 21, "home": "England",        "away": "Croatia"},
    {"id": 22, "home": "Ghana",          "away": "Panama"},
    {"id": 23, "home": "Portugal",       "away": "Congo DR"},
    {"id": 24, "home": "Uzbekistan",     "away": "Colombia"},
]


def espn_get(path, params=None):
    r = requests.get(ESPN_BASE + path, params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def find_event(home_target, away_target, start_str, end_str):
    d   = datetime.date(int(start_str[:4]), int(start_str[4:6]), int(start_str[6:]))
    end = datetime.date(int(end_str[:4]),   int(end_str[4:6]),   int(end_str[6:]))
    while d <= end:
        ds = d.strftime("%Y%m%d")
        print(f"  Scanning {ds}...", end=" ", flush=True)
        try:
            data = espn_get("/scoreboard", {"dates": ds})
        except Exception as e:
            print(f"FAILED ({e})")
            d += datetime.timedelta(days=1)
            continue
        events = data.get("events", [])
        print(f"{len(events)} events")
        for ev in events:
            comp = ev.get("competitions", [{}])[0]
            competitors = comp.get("competitors", [])
            home_c = next((c for c in competitors if c.get("homeAway") == "home"), {})
            away_c = next((c for c in competitors if c.get("homeAway") == "away"), {})
            home   = home_c.get("team", {}).get("displayName", "").strip()
            away   = away_c.get("team", {}).get("displayName", "").strip()
            status = comp.get("status", {}).get("type", {}).get("name", "")
            if home == home_target and away == away_target:
                print(f"  FOUND: {home} vs {away} -- status={status}, event_id={ev['id']}")
                return ev["id"], comp
        d += datetime.timedelta(days=1)
    return None, None


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")
    p = argparse.ArgumentParser(description="Force-rescore a specific match from ESPN.")
    p.add_argument("--match-id", type=int, required=True, help="Fantasy schedule match ID")
    p.add_argument("--start",    required=True, help="Search start date YYYYMMDD")
    p.add_argument("--end",      required=True, help="Search end date YYYYMMDD")
    args = p.parse_args()

    match = next((m for m in SCHEDULE if m["id"] == args.match_id), None)
    if not match:
        print(f"ERROR: match ID {args.match_id} not found in schedule.")
        sys.exit(1)

    print(f"Looking for match {args.match_id}: {match['home']} vs {match['away']}")
    event_id, comp = find_event(match["home"], match["away"], args.start, args.end)
    if event_id is None:
        print("ERROR: match not found in ESPN for the given date range.")
        sys.exit(1)

    # Reuse scorer helpers
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from scorer import fetch_game_summary, score_match, init_firebase, write_results

    print("Initializing Firebase...")
    init_firebase()

    print("Fetching full ESPN game summary...")
    summary   = fetch_game_summary(event_id)
    comp_full = (summary.get("header", {}).get("competitions") or [comp])[0]

    print("Scoring...")
    result = score_match(comp_full, summary, is_live=False)
    teams  = list(result["teams"].items())
    h, a   = teams[0], teams[1]
    print(f"  {h[0]} {result['finalScore']['home']}-{result['finalScore']['away']} {a[0]}")
    print(f"  {h[0]}: {h[1]['fantasyPoints']} pts  |  {a[0]}: {a[1]['fantasyPoints']} pts")

    print(f"Writing match {args.match_id} to Firebase /results...")
    write_results({args.match_id: result})
    print("Done.")


if __name__ == "__main__":
    main()
