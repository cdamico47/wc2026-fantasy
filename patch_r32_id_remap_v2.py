#!/usr/bin/env python3
"""
V2 of the R32 ID remap patch — correctly handles cyclic remaps.

The V1 script skipped destination slots that already had data. Because the
remap forms two 3-cycles (74→76→75→74 and 77→78→79→77), every destination
slot was occupied and nothing moved. This version loads all 6 source values
first, then writes them to the correct destinations in one pass.

Remap (current Firebase key → correct app match ID):
  74 → 76  (Brasil vs Japan:            Houston, was stored under Boston slot)
  75 → 74  (Germany vs Paraguay:        Boston,  was stored under Monterrey slot)
  76 → 75  (Netherlands vs Morocco:     Monterrey, was stored under Houston slot)
  77 → 78  (Ivory Coast vs Norway:      Dallas,  was stored under NY slot)
  78 → 79  (Mexico vs Ecuador:          Mexico City, was stored under Dallas slot)
  79 → 77  (France vs Sweden:           New York, was stored under Mexico City slot)

Usage:
  GOOGLE_APPLICATION_CREDENTIALS=/path/to/creds.json python patch_r32_id_remap_v2.py
  -- or --
  FIREBASE_ADMIN_SDK_JSON='<json string>' python patch_r32_id_remap_v2.py
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

# Maps current wrong Firebase key → correct app match ID
REMAP = {74: 76, 75: 74, 76: 75, 77: 78, 78: 79, 79: 77}


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

    # ── Step 1: Load ALL source values before any writes ─────────────────────
    print("\nLoading source data…")
    sources = {}
    for wrong_id in REMAP:
        data = all_results.get(str(wrong_id))
        if data:
            home = data.get("home", "?")
            away = data.get("away", "?")
            print(f"  Loaded M{wrong_id}: {home} vs {away}")
            sources[wrong_id] = data
        else:
            print(f"  M{wrong_id}: not in Firebase — will skip")

    if not sources:
        print("Nothing to remap.")
        return

    # ── Step 2: Write each to its correct slot ────────────────────────────────
    print("\nWriting to correct slots…")
    for wrong_id, correct_id in REMAP.items():
        if wrong_id not in sources:
            continue
        data = sources[wrong_id]
        home = data.get("home", "?")
        away = data.get("away", "?")
        print(f"  M{wrong_id} ({home} vs {away}) → M{correct_id}")
        results_ref.child(str(correct_id)).set(data)

    print(f"\n  {len(sources)} match(es) written to correct slots.")

    # ── Step 3: Update _ko_registry ───────────────────────────────────────────
    print("\nUpdating _ko_registry…")
    registry_ref = db.reference("results/_ko_registry")
    registry = fb_to_dict(registry_ref.get())

    if not registry:
        print("  _ko_registry is empty — nothing to update.")
    else:
        updated = {}
        changes = 0
        for match_key, assigned_id in registry.items():
            if isinstance(assigned_id, int) and assigned_id in REMAP:
                correct_id = REMAP[assigned_id]
                print(f"  Registry: {match_key}: {assigned_id} → {correct_id}")
                updated[match_key] = correct_id
                changes += 1
            else:
                updated[match_key] = assigned_id
        registry_ref.set(updated)
        print(f"  Registry updated ({changes} entr(ies) changed, {len(updated)} total).")

    print("\nDone.")
    print("\nNext step: trigger the main scorer workflow to re-score affected matches.")


if __name__ == "__main__":
    main()
