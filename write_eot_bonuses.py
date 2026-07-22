#!/usr/bin/env python3
"""Write EOT bonus data to Firebase /eotBonuses and verify /results count."""
import os, json, tempfile
import firebase_admin
from firebase_admin import credentials, db

FIREBASE_DB_URL = os.environ.get("FIREBASE_DB_URL",
                  "https://wc2026-fantasy-m47-default-rtdb.firebaseio.com")

def _fb_key(name):
    import re
    return re.sub(r"[.#$\[\]/]", "_", name)

def init_firebase():
    cred_json = os.environ.get("FIREBASE_ADMIN_SDK_JSON")
    if not cred_json:
        raise RuntimeError("FIREBASE_ADMIN_SDK_JSON not set")
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    tmp.write(cred_json); tmp.close()
    cred = credentials.Certificate(tmp.name)
    firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DB_URL})

# EOT awards — stats-only, no win/advance bonuses
# Each award is a dict with label and pts
EOT_BONUSES = {
    "Netherlands": [
        {"label": "PPG Best Points Per Game", "pts": 25},
        {"label": "1G Single-Game Best",      "pts": 15},
        {"label": "GSW Group Stage Warrior",  "pts": 15}
    ],
    "Spain": [
        {"label": "DEF Best Tournament Defense", "pts": 20}
    ],
    "Germany": [
        {"label": "OFF Best Tournament Offense", "pts": 20}
    ],
    "Argentina": [
        {"label": "CBK Comeback King",  "pts": 12},
        {"label": "DRM Most Dramatic",  "pts": 10}
    ],
    "Paraguay": [
        {"label": "GK Golden Glove", "pts": 15}
    ],
    "Austria": [
        {"label": "CLN Clinical Finisher", "pts": 15}
    ],
    "Norway": [
        {"label": "DIS Iron Discipline", "pts": 10}
    ]
}

def main():
    init_firebase()

    # Write eotBonuses
    eot_ref = db.reference("eotBonuses")
    payload = {_fb_key(team): awards for team, awards in EOT_BONUSES.items()}
    eot_ref.set(payload)
    print("✓ /eotBonuses written:")
    for team, awards in EOT_BONUSES.items():
        total = sum(a["pts"] for a in awards)
        labels = ", ".join(a["label"] for a in awards)
        print(f"  {team} (+{total}): {labels}")

    # Verify results count
    results = db.reference("results").get() or {}
    numeric_keys = [k for k in results.keys() if k.isdigit()]
    print(f"\n✓ /results numeric match keys: {len(numeric_keys)}")
    max_key = max(int(k) for k in numeric_keys) if numeric_keys else 0
    print(f"  Max match ID in Firebase: {max_key}")
    if max_key > 104:
        print(f"  WARNING: Found match ID {max_key} > 104!")
    else:
        print("  Clean — no rogue match IDs.")

    # Verify koRounds
    ko = db.reference("koRounds").get() or {}
    print(f"\n✓ /koRounds:")
    for rnd, val in ko.items():
        print(f"  {rnd}: {val}")

    print("\nDone.")

if __name__ == "__main__":
    main()
