#!/usr/bin/env python3
"""
One-time patch: clear underdogOdds for match 3 (Canada vs Bosnia & Herz.)
so the scorer re-scores it with the corrected draw formula (÷300 not ÷150).

Usage:
  GOOGLE_APPLICATION_CREDENTIALS=/path/to/creds.json python patch_clear_underdog_odds.py
  -- or --
  FIREBASE_ADMIN_SDK_JSON='<json string>' python patch_clear_underdog_odds.py
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

MATCH_IDS_TO_RESET = [3]  # Canada vs Bosnia & Herz. (drew 1-1, Jun 12)


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


def main():
    print("Initializing Firebase…")
    init_firebase()

    codes = list(db.reference("leagues").get(shallow=True) or {})
    if not codes:
        print("No leagues found.")
        return

    for code in codes:
        results_ref = db.reference(f"leagues/{code}/data/results")
        for mid in MATCH_IDS_TO_RESET:
            match_ref = results_ref.child(str(mid))
            current = match_ref.get() or {}
            if "underdogOdds" not in current:
                print(f"  League {code} / match {mid}: no underdogOdds field — nothing to clear.")
                continue
            match_ref.child("underdogOdds").delete()
            print(f"  League {code} / match {mid}: underdogOdds cleared. Scorer will re-score on next run.")

    print("Done. Run scorer.py (or trigger the GitHub Actions workflow) to apply the fix.")


if __name__ == "__main__":
    main()
