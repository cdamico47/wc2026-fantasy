#!/usr/bin/env python3
"""
One-time patch: apply Qualify +5 bonus to all teams that played in R32.

All 32 teams that qualified from the group stage to the Round of 32 earn +5
fantasy points per the V3 scoring rules ("Advance from group"). This patch
applies the bonus to both home and away teams in each finalized R32 match
(IDs 73–88) that doesn't already have it.

Usage:
  GOOGLE_APPLICATION_CREDENTIALS=/path/to/creds.json python patch_qualify_bonus.py
  -- or --
  FIREBASE_ADMIN_SDK_JSON='<json string>' python patch_qualify_bonus.py
"""
import os
import json
import tempfile
import firebase_admin
from firebase_admin import credentials, db

FIREBASE_DB_URL = os.environ.get(
    "FIREBASE_DB_URL",
    "https://wc2026-fantasy-m47-default-rtdb.firebaseio.com"
)

QUALIFY_LABEL = "Qualify +5"
QUALIFY_PTS   = 5
R32_IDS       = range(73, 89)


def init_firebase():
    cred_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not cred_path:
        cred_json = os.environ.get("FIREBASE_ADMIN_SDK_JSON")
        if not cred_json:
            raise RuntimeError(
                "Set GOOGLE_APPLICATION_CREDENTIALS or FIREBASE_ADMIN_SDK_JSON."
            )
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        tmp.write(cred_json)
        tmp.close()
        cred_path = tmp.name
    cred = credentials.Certificate(cred_path)
    firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DB_URL})


def fb_to_dict(val):
    if isinstance(val, list):
        return {str(i): v for i, v in enumerate(val) if v is not None}
    return val or {}


def main():
    print("Initializing Firebase…")
    init_firebase()

    results_ref = db.reference("results")
    all_results = fb_to_dict(results_ref.get())

    patched = 0
    skipped = 0

    for mid in R32_IDS:
        key = str(mid)
        result = all_results.get(key)
        if not result:
            print(f"  M{mid}: not in Firebase — skipping")
            skipped += 1
            continue
        if result.get("live", False):
            print(f"  M{mid}: still live — skipping")
            skipped += 1
            continue

        home = result.get("home", "?")
        away = result.get("away", "?")
        teams = fb_to_dict(result.get("teams", {}))

        # Check if already applied (idempotent)
        already = any(
            QUALIFY_LABEL in td.get("breakdown", [])
            for td in teams.values()
        )
        if already:
            print(f"  M{mid} ({home} vs {away}): already patched — skipping")
            skipped += 1
            continue

        print(f"  M{mid} ({home} vs {away}): applying {QUALIFY_LABEL} to both teams")
        for team_key, td in teams.items():
            td["fantasyPoints"] = round(td.get("fantasyPoints", 0) + QUALIFY_PTS, 2)
            td.setdefault("breakdown", []).append(QUALIFY_LABEL)

        result["teams"] = teams
        results_ref.child(key).set(result)
        patched += 1

    print(f"\nDone. {patched} match(es) patched, {skipped} skipped.")


if __name__ == "__main__":
    main()
