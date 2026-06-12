#!/usr/bin/env python3
"""
WC 2026 Fantasy League — Phase 3 Scorer
Fetches match data from api-football.com (api-sports.io) and writes
fantasy points to Firebase Realtime Database.

Live strategy (runs every 10 min via GitHub Actions):
  - LIVE match  → 1 API call (events only) → partial score, live:true in Firebase
  - FT match    → 3 API calls (events + statistics + players) → final score, live:false
  - Already-FT  → 0 API calls (skipped — already finalized in Firebase)

Free tier budget: 100 req/day. Worst case ~90/day with 2 concurrent live matches.
"""

import os
import json
import logging
import tempfile
import time
from collections import defaultdict

import requests
import firebase_admin
from firebase_admin import credentials, db

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
API_FOOTBALL_KEY  = os.environ.get("API_FOOTBALL_KEY", "")
API_FOOTBALL_BASE = "https://v3.football.api-sports.io"
WC_LEAGUE_ID      = 1      # FIFA World Cup on api-football
WC_SEASON         = 2026

FIREBASE_DB_URL   = os.environ.get("FIREBASE_DB_URL",
                    "https://wc2026-fantasy-m47-default-rtdb.firebaseio.com")

# ─────────────────────────────────────────────
# TEAM NAME NORMALIZATION  (api-football → app)
# ─────────────────────────────────────────────
TEAM_MAP = {
    "Mexico":                       "Mexico",
    "South Africa":                 "South Africa",
    "Korea Republic":               "South Korea",
    "Czech Republic":               "Czech Republic",
    "Canada":                       "Canada",
    "Bosnia and Herzegovina":       "Bosnia & Herz.",
    "United States":                "USA",
    "Paraguay":                     "Paraguay",
    "Brazil":                       "Brazil",
    "Morocco":                      "Morocco",
    "Australia":                    "Australia",
    "Turkey":                       "Turkey",
    "Türkiye":                      "Turkey",
    "Qatar":                        "Qatar",
    "Switzerland":                  "Switzerland",
    "Haiti":                        "Haiti",
    "Scotland":                     "Scotland",
    "Germany":                      "Germany",
    "Curacao":                      "Curaçao",
    "Curaçao":                      "Curaçao",
    "Ivory Coast":                  "Ivory Coast",
    "Côte d'Ivoire":                "Ivory Coast",
    "Ecuador":                      "Ecuador",
    "Netherlands":                  "Netherlands",
    "Japan":                        "Japan",
    "Sweden":                       "Sweden",
    "Tunisia":                      "Tunisia",
    "Spain":                        "Spain",
    "Cabo Verde":                   "Cabo Verde",
    "Cape Verde":                   "Cabo Verde",
    "Belgium":                      "Belgium",
    "Egypt":                        "Egypt",
    "Saudi Arabia":                 "Saudi Arabia",
    "Uruguay":                      "Uruguay",
    "Iran":                         "Iran",
    "New Zealand":                  "New Zealand",
    "France":                       "France",
    "Senegal":                      "Senegal",
    "Iraq":                         "Iraq",
    "Norway":                       "Norway",
    "Austria":                      "Austria",
    "Jordan":                       "Jordan",
    "Argentina":                    "Argentina",
    "Algeria":                      "Algeria",
    "England":                      "England",
    "Croatia":                      "Croatia",
    "Ghana":                        "Ghana",
    "Panama":                       "Panama",
    "Portugal":                     "Portugal",
    "DR Congo":                     "Congo DR",
    "Congo DR":                     "Congo DR",
    "Democratic Republic of Congo": "Congo DR",
    "Uzbekistan":                   "Uzbekistan",
    "Colombia":                     "Colombia",
}

# ─────────────────────────────────────────────
# APP SCHEDULE  (group stage; knockouts matched dynamically)
# ─────────────────────────────────────────────
SCHEDULE = [
    # MD 1
    {"id":1,  "home":"Mexico",         "away":"South Africa"},
    {"id":2,  "home":"South Korea",    "away":"Czech Republic"},
    {"id":3,  "home":"Canada",         "away":"Bosnia & Herz."},
    {"id":4,  "home":"USA",            "away":"Paraguay"},
    {"id":5,  "home":"Brazil",         "away":"Morocco"},
    {"id":6,  "home":"Australia",      "away":"Turkey"},
    {"id":7,  "home":"Qatar",          "away":"Switzerland"},
    {"id":8,  "home":"Haiti",          "away":"Scotland"},
    {"id":9,  "home":"Germany",        "away":"Curaçao"},
    {"id":10, "home":"Ivory Coast",    "away":"Ecuador"},
    {"id":11, "home":"Netherlands",    "away":"Japan"},
    {"id":12, "home":"Sweden",         "away":"Tunisia"},
    {"id":13, "home":"Spain",          "away":"Cabo Verde"},
    {"id":14, "home":"Belgium",        "away":"Egypt"},
    {"id":15, "home":"Saudi Arabia",   "away":"Uruguay"},
    {"id":16, "home":"Iran",           "away":"New Zealand"},
    {"id":17, "home":"France",         "away":"Senegal"},
    {"id":18, "home":"Iraq",           "away":"Norway"},
    {"id":19, "home":"Austria",        "away":"Jordan"},
    {"id":20, "home":"Argentina",      "away":"Algeria"},
    {"id":21, "home":"England",        "away":"Croatia"},
    {"id":22, "home":"Ghana",          "away":"Panama"},
    {"id":23, "home":"Portugal",       "away":"Congo DR"},
    {"id":24, "home":"Uzbekistan",     "away":"Colombia"},
    # MD 2
    {"id":25, "home":"Czech Republic", "away":"South Africa"},
    {"id":26, "home":"Switzerland",    "away":"Bosnia & Herz."},
    {"id":27, "home":"Canada",         "away":"Qatar"},
    {"id":28, "home":"Mexico",         "away":"South Korea"},
    {"id":29, "home":"USA",            "away":"Australia"},
    {"id":30, "home":"Scotland",       "away":"Morocco"},
    {"id":31, "home":"Brazil",         "away":"Haiti"},
    {"id":32, "home":"Turkey",         "away":"Paraguay"},
    {"id":33, "home":"Netherlands",    "away":"Sweden"},
    {"id":34, "home":"Germany",        "away":"Ivory Coast"},
    {"id":35, "home":"Tunisia",        "away":"Japan"},
    {"id":36, "home":"Ecuador",        "away":"Curaçao"},
    {"id":37, "home":"Spain",          "away":"Saudi Arabia"},
    {"id":38, "home":"Belgium",        "away":"Iran"},
    {"id":39, "home":"Uruguay",        "away":"Cabo Verde"},
    {"id":40, "home":"New Zealand",    "away":"Egypt"},
    {"id":41, "home":"France",         "away":"Iraq"},
    {"id":42, "home":"Norway",         "away":"Senegal"},
    {"id":43, "home":"Argentina",      "away":"Austria"},
    {"id":44, "home":"Jordan",         "away":"Algeria"},
    {"id":45, "home":"England",        "away":"Ghana"},
    {"id":46, "home":"Panama",         "away":"Croatia"},
    {"id":47, "home":"Portugal",       "away":"Uzbekistan"},
    {"id":48, "home":"Colombia",       "away":"Congo DR"},
    # MD 3
    {"id":49, "home":"Scotland",       "away":"Brazil"},
    {"id":50, "home":"Morocco",        "away":"Haiti"},
    {"id":51, "home":"Switzerland",    "away":"Canada"},
    {"id":52, "home":"Bosnia & Herz.", "away":"Qatar"},
    {"id":53, "home":"Czech Republic", "away":"Mexico"},
    {"id":54, "home":"South Africa",   "away":"South Korea"},
    {"id":55, "home":"Curaçao",        "away":"Ivory Coast"},
    {"id":56, "home":"Ecuador",        "away":"Germany"},
    {"id":57, "home":"Japan",          "away":"Sweden"},
    {"id":58, "home":"Tunisia",        "away":"Netherlands"},
    {"id":59, "home":"Turkey",         "away":"USA"},
    {"id":60, "home":"Paraguay",       "away":"Australia"},
    {"id":61, "home":"Norway",         "away":"France"},
    {"id":62, "home":"Senegal",        "away":"Iraq"},
    {"id":63, "home":"Cabo Verde",     "away":"Saudi Arabia"},
    {"id":64, "home":"Uruguay",        "away":"Spain"},
    {"id":65, "home":"Egypt",          "away":"Iran"},
    {"id":66, "home":"New Zealand",    "away":"Belgium"},
    {"id":67, "home":"Panama",         "away":"England"},
    {"id":68, "home":"Croatia",        "away":"Ghana"},
    {"id":69, "home":"Colombia",       "away":"Portugal"},
    {"id":70, "home":"Congo DR",       "away":"Uzbekistan"},
    {"id":71, "home":"Algeria",        "away":"Austria"},
    {"id":72, "home":"Jordan",         "away":"Argentina"},
]

_SCHED_LOOKUP = {(m["home"], m["away"]): m["id"] for m in SCHEDULE}


def _norm(name):
    return TEAM_MAP.get(name, name)


# ─────────────────────────────────────────────
# API-FOOTBALL CLIENT
# ─────────────────────────────────────────────

def _af_get(path, params=None):
    """Single API-Football request with rate-limit retry."""
    url     = API_FOOTBALL_BASE + path
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    for attempt in range(3):
        r = requests.get(url, headers=headers, params=params, timeout=30)
        if r.status_code == 429:
            logging.warning("Rate limited — sleeping 60 s…")
            time.sleep(60)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"API-Football request failed after 3 attempts: {path}")


def get_wc_league_id():
    """Return the league ID for FIFA World Cup 2026 (usually 1, verified live)."""
    data    = _af_get("/leagues", {"name": "FIFA World Cup", "season": WC_SEASON})
    entries = data.get("response", [])
    if entries:
        return entries[0]["league"]["id"]
    return WC_LEAGUE_ID   # fallback


LIVE_STATUSES = {"1H", "HT", "2H", "ET", "BT", "P", "SUSP", "INT", "LIVE"}
FT_STATUSES   = {"FT", "AET", "PEN"}


def fetch_active_fixtures():
    """
    Return all WC 2026 fixtures that are either currently LIVE or FINISHED.
    One API call covers both. Returns (live_list, ft_list).
    """
    status_str = "-".join(sorted(LIVE_STATUSES | FT_STATUSES))
    data = _af_get("/fixtures", {
        "league": WC_LEAGUE_ID,
        "season": WC_SEASON,
        "status": status_str,
    })
    all_fixtures = data.get("response", [])
    live_fixtures = [f for f in all_fixtures
                     if (f.get("fixture") or {}).get("status", {}).get("short") in LIVE_STATUSES]
    ft_fixtures   = [f for f in all_fixtures
                     if (f.get("fixture") or {}).get("status", {}).get("short") in FT_STATUSES]
    return live_fixtures, ft_fixtures


def fetch_events(fixture_id):
    """Goals (type, detail, minute, scorer, assist) and cards for one fixture."""
    data = _af_get("/fixtures/events", {"fixture": fixture_id})
    return data.get("response", [])


def fetch_statistics(fixture_id):
    """
    Per-team stats dict for one fixture.
    Returns: {app_team_name: {stat_type: value, ...}, ...}
    """
    data    = _af_get("/fixtures/statistics", {"fixture": fixture_id})
    result  = {}
    for entry in data.get("response", []):
        team_name = _norm(entry["team"]["name"])
        stats     = {s["type"]: s["value"] for s in entry.get("statistics", [])}
        result[team_name] = stats
    return result


def fetch_player_stats(fixture_id):
    """
    Per-player stats for tackles (team aggregate).
    Returns: {app_team_name: {"tackles": int}, ...}
    """
    data   = _af_get("/fixtures/players", {"fixture": fixture_id})
    result = defaultdict(lambda: {"tackles": 0})
    for team_entry in data.get("response", []):
        team_name = _norm(team_entry["team"]["name"])
        for player in team_entry.get("players", []):
            stats   = (player.get("statistics") or [{}])[0]
            tackles = (stats.get("tackles") or {}).get("total") or 0
            result[team_name]["tackles"] += tackles
    return dict(result)


# ─────────────────────────────────────────────
# STAT HELPERS
# ─────────────────────────────────────────────

def _int_stat(stats, key):
    v = stats.get(key)
    if v is None:
        return 0
    if isinstance(v, str):
        return int(v.replace("%", "").strip() or 0)
    return int(v or 0)


def _pct_stat(stats, key):
    """Return pass-accuracy percentage as a float (0–100)."""
    v = stats.get(key)
    if v is None:
        return None
    if isinstance(v, str):
        v = v.replace("%", "").strip()
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def pass_accuracy_bonus(pct):
    if pct is None:
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
# SCORING ENGINE
# ─────────────────────────────────────────────

def score_match(fixture, events, team_stats, player_stats, is_live=False):
    """
    Compute fantasy points for both teams.
    is_live=True: partial score from events only (stats/players not available).
    Returns Firebase-ready result dict.
    """
    teams_data = fixture.get("teams", {})
    score_data = fixture.get("score", {})
    goals_data = fixture.get("goals", {})

    home = _norm(teams_data.get("home", {}).get("name", "Home"))
    away = _norm(teams_data.get("away", {}).get("name", "Away"))

    home_ft = goals_data.get("home") or 0
    away_ft = goals_data.get("away") or 0
    home_ht = (score_data.get("halftime") or {}).get("home") or 0
    away_ht = (score_data.get("halftime") or {}).get("away") or 0

    # Duration: check extratime / penalty shootout
    ext_score = score_data.get("extratime") or {}
    pen_score  = score_data.get("penalty")  or {}
    in_et  = (ext_score.get("home") is not None)
    in_pso = (pen_score.get("home")  is not None)

    winner_team  = teams_data.get("home", {}) if fixture.get("teams", {}).get("home", {}).get("winner") else \
                   teams_data.get("away", {}) if fixture.get("teams", {}).get("away", {}).get("winner") else None
    home_won = teams_data.get("home", {}).get("winner", False)
    away_won = teams_data.get("away", {}).get("winner", False)

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

    # ── 2. Goals (from events) ───────────────────────────────
    team_reg   = defaultdict(int)
    team_pen   = defaultdict(int)
    team_assists = defaultdict(int)
    scorer_cnt = defaultdict(int)
    et_teams   = set()
    card_yellows = defaultdict(int)
    card_reds    = defaultdict(int)
    pen_saves    = defaultdict(int)  # GK penalty saves

    for ev in events:
        ev_team    = _norm((ev.get("team") or {}).get("name", ""))
        ev_type    = (ev.get("type") or "").lower()
        ev_detail  = (ev.get("detail") or "").lower()
        ev_minute  = (ev.get("time") or {}).get("elapsed") or 0
        ev_extra   = (ev.get("time") or {}).get("extra") or 0
        total_min  = ev_minute + ev_extra
        scorer     = (ev.get("player") or {}).get("name", "")
        assist_p   = (ev.get("assist") or {}).get("name", "")

        if ev_type == "goal":
            if ev_detail == "own goal":
                conceded_by = away if ev_team == home else home
                add(conceded_by, -6, "Own Goal −6")
            elif ev_detail == "penalty":
                team_pen[ev_team] += 1
                if scorer:
                    scorer_cnt[(scorer, ev_team)] += 1
                if assist_p:
                    team_assists[ev_team] += 1
            elif ev_detail == "missed penalty":
                # The opposing team's GK saved or player missed wide — can't
                # distinguish without extra data; credit GK save conservatively
                # only when it's "Penalty Saved" detail (api-football uses both)
                pass
            else:
                team_reg[ev_team] += 1
                if scorer:
                    scorer_cnt[(scorer, ev_team)] += 1
                if assist_p:
                    team_assists[ev_team] += 1
                if total_min > 90:
                    et_teams.add(ev_team)

        elif ev_type == "goal" and ev_detail == "penalty saved":
            # GK penalty save: credit the defending team
            defending = away if ev_team == home else home
            pen_saves[defending] += 1

        elif ev_type == "card":
            if "red" in ev_detail and "yellow" not in ev_detail:
                card_reds[ev_team] += 1
            elif "yellow red" in ev_detail:
                card_reds[ev_team] += 1
            elif "yellow" in ev_detail:
                card_yellows[ev_team] += 1

    # Penalty saves can also appear as a separate event type in some API responses
    # Re-scan specifically for "Penalty Saved" detail under Goal type
    for ev in events:
        if (ev.get("type") or "").lower() == "goal" and \
           (ev.get("detail") or "").lower() == "penalty saved":
            ev_team    = _norm((ev.get("team") or {}).get("name", ""))
            defending  = away if ev_team == home else home
            pen_saves[defending] += 1

    # Score goals
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

    # ── 5. Goalkeeper stats (from statistics) ────────────────
    for team in (home, away):
        tstats = team_stats.get(team, {})

        gk_saves     = _int_stat(tstats, "Goalkeeper Saves")
        sot_conceded = home_ft if team == away else away_ft  # goals conceded

        # GK heroics: 5+ saves AND ≤1 goal conceded (does NOT stack with clean sheet)
        already_clean = (sot_conceded == 0)
        if gk_saves >= 5 and sot_conceded <= 1 and not already_clean:
            add(team, 3, f"GK Heroics ({gk_saves} saves) +3")

        # GK penalty saves
        if pen_saves[team]:
            n = pen_saves[team]
            add(team, n * 10, f"Pen Save ×{n} +{n*10}")

        # Individual saves (open play): +4 each
        # api-football "Goalkeeper Saves" = total saves including penalties
        # We subtract pen saves to approximate open-play saves
        open_saves = max(0, gk_saves - pen_saves[team])
        if open_saves:
            add(team, open_saves * 4, f"Saves ×{open_saves} +{open_saves*4}")

    # ── 6. Shots on target ───────────────────────────────────
    for team in (home, away):
        tstats = team_stats.get(team, {})
        sot = _int_stat(tstats, "Shots on Goal")
        if sot:
            p = round(sot * 0.75, 2)
            add(team, p, f"SOT ×{sot} +{p}")
        if sot >= 10:
            add(team, 4, "10+ SOT +4")

    # ── 7. Pass accuracy bonus ───────────────────────────────
    for team in (home, away):
        tstats = team_stats.get(team, {})
        pct    = _pct_stat(tstats, "Passes %")
        bonus, label = pass_accuracy_bonus(pct)
        if bonus:
            add(team, bonus, label)

    # ── 8. Offsides ──────────────────────────────────────────
    for team in (home, away):
        tstats = team_stats.get(team, {})
        off    = _int_stat(tstats, "Offsides")
        if off:
            p = round(off * -0.5, 2)
            add(team, p, f"Offsides ×{off} {p}")

    # ── 9. Tackles won ───────────────────────────────────────
    for team in (home, away):
        tackles = (player_stats.get(team) or {}).get("tackles", 0)
        if tackles:
            p = round(tackles * 0.5, 2)
            add(team, p, f"Tackles ×{tackles} +{p}")

    # ── 10. Cards ────────────────────────────────────────────
    for team in (home, away):
        if card_yellows[team]:
            n = card_yellows[team]; p = n * -2
            add(team, p, f"Yellow ×{n} {p}")
        if card_reds[team]:
            n = card_reds[team]; p = n * -5
            add(team, p, f"Red ×{n} {p}")

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
    for ev in sorted(events, key=lambda e: (e.get("time") or {}).get("elapsed") or 0):
        if (ev.get("type") or "").lower() != "goal":
            continue
        detail = (ev.get("detail") or "").lower()
        if detail == "own goal":
            continue
        ev_team = _norm((ev.get("team") or {}).get("name", ""))
        if ev_team == home:
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
        if card_reds[team] > 0:
            won = (team == home and home_won) or (team == away and away_won)
            if won:
                add(team, 10, "Win w/ 10 Men +10")

    # ── 16. Extra-time goals ─────────────────────────────────
    for team in et_teams:
        add(team, 4, "ET Goal +4")

    # ── 17. Last-minute winner (88'+) ────────────────────────
    if ft_winner:
        win_goal_mins = [
            (ev.get("time") or {}).get("elapsed", 0)
            for ev in events
            if (ev.get("type") or "").lower() == "goal"
            and _norm((ev.get("team") or {}).get("name", "")) == ft_winner
            and (ev.get("detail") or "").lower() not in ("own goal",)
        ]
        if win_goal_mins and max(win_goal_mins) >= 88:
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

def resolve_schedule_id(fixture):
    home = _norm(fixture.get("teams", {}).get("home", {}).get("name", ""))
    away = _norm(fixture.get("teams", {}).get("away", {}).get("name", ""))
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


def get_already_scored(league_code):
    """Return set of match IDs already in Firebase for this league."""
    ref = db.reference(f"leagues/{league_code}/results")
    data = ref.get(shallow=True) or {}
    return set(data.keys())


def write_results(results_by_match_id):
    codes = db.reference("leagues").get(shallow=True) or {}
    for code in codes:
        results_ref = db.reference(f"leagues/{code}/results")
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

    if not API_FOOTBALL_KEY:
        raise RuntimeError("API_FOOTBALL_KEY not set.")

    logging.info("Initializing Firebase…")
    init_firebase()

    # Which match IDs are already FINALIZED in Firebase (live results get overwritten)
    codes    = list(db.reference("leagues").get(shallow=True) or {})
    finalized = set()
    if codes:
        ref  = db.reference(f"leagues/{codes[0]}/results")
        data = ref.get() or {}
        finalized = {k for k, v in data.items() if not (v or {}).get("live", True)}

    logging.info("Fetching active WC 2026 fixtures…")
    live_fixtures, ft_fixtures = fetch_active_fixtures()
    logging.info(f"  {len(live_fixtures)} live | {len(ft_fixtures)} finished.")

    results   = {}
    api_calls = 1  # counted the fetch_active_fixtures call

    # ── LIVE matches: 1 call each (events only, partial score) ──
    for fix in live_fixtures:
        sid = resolve_schedule_id(fix)
        if sid is None:
            continue
        fid = fix["fixture"]["id"]
        logging.info(f"  [LIVE] Match {sid} (fixture {fid})…")
        events       = fetch_events(fid);  api_calls += 1
        result       = score_match(fix, events, {}, {}, is_live=True)
        results[sid] = result
        teams = list(result["teams"].items())
        h, a  = teams[0], teams[1]
        logging.info(
            f"    {h[0]} {result['finalScore']['home']}-"
            f"{result['finalScore']['away']} {a[0]}  (in progress)"
            f"  |  {h[0]}: {h[1]['fantasyPoints']} pts"
            f"  |  {a[0]}: {a[1]['fantasyPoints']} pts"
        )

    # ── FINISHED matches: 3 calls each (skip already-finalized) ──
    for fix in ft_fixtures:
        sid = resolve_schedule_id(fix)
        if sid is None:
            h = fix.get("teams", {}).get("home", {}).get("name", "?")
            a = fix.get("teams", {}).get("away", {}).get("name", "?")
            logging.warning(f"  No schedule match for: {h} vs {a}")
            continue

        if str(sid) in finalized:
            logging.info(f"  Match {sid}: finalized — skipping.")
            continue

        fid = fix["fixture"]["id"]
        logging.info(f"  [FT]   Match {sid} (fixture {fid})…")
        events       = fetch_events(fid);       api_calls += 1
        team_stats   = fetch_statistics(fid);   api_calls += 1
        player_stats = fetch_player_stats(fid); api_calls += 1

        result       = score_match(fix, events, team_stats, player_stats, is_live=False)
        results[sid] = result
        teams = list(result["teams"].items())
        h, a  = teams[0], teams[1]
        logging.info(
            f"    {h[0]} {result['finalScore']['home']}-"
            f"{result['finalScore']['away']} {a[0]}"
            f"  |  {h[0]}: {h[1]['fantasyPoints']} pts"
            f"  |  {a[0]}: {a[1]['fantasyPoints']} pts"
        )

    logging.info(f"API calls used this run: {api_calls}")

    if results:
        logging.info(f"Writing {len(results)} result(s) to Firebase…")
        write_results(results)
        logging.info("Done.")
    else:
        logging.info("No new results to write.")


if __name__ == "__main__":
    main()
