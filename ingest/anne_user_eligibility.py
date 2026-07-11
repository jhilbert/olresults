#!/usr/bin/env python3
"""Fetch ANNE's authoritative championshipEligibility flag for every runner
on record with a non-Austrian nationality, via the authenticated
GET /v1/user/:id endpoint (requires ANNE_API_KEY - a personal API key from
an ANNE account with clubManager+ role). Caches to
data/raw/anne/user_eligibility.json, keyed by ANNE userId.

Why this exists: person.nationality alone is not a reliable ÖM/ÖSTM
eligibility signal - it reflects passport/birth nationality, not
competition eligibility, and several long-tenured Austrian club members are
on record with a foreign nationality yet hold an explicit
championshipEligibility override (confirmed by hand: Vera Arbter/CHE,
Marina Skern/RUS, Frederic Genevois/FRA, all real ÖM medalists per their
club's own records). See build_db.py's use of this cache.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "anne"
OUT_PATH = RAW / "user_eligibility.json"
BASE = "https://anne-api.oefol.at/v1"


def scan_foreign_user_ids():
    """Every distinct (userId -> nationality) seen in raw ANNE result
    snapshots (individual rows and relay teamMembers) where nationality is
    set and isn't Austrian - the only people championshipEligibility could
    possibly matter for. Everyone else is either Austrian already or has no
    ANNE account to look up at all (a synthetic person id), in which case
    this API has nothing to tell us."""
    ids = {}
    for path in (RAW / "results").glob("*.json"):
        try:
            rows = json.loads(path.read_text())
        except Exception:
            continue
        if not isinstance(rows, list):
            continue
        for r in rows:
            for entry in ([r] + (r.get("teamMembers") or [])):
                uid, nat = entry.get("userId"), entry.get("nationality")
                if uid and nat and nat != "AUT":
                    ids[uid] = nat
    return ids


def fetch_eligibility(user_id, api_key):
    result = subprocess.run(
        ["curl", "-s", "-H", f"X-API-Key: {api_key}", "-H", "Accept: application/json",
         f"{BASE}/user/{user_id}"],
        capture_output=True, text=True, timeout=20)
    try:
        d = json.loads(result.stdout)
    except Exception:
        return "error"
    if "championshipEligibility" not in d:
        return "error"
    return d.get("championshipEligibility")


def main():
    force = "--force" in sys.argv
    api_key = os.environ.get("ANNE_API_KEY")
    if not api_key:
        print("ANNE_API_KEY not set - skipping (existing cache, if any, is left as-is)")
        return

    cache = json.loads(OUT_PATH.read_text()) if OUT_PATH.exists() else {}
    candidates = scan_foreign_user_ids()
    # "error" entries (a transient API/network failure) are always retried;
    # a real True/null result is trusted and only re-checked with --force
    todo = [uid for uid in candidates
            if force or str(uid) not in cache or cache[str(uid)] == "error"]
    print(f"foreign-nationality user ids on record: {len(candidates)}, to fetch: {len(todo)}")

    for uid in todo:
        cache[str(uid)] = fetch_eligibility(uid, api_key)
        time.sleep(0.1)

    OUT_PATH.write_text(json.dumps(cache, indent=2, sort_keys=True))
    n_true = sum(1 for v in cache.values() if v is True)
    print(f"wrote {OUT_PATH} ({len(cache)} entries, {n_true} with an eligibility override)")


if __name__ == "__main__":
    main()
