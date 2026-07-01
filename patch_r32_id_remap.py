#!/usr/bin/env python3
"""
One-time migration: correct R32 match IDs in Firebase.

The scorer assigned R32 match IDs 74–79 in chronological play order, but the
HTML app's SCHEDULE defines those IDs by bracket slot (not by time). This patch
moves each result to its correct ID.

Remap (wrong Firebase key → correct app match ID):
  74 → 76  (Brazil vs Japan:      Houston, was stored under Boston slot)
  75 → 74  (Germany vs Paraguay:  Boston,  was stored under third-place slot)
  76 → 75  (Netherlands vs Morocco: Monterrey, was stored under Houston slot)
  77 → 78  (Ivory Coast vs Norway: Dallas, was stored under NY slot)
  78 → 79  (Mexico vs Ecuador:    Mexico City, was stored under Dallas slot)
  79 → 77  (France vs Sweden:     New York, was stored under Mexico City slot)

Also updates /results/_ko_registry so future scorer runs write to correct IDs.

Usage:
  GOOGLE_APPLICATION_CREDENTIALS=/path/to/creds.json python patch_r32_id_remap.py
  -- or --
  FIREBASE_ADMIN_SDK_JSON='<json string>' python patch_r32_id_remap.py
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

# Maps wrong Firebase key → correct app match ID
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

    # ── Step 1: migrate result data ───────────────────────────────────────
    print("\nMigrating result data…")
    moved = []
    skipped = []

    for wrong_id, correct_id in REMAP.items():
        wrong_key   = str(wrong_id)
        correct_key = str(correct_id)

        src = all_results.get(wrong_key)
        dst = all_results.get(correct_key)

        if src is None:
            skipped.append(f"  M{wrong_id}: not in Firebase — skipping")
            continue

        if dst is not None and not dst.get("live", True):
            # Correct key already has finalized data — don't overwrite
            skipped.append(
                f"  M{wrong_id}→M{correct_id}: M{correct_id} already finalized "
                f"({dst.get('home')} vs {dst.get('away')}) — skipping"
            )
            continue

        home = src.get("home", "?")
        away = src.get("away", "?")
        print(f"  M{wrong_id} ({home} vs {away}) → M{correct_id}")

        # Write to correct key
        results_ref.child(correct_key).set(src)

        # Delete old wrong key
        results_ref.child(wrong_key).delete()
        moved.append((wrong_id, correct_id, home, away))

    for msg in skipped:
        print(msg)

    print(f"\n  {len(moved)} result(s) remapped, {len(skipped)} skipped.")

    # ── Step 2: update _ko_registry ───────────────────────────────────────
    print("\nUpdating _ko_registry…")
    registry_ref = db.reference("results/_ko_registry")
    registry = fb_to_dict(registry_ref.get())

    if not registry:
        print("  _ko_registry is empty — nothing to update.")
    else:
        updated = {}
        for match_key, assigned_id in registry.items():
            if isinstance(assigned_id, int) and assigned_id in REMAP:
                correct_id = REMAP[assigned_id]
                print(f"  Registry: {match_key}: {assigned_id} → {correct_id}")
                updated[match_key] = correct_id
            else:
                updated[match_key] = assigned_id

        registry_ref.set(updated)
        print(f"  Registry updated ({len(updated)} entries).")

    # ── Done ──────────────────────────────────────────────────────────────
    print("\nDone.")
    print("\nNext steps:")
    print("  1. Commit + push this script to GitHub")
    print("  2. Remove _normalizeR32Results workaround from wc2026_fantasy_app.html")
    print("     (set _R32_FBKEY_REMAP = {} or delete the constant + function)")
    print("  3. Trigger scorer workflow manually to refresh /koRounds")


if __name__ == "__main__":
    main()
