#!/usr/bin/env python3
"""
Pre-populate / fix _ko_registry for R16 matches (IDs 89-96).

The scorer assigns KO match IDs chronologically, but the app's SCHEDULE
uses a bracket-slot ordering. For same-day games (M89 at 21:00 UTC and
M90 at 17:00 UTC on July 4) the chronological scorer would assign them
backwards. This script reads the finalized R32 results, determines the
correct R16 team pairings from the bracket, and writes them to the
registry BEFORE the scorer processes any July 4 games.

R16 bracket:
  M89 = W74 vs W77   (Germany/Paraguay winner vs France/Sweden winner)
  M90 = W73 vs W75   (South Africa/Canada winner vs Netherlands/Morocco winner)
  M91 = W76 vs W78   (Brasil/Japan winner vs Ivory Coast/Norway winner)
  M92 = W79 vs W80   (Mexico/Ecuador winner vs England/Congo winner)
  M93 = W83 vs W84   (Portugal/Croatia winner vs Spain/Austria winner)
  M94 = W81 vs W82   (USA/Bosnia winner vs Belgium/Senegal winner)
  M95 = W86 vs W88   (Argentina/Cape Verde winner vs Australia/Egypt winner)
  M96 = W85 vs W87   (Switzerland/Algeria winner vs Colombia/Ghana winner)

Usage:
  GOOGLE_APPLICATION_CREDENTIALS=/path/to/creds.json python patch_r16_registry.py
  -- or --
  FIREBASE_ADMIN_SDK_JSON='<json string>' python patch_r16_registry.py
"""
import os
import json
import re
import tempfile
import firebase_admin
from firebase_admin import credentials, db

FIREBASE_DB_URL = os.environ.get(
    "FIREBASE_DB_URL",
    "https://wc2026-fantasy-m47-default-rtdb.firebaseio.com"
)

# R16 bracket: R16_match_id → (r32_home_id, r32_away_id)
R16_BRACKET = {
    89: (74, 77),
    90: (73, 75),
    91: (76, 78),
    92: (79, 80),
    93: (83, 84),
    94: (81, 82),
    95: (86, 88),
    96: (85, 87),
}


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


def _fb_key(name):
    """Convert team name to Firebase-safe key (matches scorer logic)."""
    return re.sub(r"[^a-z0-9_]", "_", name.lower().strip())


def get_winner(result):
    """Extract matchWinner from a result dict; return None if not finalized."""
    if not result or result.get("live", False):
        return None
    w = result.get("matchWinner")
    if w:
        return w
    # Fall back: check breakdown for Win R32 label
    for td in result.get("teams", {}).values():
        for s in td.get("breakdown", []):
            if "Win R32" in s:
                home = result.get("home")
                away = result.get("away")
                tkey = list(result.get("teams", {}).keys())
                # td key matches fb_key of winner
                if home and _fb_key(home) in result.get("teams", {}):
                    pass
                return None
    return None


def main():
    print("Initializing Firebase…")
    init_firebase()

    results_ref = db.reference("results")
    all_results = fb_to_dict(results_ref.get())

    registry_ref = db.reference("results/_ko_registry")
    registry = fb_to_dict(registry_ref.get())

    print(f"\nCurrent _ko_registry has {len(registry)} entries.")

    updated = 0
    skipped = 0
    deferred = 0

    for r16_id, (r32_home_id, r32_away_id) in sorted(R16_BRACKET.items()):
        res_home = all_results.get(str(r32_home_id))
        res_away = all_results.get(str(r32_away_id))

        w_home = get_winner(res_home) if res_home else None
        w_away = get_winner(res_away) if res_away else None

        home_str = f"M{r32_home_id}→{w_home}" if w_home else f"M{r32_home_id}(pending)"
        away_str = f"M{r32_away_id}→{w_away}" if w_away else f"M{r32_away_id}(pending)"
        print(f"\n  R16 M{r16_id}: {home_str} vs {away_str}")

        if not w_home or not w_away:
            print(f"    Skipping — one or both R32 winners not yet determined.")
            deferred += 1
            continue

        # Build registry key (always home_vs_away canonical order)
        key = f"{_fb_key(w_home)}_vs_{_fb_key(w_away)}"
        key_rev = f"{_fb_key(w_away)}_vs_{_fb_key(w_home)}"

        # Check if already correctly set
        existing = registry.get(key) or registry.get(key_rev)
        if existing == r16_id:
            print(f"    Already correct: {key} → {r16_id}")
            skipped += 1
            continue

        if existing is not None and existing != r16_id:
            print(f"    FIXING: {key} was {existing}, setting to {r16_id}")
        else:
            print(f"    Setting: {key} → {r16_id}")

        # Remove any reversed key if present
        if key_rev in registry:
            del registry[key_rev]
        registry[key] = r16_id
        updated += 1

    if updated > 0:
        registry_ref.set(registry)
        print(f"\n_ko_registry updated. {updated} R16 entr(ies) set/fixed, {skipped} already correct, {deferred} deferred.")
    else:
        print(f"\nNo changes needed. {skipped} already correct, {deferred} deferred.")

    print("\nDone.")


if __name__ == "__main__":
    main()
