#!/usr/bin/env python3
"""
Re-sum fantasy points from breakdown for every match in Firebase.

For each team in each match, parses the +N / -N value at the end of every
breakdown string, sums them, and compares to the stored fantasyPoints.
Any mismatch is corrected in Firebase and reported.
"""
import os, re, json, tempfile

import firebase_admin
from firebase_admin import credentials, db

FIREBASE_DB_URL = (os.environ.get("FIREBASE_DB_URL") or
                   "https://wc2026-fantasy-m47-default-rtdb.firebaseio.com")

PTS_RE = re.compile(r'([+-]\d+(?:\.\d+)?)$')


def init_firebase():
    cred_json = os.environ.get("FIREBASE_ADMIN_SDK_JSON")
    if not cred_json:
        raise RuntimeError("Set FIREBASE_ADMIN_SDK_JSON env var.")
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    tmp.write(cred_json)
    tmp.close()
    cred = credentials.Certificate(tmp.name)
    firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DB_URL})


def parse_pts(label: str) -> float:
    """Extract the point value from a breakdown label like 'Win R32 +8' or 'Own Goal -6'."""
    m = PTS_RE.search(label.strip())
    return float(m.group(1)) if m else 0.0


def main():
    print("Initializing Firebase...")
    init_firebase()

    print("Reading Firebase /results...")
    raw = db.reference("results").get() or {}

    fixed   = 0
    ok      = 0
    skipped = 0

    results_ref = db.reference("results")

    print(f"\n{'M#':<6} {'Team key':<28} {'Stored':>8} {'Computed':>10} {'Delta':>8}  Breakdown")
    print("-" * 110)

    mids = sorted(raw.keys(), key=lambda k: int(k) if str(k).isdigit() else 0)

    for mid in mids:
        result = raw[mid]
        if not isinstance(result, dict):
            continue

        teams = result.get("teams")
        if not isinstance(teams, dict):
            continue

        match_patched = False
        updated_teams = {k: dict(v) for k, v in teams.items() if isinstance(v, dict)}

        for team_key, td in updated_teams.items():
            breakdown = td.get("breakdown")
            if not isinstance(breakdown, list) or not breakdown:
                skipped += 1
                continue

            computed = round(sum(parse_pts(s) for s in breakdown), 2)
            stored   = round(float(td.get("fantasyPoints", 0) or 0), 2)

            if computed == stored:
                ok += 1
                continue

            delta = round(computed - stored, 2)
            bd_preview = " · ".join(breakdown[:6]) + (" …" if len(breakdown) > 6 else "")
            print(f"  M{mid:<5} {team_key:<28} {stored:>8.2f} {computed:>10.2f} {delta:>+8.2f}  {bd_preview}")

            updated_teams[team_key]["fantasyPoints"] = computed
            match_patched = True
            fixed += 1

        if match_patched:
            results_ref.child(str(mid)).child("teams").set(updated_teams)

    print("-" * 110)
    print(f"\nDone. {fixed} team(s) corrected | {ok} already correct | {skipped} skipped (no breakdown).")


if __name__ == "__main__":
    main()
