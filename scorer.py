#!/usr/bin/env python3
"""
WC 2026 Fantasy League — Phase 3 Scorer (ESPN edition)
Fetches match data from site.api.espn.com (no API key required) and writes
fantasy points to Firebase Realtime Database.

Live strategy (runs every 15 min via GitHub Actions):
  - LIVE match  → summary call → partial score from events, no stats
  - FT match    → summary call → full score with all stats
  - Already-FT  → 0 API calls (skipped — already finalized in Firebase)

Game discovery: scans ESPN scoreboard for the past 7 days so any match
missed during downtime is automatically caught up.
"""

import os
import json
import logging
import tempfile
import time
import datetime
from collections import defaultdict

import requests
import firebase_admin
from firebase_admin import credentials, db

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
ESPN_BASE     = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world"
DAYS_LOOKBACK = 7   # days of scoreboard history to scan each run

FIREBASE_DB_URL = os.environ.get("FIREBASE_DB_URL",
                  "https://wc2026-fantasy-m47-default-rtdb.firebaseio.com")

# ─────────────────────────────────────────────
# STATUS SETS
# ─────────────────────────────────────────────
LIVE_STATUSES = {
    "STATUS_IN_PROGRESS",
    "STATUS_HALFTIME",
    "STATUS_EXTRA_TIME",
    "STATUS_EXTRA_TIME_HALFTIME",
    "STATUS_PENALTY_SHOOTOUT",
}
FT_STATUSES = {
    "STATUS_FULL_TIME",
    "STATUS_FINAL_AET",
    "STATUS_FINAL_PENALTIES",
    "STATUS_ABANDONED",
    "STATUS_FORFEIT",
}

# ─────────────────────────────────────────────
# TEAM NAME NORMALIZATION  (ESPN → app)
# ─────────────────────────────────────────────
TEAM_MAP = {
    "Mexico":                        "Mexico",
    "South Africa":                  "South Africa",
    "South Korea":                   "South Korea",
    "Korea Republic":                "South Korea",
    "Czechia":                       "Czech Republic",
    "Czech Republic":                "Czech Republic",
    "Canada":                        "Canada",
    "Bosnia-Herzegovina":            "Bosnia & Herz.",
    "Bosnia and Herzegovina":        "Bosnia & Herz.",
    "United States":                 "USA",
    "USA":                           "USA",
    "Paraguay":                      "Paraguay",
    "Brazil":                        "Brazil",
    "Morocco":                       "Morocco",
    "Australia":                     "Australia",
    "Turkey":                        "Turkey",
    "Türkiye":                       "Turkey",
    "Qatar":                         "Qatar",
    "Switzerland":                   "Switzerland",
    "Haiti":                         "Haiti",
    "Scotland":                      "Scotland",
    "Germany":                       "Germany",
    "Curacao":                       "Curaçao",
    "Curaçao":                       "Curaçao",
    "Ivory Coast":                   "Ivory Coast",
    "Côte d'Ivoire":                 "Ivory Coast",
    "Ecuador":                       "Ecuador",
    "Netherlands":                   "Netherlands",
    "Japan":                         "Japan",
    "Sweden":                        "Sweden",
    "Tunisia":                       "Tunisia",
    "Spain":                         "Spain",
    "Cabo Verde":                    "Cabo Verde",
    "Cape Verde":                    "Cabo Verde",
    "Belgium":                       "Belgium",
    "Egypt":                         "Egypt",
    "Saudi Arabia":                  "Saudi Arabia",
    "Uruguay":                       "Uruguay",
    "Iran":                          "Iran",
    "New Zealand":                   "New Zealand",
    "France":                        "France",
    "Senegal":                       "Senegal",
    "Iraq":                          "Iraq",
    "Norway":                        "Norway",
    "Austria":                       "Austria",
    "Jordan":                        "Jordan",
    "Argentina":                     "Argentina",
    "Algeria":                       "Algeria",
    "England":                       "England",
    "Croatia":                       "Croatia",
    "Ghana":                         "Ghana",
    "Panama":                        "Panama",
    "Portugal":                      "Portugal",
    "DR Congo":                      "Congo DR",
    "Congo DR":                      "Congo DR",
    "Democratic Republic of Congo":  "Congo DR",
    "Uzbekistan":                    "Uzbekistan",
    "Colombia":                      "Colombia",
}


def _norm(name):
    return TEAM_MAP.get(name, name)


# ─────────────────────────────────────────────
# APP SCHEDULE  (group stage; knockouts matched dynamically)
# ─────────────────────────────────────────────
SCHEDULE = [
    # MD 1
    {"id": 1,  "home": "Mexico",         "away": "South Africa"},
    {"id": 2,  "home": "South Korea",    "away": "Czech Republic"},
    {"id": 3,  "home": "Canada",         "away": "Bosnia & Herz."},
    {"id": 4,  "home": "USA",            "away": "Paraguay"},
    {"id": 5,  "home": "Brazil",         "away": "Morocco"},
    {"id": 6,  "home": "Australia",      "away": "Turkey"},
    {"id": 7,  "home": "Qatar",          "away": "Switzerland"},
    {"id": 8,  "home": "Haiti",          "away": "Scotland"},
    {"id": 9,  "home": "Germany",        "away": "Curaçao"},
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
    # MD 2
    {"id": 25, "home": "Czech Republic", "away": "South Africa"},
    {"id": 26, "home": "Switzerland",    "away": "Bosnia & Herz."},
    {"id": 27, "home": "Canada",         "away": "Qatar"},
    {"id": 28, "home": "Mexico",         "away": "South Korea"},
    {"id": 29, "home": "USA",            "away": "Australia"},
    {"id": 30, "home": "Scotland",       "away": "Morocco"},
    {"id": 31, "home": "Brazil",         "away": "Haiti"},
    {"id": 32, "home": "Turkey",         "away": "Paraguay"},
    {"id": 33, "home": "Netherlands",    "away": "Sweden"},
    {"id": 34, "home": "Germany",        "away": "Ivory Coast"},
    {"id": 35, "home": "Tunisia",        "away": "Japan"},
    {"id": 36, "home": "Ecuador",        "away": "Curaçao"},
    {"id": 37, "home": "Spain",          "away": "Saudi Arabia"},
    {"id": 38, "home": "Belgium",        "away": "Iran"},
    {"id": 39, "home": "Uruguay",        "away": "Cabo Verde"},
    {"id": 40, "home": "New Zealand",    "away": "Egypt"},
    {"id": 41, "home": "France",         "away": "Iraq"},
    {"id": 42, "home": "Norway",         "away": "Senegal"},
    {"id": 43, "home": "Argentina",      "away": "Austria"},
    {"id": 44, "home": "Jordan",         "away": "Algeria"},
    {"id": 45, "home": "England",        "away": "Ghana"},
    {"id": 46, "home": "Panama",         "away": "Croatia"},
    {"id": 47, "home": "Portugal",       "away": "Uzbekistan"},
    {"id": 48, "home": "Colombia",       "away": "Congo DR"},
    # MD 3
    {"id": 49, "home": "Scotland",       "away": "Brazil"},
    {"id": 50, "home": "Morocco",        "away": "Haiti"},
    {"id": 51, "home": "Switzerland",    "away": "Canada"},
    {"id": 52, "home": "Bosnia & Herz.", "away": "Qatar"},
    {"id": 53, "home": "Czech Republic", "away": "Mexico"},
    {"id": 54, "home": "South Africa",   "away": "South Korea"},
    {"id": 55, "home": "Curaçao",        "away": "Ivory Coast"},
    {"id": 56, "home": "Ecuador",        "away": "Germany"},
    {"id": 57, "home": "Japan",          "away": "Sweden"},
    {"id": 58, "home": "Tunisia",        "away": "Netherlands"},
    {"id": 59, "home": "Turkey",         "away": "USA"},
    {"id": 60, "home": "Paraguay",       "away": "Australia"},
    {"id": 61, "home": "Norway",         "away": "France"},
    {"id": 62, "home": "Senegal",        "away": "Iraq"},
    {"id": 63, "home": "Cabo Verde",     "away": "Saudi Arabia"},
    {"id": 64, "home": "Uruguay",        "away": "Spain"},
    {"id": 65, "home": "Egypt",          "away": "Iran"},
    {"id": 66, "home": "New Zealand",    "away": "Belgium"},
    {"id": 67, "home": "Panama",         "away": "England"},
    {"id": 68, "home": "Croatia",        "away": "Ghana"},
    {"id": 69, "home": "Colombia",       "away": "Portugal"},
    {"id": 70, "home": "Congo DR",       "away": "Uzbekistan"},
    {"id": 71, "home": "Algeria",        "away": "Austria"},
    {"id": 72, "home": "Jordan",         "away": "Argentina"},
]

_SCHED_LOOKUP = {(m["home"], m["away"]): m["id"] for m in SCHEDULE}


# ─────────────────────────────────────────────
# ESPN CLIENT
# ─────────────────────────────────────────────

_SSL_VERIFY = os.environ.get("ESPN_SSL_VERIFY", "true").lower() != "false"


def _espn_get(path, params=None, retries=3):
    """Single ESPN API request with retry."""
    url = ESPN_BASE + path
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=30, verify=_SSL_VERIFY)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            logging.warning(f"ESPN request failed (attempt {attempt+1}): {e}")
            if attempt < retries - 1:
                time.sleep(5)
    raise RuntimeError(f"ESPN request failed after {retries} attempts: {path}")


def fetch_scoreboard_events(days_back=DAYS_LOOKBACK):
    """
    Return all WC 2026 ESPN events from the past N days.
    Each entry is the raw competition block from the scoreboard.
    De-duplicated by event ID.
    """
    seen = {}
    today = datetime.date.today()
    for i in range(days_back + 1):
        d = today - datetime.timedelta(days=i)
        date_str = d.strftime("%Y%m%d")
        try:
            data = _espn_get("/scoreboard", {"dates": date_str})
        except RuntimeError:
            logging.warning(f"  Failed to fetch scoreboard for {date_str}")
            continue
        for ev in data.get("events", []):
            if ev["id"] not in seen:
                seen[ev["id"]] = ev
    return list(seen.values())


def fetch_game_summary(event_id):
    """Return the full ESPN game summary for one event."""
    return _espn_get("/summary", {"event": event_id})


# ─────────────────────────────────────────────
# STAT HELPERS
# ─────────────────────────────────────────────

def _parse_boxscore(summary, home_name, away_name):
    """
    Extract per-team stats dict from ESPN boxscore.
    Returns {team_name: {stat_name: numeric_value}}
    """
    result = {}
    for entry in summary.get("boxscore", {}).get("teams", []):
        team = _norm(entry.get("team", {}).get("displayName", ""))
        stats = {}
        for s in entry.get("statistics", []):
            name = s.get("name", "")
            val  = s.get("displayValue", "0")
            try:
                stats[name] = float(val)
            except (ValueError, TypeError):
                stats[name] = 0.0
        result[team] = stats
    return result


def _int_stat(stats, key):
    return int(stats.get(key, 0) or 0)


def _pass_pct(stats):
    """ESPN stores passPct as 0.0–1.0 ratio; convert to 0–100."""
    raw = stats.get("passPct", 0.0) or 0.0
    if raw <= 1.0:
        return raw * 100
    return raw


def pass_accuracy_bonus(pct):
    if pct is None or pct == 0:
        return 0, None
    if pct >= 95:
        return 4, "Pass Acc ≥95% +4"
    if pct >= 90:
        return 3, "Pass Acc 90–94% +3"
    if pct >= 80:
        return 2, "Pass Acc 80–89% +2"
    if pct >= 70:
        return 1, "Pass Acc 70–79% +1"
    return 0, None


# ─────────────────────────────────────────────
# EVENT PARSING (from competition.details)
# ─────────────────────────────────────────────

def parse_details(details, home, away):
    """
    Parse competition.details into structured event lists.

    Returns:
      goals       list of {team, scorer, is_own_goal, is_penalty, clock_secs}
      pk_misses   list of {defending_team}  (penaltyKick=True, scoringPlay=False)
    """
    goals     = []
    pk_misses = []

    for d in details:
        team      = _norm((d.get("team") or {}).get("displayName", ""))
        scoring   = d.get("scoringPlay", False)
        red       = d.get("redCard", False)
        own_goal  = d.get("ownGoal", False)
        pk        = d.get("penaltyKick", False)
        clock_val = (d.get("clock") or {}).get("value", 0) or 0
        extra_val = (d.get("addedClock") or {}).get("value", 0) or 0

        if scoring:
            parts = d.get("participants", [])
            scorer = (parts[0].get("athlete", {}).get("displayName", "") if parts else "")
            goals.append({
                "team":        team,
                "scorer":      scorer,
                "is_own_goal": own_goal,
                "is_penalty":  pk,
                "clock_secs":  clock_val + extra_val,
            })
        elif pk and not scoring:
            # PK attempt that didn't score — credit defending team with a save
            defending = away if team == home else home
            pk_misses.append({"defending_team": defending})

    return goals, pk_misses


# ─────────────────────────────────────────────
# SCORING ENGINE
# ─────────────────────────────────────────────

def score_match(competition, summary, is_live=False):
    """
    Compute fantasy points for both teams from an ESPN competition block
    and its full summary.
    Returns Firebase-ready result dict.
    """
    competitors = competition.get("competitors", [])
    home_c = next((c for c in competitors if c.get("homeAway") == "home"), {})
    away_c = next((c for c in competitors if c.get("homeAway") == "away"), {})

    home = _norm(home_c.get("team", {}).get("displayName", "Home"))
    away = _norm(away_c.get("team", {}).get("displayName", "Away"))

    home_ft   = int(home_c.get("score", "0") or 0)
    away_ft   = int(away_c.get("score", "0") or 0)
    home_won  = bool(home_c.get("winner", False))
    away_won  = bool(away_c.get("winner", False))

    # Halftime scores from linescores[0]
    home_ls = home_c.get("linescores", [])
    away_ls = away_c.get("linescores", [])
    home_ht = int((home_ls[0].get("displayValue", "0") if home_ls else "0") or 0)
    away_ht = int((away_ls[0].get("displayValue", "0") if away_ls else "0") or 0)

    # Match duration type
    status_name = competition.get("status", {}).get("type", {}).get("name", "")
    in_et  = (status_name == "STATUS_FINAL_AET")
    in_pso = (status_name == "STATUS_FINAL_PENALTIES")

    # Parse events
    details   = competition.get("details", [])
    goals, pk_misses = parse_details(details, home, away)

    # Team stats from boxscore (empty for live matches — stats finalize at FT)
    team_stats = _parse_boxscore(summary, home, away)

    pts = {home: 0, away: 0}
    bd  = {home: [], away: []}

    def add(team, p, label):
        pts[team] = pts.get(team, 0) + p
        if label:
            bd[team].append(label)

    # ── 1. Result ────────────────────────────────────────────
    if home_won:
        add(home, 6, "Win +6")
        add(away, 0, "Loss 0")
    elif away_won:
        add(away, 6, "Win +6")
        add(home, 0, "Loss 0")
    else:
        add(home, 2, "Draw +2")
        add(away, 2, "Draw +2")

    # ── 2. Goals ─────────────────────────────────────────────
    team_reg     = defaultdict(int)
    team_pen     = defaultdict(int)
    team_assists = defaultdict(int)
    scorer_cnt   = defaultdict(int)
    et_teams     = set()

    for g in goals:
        team_g = g["team"]
        if g["is_own_goal"]:
            # Own goal: penalise the team whose player scored it (they concede)
            conceded_by = away if team_g == home else home
            add(conceded_by, -6, "Own Goal −6")
        elif g["is_penalty"]:
            team_pen[team_g] += 1
            if g["scorer"]:
                scorer_cnt[(g["scorer"], team_g)] += 1
        else:
            team_reg[team_g] += 1
            if g["scorer"]:
                scorer_cnt[(g["scorer"], team_g)] += 1
            # Count assist (second participant = assist provider)
            # Assists are tracked separately below via team total
            if g["clock_secs"] > 5400:  # beyond 90'
                et_teams.add(team_g)

    # Count assists from details (participants[1] indicates assist exists)
    for d in details:
        if d.get("scoringPlay") and not d.get("ownGoal"):
            parts = d.get("participants", [])
            if len(parts) >= 2:
                team_g = _norm((d.get("team") or {}).get("displayName", ""))
                team_assists[team_g] += 1

    for team in (home, away):
        if team_reg[team]:
            n = team_reg[team]
            add(team, n * 4, f"Goals ×{n} +{n*4}")
        if team_pen[team]:
            n = team_pen[team]
            add(team, n * 3, f"Pen Goals ×{n} +{n*3}")

    # ── 3. Assists ───────────────────────────────────────────
    for team in (home, away):
        n = team_assists[team]
        if n:
            add(team, n * 3, f"Assists ×{n} +{n*3}")

    # ── 4. Clean sheet ───────────────────────────────────────
    if away_ft == 0:
        add(home, 3, "Clean Sheet +3")
    if home_ft == 0:
        add(away, 3, "Clean Sheet +3")

    # ── 5. GK stats ──────────────────────────────────────────
    # PK saves: opponent's penaltyKickShots - penaltyKickGoals
    pk_saves = {home: 0, away: 0}
    for pm in pk_misses:
        pk_saves[pm["defending_team"]] += 1

    for team in (home, away):
        tstats       = team_stats.get(team, {})
        gk_saves     = _int_stat(tstats, "saves")
        goals_conceded = away_ft if team == home else home_ft

        if not is_live:
            # GK heroics: 5+ saves AND ≤1 goal conceded (not clean sheet)
            already_clean = (goals_conceded == 0)
            if gk_saves >= 5 and goals_conceded <= 1 and not already_clean:
                add(team, 3, f"GK Heroics ({gk_saves} saves) +3")

            # PK saves +10 each
            if pk_saves[team]:
                n = pk_saves[team]
                add(team, n * 10, f"Pen Save ×{n} +{n*10}")

            # Open-play saves: +4 each (subtract PK saves from total)
            open_saves = max(0, gk_saves - pk_saves[team])
            if open_saves:
                add(team, open_saves * 4, f"Saves ×{open_saves} +{open_saves*4}")

    # ── 6. Shots on target ───────────────────────────────────
    for team in (home, away):
        tstats = team_stats.get(team, {})
        sot    = _int_stat(tstats, "shotsOnTarget")
        if sot and not is_live:
            p = round(sot * 0.75, 2)
            add(team, p, f"SOT ×{sot} +{p}")
            if sot >= 10:
                add(team, 4, "10+ SOT +4")

    # ── 7. Pass accuracy bonus ───────────────────────────────
    if not is_live:
        for team in (home, away):
            tstats       = team_stats.get(team, {})
            pct          = _pass_pct(tstats)
            bonus, label = pass_accuracy_bonus(pct)
            if bonus:
                add(team, bonus, label)

    # ── 8. Offsides ──────────────────────────────────────────
    if not is_live:
        for team in (home, away):
            tstats = team_stats.get(team, {})
            off    = _int_stat(tstats, "offsides")
            if off:
                p = round(off * -0.5, 2)
                add(team, p, f"Offsides ×{off} {p}")

    # ── 9. Tackles won ───────────────────────────────────────
    if not is_live:
        for team in (home, away):
            tstats  = team_stats.get(team, {})
            tackles = _int_stat(tstats, "effectiveTackles")
            if tackles:
                p = round(tackles * 0.5, 2)
                add(team, p, f"Tackles ×{tackles} +{p}")

    # ── 10. Cards ────────────────────────────────────────────
    for team in (home, away):
        tstats = team_stats.get(team, {})
        yellows = _int_stat(tstats, "yellowCards")
        reds    = _int_stat(tstats, "redCards")
        if yellows:
            p = yellows * -2
            add(team, p, f"Yellow ×{yellows} {p}")
        if reds:
            p = reds * -5
            add(team, p, f"Red ×{reds} {p}")

    # ── 11. Hat trick ────────────────────────────────────────
    for (scorer, team), count in scorer_cnt.items():
        if count >= 3:
            add(team, 10, "Hat Trick +10")
            break

    # ── 12. Dominant win (3+ goal margin) ────────────────────
    margin = abs(home_ft - away_ft)
    if margin >= 3:
        dom = home if home_ft > away_ft else away
        add(dom, 4, "Dominant Win +4")

    # ── 13. Comeback win (losing at HT, won at FT) ───────────
    ht_leader = home if home_ht > away_ht else (away if away_ht > home_ht else None)
    ft_winner = home if home_won else (away if away_won else None)
    if ht_leader and ft_winner and ht_leader != ft_winner:
        add(ft_winner, 4, "Comeback Win (HT) +4")

    # ── 14. Comeback from 2-goal deficit ─────────────────────
    run_h, run_a = 0, 0
    was_2down = {home: False, away: False}
    for g in sorted(goals, key=lambda x: x["clock_secs"]):
        if g["is_own_goal"]:
            continue
        team_g = g["team"]
        if team_g == home:
            run_h += 1
        else:
            run_a += 1
        if run_a - run_h >= 2:
            was_2down[home] = True
        if run_h - run_a >= 2:
            was_2down[away] = True

    if was_2down[home] and home_ft >= away_ft:
        add(home, 7, "Comeback 2 Down +7")
    if was_2down[away] and away_ft >= home_ft:
        add(away, 7, "Comeback 2 Down +7")

    # ── 15. Win with 10 men ──────────────────────────────────
    for team in (home, away):
        tstats = team_stats.get(team, {})
        reds   = _int_stat(tstats, "redCards")
        won    = (team == home and home_won) or (team == away and away_won)
        if reds > 0 and won:
            add(team, 10, "Win w/ 10 Men +10")

    # ── 16. Extra-time goals ─────────────────────────────────
    for team in et_teams:
        add(team, 4, "ET Goal +4")

    # ── 17. Last-minute winner (88'+) ────────────────────────
    if ft_winner:
        win_goal_secs = [
            g["clock_secs"]
            for g in goals
            if g["team"] == ft_winner and not g["is_own_goal"]
        ]
        if win_goal_secs and max(win_goal_secs) >= 88 * 60:
            add(ft_winner, 4, "Last-Min Winner +4")

    # ── Stats summary string ─────────────────────────────────
    if is_live:
        status_str = "LIVE"
    elif in_pso:
        status_str = "PSO"
    elif in_et:
        status_str = "AET"
    else:
        status_str = "FT"

    stats_str = (
        f"{status_str}: {home} {home_ft}-{away_ft} {away}"
        f" | HT: {home_ht}-{away_ht}"
        + (" | Partial — stats update at FT" if is_live else "")
    )

    return {
        "live": is_live,
        "teams": {
            home: {"fantasyPoints": round(pts[home], 2), "breakdown": bd[home]},
            away: {"fantasyPoints": round(pts[away], 2), "breakdown": bd[away]},
        },
        "stats": stats_str,
        "finalScore": {"home": home_ft, "away": away_ft},
    }


# ─────────────────────────────────────────────
# MATCH ID RESOLUTION
# ─────────────────────────────────────────────

def resolve_schedule_id(home, away):
    """Return app schedule match ID for a (home, away) pair, or None."""
    return _SCHED_LOOKUP.get((home, away))


# ─────────────────────────────────────────────
# FIREBASE
# ─────────────────────────────────────────────

def init_firebase():
    cred_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not cred_path:
        cred_json = os.environ.get("FIREBASE_ADMIN_SDK_JSON")
        if not cred_json:
            raise RuntimeError(
                "No Firebase credentials. Set GOOGLE_APPLICATION_CREDENTIALS "
                "or FIREBASE_ADMIN_SDK_JSON."
            )
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        tmp.write(cred_json)
        tmp.close()
        cred_path = tmp.name

    cred = credentials.Certificate(cred_path)
    firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DB_URL})


def get_finalized_match_ids():
    """Return set of match IDs already finalized (non-live) in Firebase."""
    codes = list(db.reference("leagues").get(shallow=True) or {})
    if not codes:
        return set()
    ref  = db.reference(f"leagues/{codes[0]}/data/results")
    data = ref.get() or {}
    return {k for k, v in data.items() if not (v or {}).get("live", True)}


def write_results(results_by_match_id):
    codes = db.reference("leagues").get(shallow=True) or {}
    for code in codes:
        results_ref = db.reference(f"leagues/{code}/data/results")
        existing    = results_ref.get() or {}
        n_written   = 0
        for match_id, result in results_by_match_id.items():
            key = str(match_id)
            if json.dumps(existing.get(key), sort_keys=True) != \
               json.dumps(result, sort_keys=True):
                results_ref.child(key).set(result)
                n_written += 1
        logging.info(f"  League {code}: {n_written} result(s) written.")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
    )

    logging.info("Initializing Firebase…")
    init_firebase()

    finalized = get_finalized_match_ids()

    logging.info(f"Scanning ESPN scoreboard (last {DAYS_LOOKBACK} days)…")
    events = fetch_scoreboard_events(DAYS_LOOKBACK)
    logging.info(f"  Found {len(events)} WC 2026 event(s).")

    live_events = []
    ft_events   = []
    for ev in events:
        comp        = ev.get("competitions", [{}])[0]
        status_name = comp.get("status", {}).get("type", {}).get("name", "")
        if status_name in LIVE_STATUSES:
            live_events.append((ev["id"], comp))
        elif status_name in FT_STATUSES:
            ft_events.append((ev["id"], comp))

    logging.info(f"  {len(live_events)} live | {len(ft_events)} finished.")

    results = {}

    # ── LIVE matches ─────────────────────────────────────────
    for event_id, comp in live_events:
        competitors = comp.get("competitors", [])
        home_c = next((c for c in competitors if c.get("homeAway") == "home"), {})
        away_c = next((c for c in competitors if c.get("homeAway") == "away"), {})
        home = _norm(home_c.get("team", {}).get("displayName", "?"))
        away = _norm(away_c.get("team", {}).get("displayName", "?"))
        sid  = resolve_schedule_id(home, away)
        if sid is None:
            logging.warning(f"  No schedule match for: {home} vs {away}")
            continue

        logging.info(f"  [LIVE] Match {sid} ({home} vs {away}, event {event_id})…")
        summary      = fetch_game_summary(event_id)
        # Use competition block from summary (has details)
        comp_full    = (summary.get("header", {}).get("competitions") or [comp])[0]
        result       = score_match(comp_full, summary, is_live=True)
        results[sid] = result
        teams_list   = list(result["teams"].items())
        h, a         = teams_list[0], teams_list[1]
        logging.info(
            f"    {h[0]} {result['finalScore']['home']}-"
            f"{result['finalScore']['away']} {a[0]}  (in progress)"
            f"  |  {h[0]}: {h[1]['fantasyPoints']} pts"
            f"  |  {a[0]}: {a[1]['fantasyPoints']} pts"
        )

    # ── FINISHED matches ─────────────────────────────────────
    for event_id, comp in ft_events:
        competitors = comp.get("competitors", [])
        home_c = next((c for c in competitors if c.get("homeAway") == "home"), {})
        away_c = next((c for c in competitors if c.get("homeAway") == "away"), {})
        home = _norm(home_c.get("team", {}).get("displayName", "?"))
        away = _norm(away_c.get("team", {}).get("displayName", "?"))
        sid  = resolve_schedule_id(home, away)
        if sid is None:
            logging.warning(f"  No schedule match for: {home} vs {away}")
            continue

        if str(sid) in finalized:
            logging.info(f"  Match {sid} ({home} vs {away}): finalized — skipping.")
            continue

        logging.info(f"  [FT]   Match {sid} ({home} vs {away}, event {event_id})…")
        summary      = fetch_game_summary(event_id)
        comp_full    = (summary.get("header", {}).get("competitions") or [comp])[0]
        result       = score_match(comp_full, summary, is_live=False)
        results[sid] = result
        teams_list   = list(result["teams"].items())
        h, a         = teams_list[0], teams_list[1]
        logging.info(
            f"    {h[0]} {result['finalScore']['home']}-"
            f"{result['finalScore']['away']} {a[0]}"
            f"  |  {h[0]}: {h[1]['fantasyPoints']} pts"
            f"  |  {a[0]}: {a[1]['fantasyPoints']} pts"
        )

    if results:
        logging.info(f"Writing {len(results)} result(s) to Firebase…")
        write_results(results)
        logging.info("Done.")
    else:
        logging.info("No new results to write.")


if __name__ == "__main__":
    main()
