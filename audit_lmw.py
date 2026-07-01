#!/usr/bin/env python3
"""
Read-only audit: find all past matches where Last-Min Winner +4 was awarded,
re-evaluate with the updated rule (goal must have changed the lead or equalized),
and print a comparison table.
"""
import os, json, tempfile, sys
from datetime import date, timedelta

import requests
import firebase_admin
from firebase_admin import credentials, db

ESPN_BASE       = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world"
FIREBASE_DB_URL = os.environ.get("FIREBASE_DB_URL",
                  "https://wc2026-fantasy-m47-default-rtdb.firebaseio.com")
LMW_LABEL       = "Last-Min Winner +4"

TEAM_MAP = {
    "Korea Republic": "South Korea", "Czechia": "Czech Republic",
    "Bosnia-Herzegovina": "Bosnia & Herz.", "Bosnia and Herzegovina": "Bosnia & Herz.",
    "Trinidad And Tobago": "Trinidad & Tobago", "Trinidad and Tobago": "Trinidad & Tobago",
    "Ivory Coast": "Ivory Coast", "Cote d'Ivoire": "Ivory Coast",
    "DR Congo": "DR Congo", "Congo DR": "DR Congo",
    "North Macedonia": "N. Macedonia",
    "United States": "USA", "United States of America": "USA",
}

def _norm(name):
    return TEAM_MAP.get(name, name)


def init_firebase():
    cred_json = os.environ.get("FIREBASE_ADMIN_SDK_JSON")
    if not cred_json:
        raise RuntimeError("Set FIREBASE_ADMIN_SDK_JSON env var.")
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    tmp.write(cred_json)
    tmp.close()
    cred = credentials.Certificate(tmp.name)
    firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DB_URL})


def fetch_espn_event_map(days_back=40):
    """Scan past N days of ESPN scoreboard; return {(home,away): event_id}."""
    event_map = {}
    today = date.today()
    start = today - timedelta(days=days_back)
    current = start
    while current <= today + timedelta(days=7):
        ds = current.strftime("%Y%m%d")
        try:
            r = requests.get(f"{ESPN_BASE}/scoreboard", params={"dates": ds}, timeout=20)
            r.raise_for_status()
            for ev in r.json().get("events", []):
                comps = ev.get("competitions", [])
                if not comps:
                    continue
                comp = comps[0]
                competitors = comp.get("competitors", [])
                home_c = next((c for c in competitors if c.get("homeAway") == "home"), None)
                away_c = next((c for c in competitors if c.get("homeAway") == "away"), None)
                if not home_c or not away_c:
                    continue
                h = _norm(home_c.get("team", {}).get("displayName", ""))
                a = _norm(away_c.get("team", {}).get("displayName", ""))
                if h and a:
                    event_map[(h, a)] = ev["id"]
        except Exception as e:
            pass  # skip failed days silently
        current += timedelta(days=1)
    return event_map


def fetch_game_summary(event_id):
    r = requests.get(f"{ESPN_BASE}/summary", params={"event": event_id}, timeout=30)
    r.raise_for_status()
    return r.json()


def parse_goals(details, home, away):
    goals = []
    for d in details:
        if not d.get("scoringPlay", False):
            continue
        if d.get("penaltyKick", False) and (d.get("clock", {}).get("value", 0) or 0) >= 7200:
            continue  # skip PSO goals
        team      = _norm((d.get("team") or {}).get("displayName", ""))
        own_goal  = d.get("ownGoal", False)
        clock_val = (d.get("clock") or {}).get("value", 0) or 0
        extra_val = (d.get("addedClock") or {}).get("value", 0) or 0
        goals.append({
            "team":        team,
            "is_own_goal": own_goal,
            "clock_secs":  clock_val + extra_val,
        })
    return goals


def lmw_new_rule(goals, home, away, ft_winner):
    """Return True only if ft_winner scored a goal at 88'+ that changed lead or equalized."""
    if not ft_winner:
        return False
    sorted_goals = sorted(goals, key=lambda x: x["clock_secs"])
    for idx, g in enumerate(sorted_goals):
        if g["is_own_goal"] or g["team"] != ft_winner:
            continue
        if g["clock_secs"] < 88 * 60:
            continue
        h_score = a_score = 0
        for prev in sorted_goals[:idx]:
            if prev["team"] == home:
                if prev["is_own_goal"]:
                    a_score += 1
                else:
                    h_score += 1
            else:
                if prev["is_own_goal"]:
                    h_score += 1
                else:
                    a_score += 1
        winner_before = h_score if ft_winner == home else a_score
        other_before  = a_score if ft_winner == home else h_score
        if winner_before <= other_before:
            return True
    return False


def main():
    print("Initializing Firebase...")
    init_firebase()

    print("Reading Firebase /results...")
    raw = db.reference("results").get() or {}
    all_results = {k: v for k, v in raw.items() if isinstance(v, dict)}

    # Collect all matches with LMW bonus
    lmw_matches = []
    for mid, result in all_results.items():
        teams = result.get("teams", {})
        if not isinstance(teams, dict):
            continue
        if any(LMW_LABEL in (td.get("breakdown") or []) for td in teams.values()):
            lmw_matches.append((int(mid), result))

    if not lmw_matches:
        print("No matches found with Last-Min Winner +4 in Firebase. Nothing to audit.")
        return

    lmw_matches.sort(key=lambda x: x[0])
    print(f"\nFound {len(lmw_matches)} match(es) with LMW bonus applied.")
    print("Scanning ESPN scoreboard (past 40 days + 7 ahead)...")
    event_map = fetch_espn_event_map(days_back=40)
    print(f"Found {len(event_map)} ESPN events mapped.\n")

    col = "{:<5} {:<22} {:<22} {:<8} {:<22} {:<7} {:<7} {}"
    print(col.format("M#", "Home", "Away", "Score", "LMW Team", "Before", "After", "Verdict"))
    print("-" * 110)

    corrections = []

    for mid, result in lmw_matches:
        home    = result.get("home", "?")
        away    = result.get("away", "?")
        hs      = result.get("homeScore", "?")
        as_     = result.get("awayScore", "?")
        score   = f"{hs}-{as_}"
        ft_win  = result.get("matchWinner")
        teams   = result.get("teams", {})

        # Find which team(s) got the bonus
        lmw_teams = [
            next((home if home.lower().replace(" ","_") == tk or tk == home else away
                  for _ in [1]), tk)
            for tk, td in teams.items()
            if LMW_LABEL in (td.get("breakdown") or [])
        ]
        lmw_display = lmw_teams[0] if lmw_teams else "?"

        # ESPN lookup
        event_id = event_map.get((home, away)) or event_map.get((away, home))
        if not event_id:
            print(col.format(f"M{mid}", home, away, score, lmw_display, "+4", "???", "no ESPN event found"))
            continue

        try:
            summary = fetch_game_summary(event_id)
        except Exception as e:
            print(col.format(f"M{mid}", home, away, score, lmw_display, "+4", "???", f"ESPN error: {e}"))
            continue

        comps = summary.get("header", {}).get("competitions") or []
        if not comps:
            print(col.format(f"M{mid}", home, away, score, lmw_display, "+4", "???", "no competition in ESPN summary"))
            continue

        details = comps[0].get("details", [])
        goals   = parse_goals(details, home, away)
        should  = lmw_new_rule(goals, home, away, ft_win)

        before_pts = 4
        after_pts  = 4 if should else 0
        delta      = after_pts - before_pts

        if delta != 0:
            verdict = f"REMOVE  (delta {delta:+d} pts)"
            corrections.append((mid, home, away, score, lmw_display, delta))
        else:
            verdict = "OK"

        print(col.format(f"M{mid}", home, away, score, lmw_display,
                         f"+{before_pts}", f"+{after_pts}", verdict))

    print("\n" + "=" * 110)
    if corrections:
        print(f"\n{len(corrections)} match(es) need correction:\n")
        for mid, home, away, score, team, delta in corrections:
            print(f"  M{mid}: {home} vs {away} ({score}) — {team} bonus delta = {delta:+d} pts")
    else:
        print("\nAll LMW bonuses correctly applied. No changes needed.")


if __name__ == "__main__":
    main()
