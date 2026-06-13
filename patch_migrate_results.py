#!/usr/bin/env python3
"""
One-time migration: move results from per-league paths to shared top-level /results.

Before:  leagues/{code}/data/results/{matchId}  (one copy per league)
After:   results/{matchId}                       (single shared node)

Also clears underdogOdds from match 3 (Canada vs Bosnia drew 1-1) so the
scorer re-scores it with the corrected draw formula (÷300 not ÷150).

Usage:
  GOOGLE_APPLICATION_CREDENTIALS=/path/to/creds.json python patch_migrate_results.py
  -- or --
  FIREBASE_ADMIN_SDK_JSON='<json string>' python patch_migrate_results.py
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

DRAW_FIX_MATCH_IDS = {3}  # Canada vs Bosnia & Herz. — re-score with fixed draw formula


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

    codes = list(db.reference("leagues").get(shallow=True) or {})
    if not codes:
        print("No leagues found.")
        return

    # Collect all results from all leagues (merge, deduplicate by match ID)
    merged = {}
    for code in codes:
        league_results = fb_to_dict(
            db.reference(f"leagues/{code}/data/results").get()
        )
        for mid, res in league_results.items():
            if mid not in merged and res:
                merged[mid] = res
        print(f"  League {code}: found {len(league_results)} result(s) in legacy path.")

    if merged:
        # Clear underdogOdds for draw-fix matches so scorer re-scores them
        for mid in list(merged.keys()):
            if int(mid) in DRAW_FIX_MATCH_IDS:
                merged[mid].pop("underdogOdds", None)
                print(f"  Match {mid}: cleared underdogOdds (will re-score with draw fix).")

        # Write to shared /results — MERGE with any data already there (don't overwrite scorer data)
        results_ref = db.reference("results")
        existing = fb_to_dict(results_ref.get())
        print(f"  /results already has {len(existing)} entry(ies) — merging.")
        n_added = 0
        for mid, res in merged.items():
            if mid not in existing and res:
                results_ref.child(mid).set(res)
                n_added += 1
        print(f"  Added {n_added} legacy result(s) to shared /results (skipped {len(merged) - n_added} already present).")
    else:
        print("  No results to migrate — /results will be populated by scorer on next run.")

    # Clean up legacy results from each league's data node
    for code in codes:
        db.reference(f"leagues/{code}/data/results").delete()
        print(f"  League {code}: legacy results node removed.")

    print("\nDone. Next steps:")
    print("  1. Push scorer.py + WC2026_Fantasy_App.html to GitHub")
    print("  2. Trigger the scorer workflow manually to apply the Bosnia draw fix")


if __name__ == "__main__":
    main()
