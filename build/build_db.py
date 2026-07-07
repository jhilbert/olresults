#!/usr/bin/env python3
"""Compile raw ANNE snapshots + normalized legacy results into site/data/results.db.

Person identity: ANNE userId is authoritative (positive ids). Legacy results
without a userId are matched to existing persons by (normalized name, year of
birth) and otherwise get synthetic negative ids. Derived statistics (starters,
classified count, winner time) are computed in the category_stats view, never
stored.
"""
import gzip
import json
import re
import sqlite3
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "anne"
NORM = ROOT / "data" / "normalized"
DB_PATH = ROOT / "site" / "data" / "results.db"

SCHEMA = """
CREATE TABLE event (
    id INTEGER PRIMARY KEY,
    slug TEXT, title TEXT, short_title TEXT,
    date_from TEXT, date_to TEXT,
    location TEXT, country TEXT NOT NULL DEFAULT 'AUT',
    coordinates TEXT,
    competition_type TEXT, sport_type TEXT, event_type TEXT,
    url TEXT
);
CREATE TABLE stage (
    id INTEGER PRIMARY KEY,
    event_id INTEGER NOT NULL REFERENCES event(id),
    number INTEGER NOT NULL DEFAULT 1,
    title TEXT, date TEXT, location TEXT
);
CREATE TABLE person (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL,
    year_of_birth INTEGER, nationality TEXT, iof_id TEXT
);
CREATE TABLE result (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage_id INTEGER NOT NULL REFERENCES stage(id),
    person_id INTEGER NOT NULL REFERENCES person(id),
    category TEXT NOT NULL,
    category_full TEXT,
    club TEXT,
    rank INTEGER,
    status TEXT NOT NULL,            -- ok|dnf|dsq|mp|dns|nc|unknown
    time_s INTEGER,
    time_behind_s INTEGER,
    out_of_competition INTEGER NOT NULL DEFAULT 0,
    course_length_m INTEGER, course_climb_m INTEGER, course_controls INTEGER,
    source TEXT NOT NULL             -- anne-api|sportsoftware-html|...
);
CREATE INDEX idx_result_person ON result(person_id);
CREATE INDEX idx_result_stage_cat ON result(stage_id, category);
CREATE INDEX idx_person_name ON person(name_key);
CREATE VIEW category_stats AS
SELECT stage_id, category,
       COUNT(*)                                       AS starters,
       SUM(status = 'ok')                             AS classified,
       MIN(CASE WHEN rank = 1 THEN time_s END)        AS winner_time_s
FROM result
WHERE status != 'dns'
GROUP BY stage_id, category;
"""

ANNE_STATUS = {
    "classified": "ok",
    "notClassified": "nc",
    "didNotFinish": "dnf",
    "disqualified": "dsq",
    "missingPunch": "mp",
    "didNotStart": "dns",
}


def name_key(name):
    """Normalized identity key: lowercase, accent-stripped, sorted tokens."""
    n = unicodedata.normalize("NFKD", name)
    n = "".join(c for c in n if not unicodedata.combining(c)).lower()
    return " ".join(sorted(re.findall(r"[a-zäöüß]+", n)))


class PersonRegistry:
    def __init__(self):
        self.by_id = {}
        self.by_key = {}     # (name_key, yob) -> pid
        self.by_name = {}    # name_key -> [pid, ...]
        self.next_synthetic = -1

    def _register(self, pid, name, yob, nationality=None, iof_id=None):
        nk = name_key(name)
        self.by_id[pid] = (name, nk, yob, nationality, iof_id)
        self.by_key.setdefault((nk, yob), pid)
        self.by_name.setdefault(nk, []).append(pid)
        return pid

    def from_anne(self, user_id, name, yob, nationality, iof_id):
        if user_id in self.by_id:
            return user_id
        return self._register(user_id, name, yob, nationality, iof_id)

    def from_legacy(self, name, yob):
        nk = name_key(name)
        if (nk, yob) in self.by_key:
            return self.by_key[(nk, yob)]
        # legacy lists often omit the year of birth (or use two digits that
        # were expanded differently): a unique name match is good enough
        candidates = self.by_name.get(nk, [])
        if len(candidates) == 1:
            return candidates[0]
        return self._register(self.next_synthetic_id(), name, yob)

    def next_synthetic_id(self):
        pid = self.next_synthetic
        self.next_synthetic -= 1
        return pid


def load_events(cur):
    events = {e["id"]: e for e in json.loads((RAW / "events.json").read_text())}
    for e in events.values():
        cur.execute(
            "INSERT INTO event VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (e["id"], e.get("slug"), e.get("shortTitle"), e.get("shortTitle"),
             (e.get("dateFrom") or "")[:10] or None,
             (e.get("dateTo") or "")[:10] or None,
             e.get("location"), "AUT", e.get("coordinates"),
             e.get("competitionType"), e.get("sportType"), e.get("eventType"),
             e.get("url")))
    return events


def default_stage(cur, event, stage_ids):
    """Stage id for single-stage events: synthetic id = event id + offset."""
    sid = 10_000_000 + event["id"]
    if sid not in stage_ids:
        cur.execute("INSERT INTO stage VALUES (?,?,?,?,?,?)",
                    (sid, event["id"], 1, None,
                     (event.get("dateFrom") or "")[:10] or None,
                     event.get("location")))
        stage_ids.add(sid)
    return sid


def load_anne_results(cur, events, persons, stage_ids):
    n = 0
    for path in sorted((RAW / "results").glob("*.json")):
        eid = int(path.stem)
        event = events.get(eid)
        if not event:
            continue
        stages_path = RAW / "stages" / f"{eid}.json"
        if stages_path.exists():
            for s in json.loads(stages_path.read_text()):
                if s["id"] not in stage_ids:
                    cur.execute("INSERT INTO stage VALUES (?,?,?,?,?,?)",
                                (s["id"], eid, s.get("number", 1), s.get("title"),
                                 s.get("dateFrom"), s.get("location")))
                    stage_ids.add(s["id"])
        rows = json.loads(path.read_text())
        # the API can return live and official lists side by side:
        # keep only the most authoritative type per stage+category
        priority = {"official": 0, "unofficial": 1, "live": 2}
        best = {}
        for r in rows:
            key = (r.get("eventStageId"), r.get("categoryShortTitle"))
            p = priority.get(r.get("resultType"), 3)
            best[key] = min(best.get(key, 3), p)
        rows = [r for r in rows
                if priority.get(r.get("resultType"), 3)
                == best[(r.get("eventStageId"), r.get("categoryShortTitle"))]]
        for r in rows:
            name = f"{r.get('firstName') or ''} {r.get('lastName') or ''}".strip()
            # team rows (relays) need team/leg modelling — skipped for now;
            # some old imports carry bib/SI numbers or 'empty' placeholders
            if not name or re.match(r"^[\d\s:.,()/-]*$", name) \
                    or "empty" in name.lower():
                continue
            uid = r.get("userId")
            if uid:
                pid = persons.from_anne(uid, name, r.get("yearOfBirth"),
                                        r.get("nationality"), r.get("iofId"))
            else:
                pid = persons.from_legacy(name, r.get("yearOfBirth"))
            sid = r.get("eventStageId") or default_stage(cur, event, stage_ids)
            course = r.get("course") or {}
            cur.execute(
                "INSERT INTO result (stage_id, person_id, category, category_full,"
                " club, rank, status, time_s, time_behind_s, out_of_competition,"
                " course_length_m, course_climb_m, course_controls, source)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, pid, r.get("categoryShortTitle") or r.get("categoryTitle"),
                 r.get("categoryTitle"), r.get("clubName"), r.get("rank"),
                 ANNE_STATUS.get(r.get("classification"), "unknown"),
                 r.get("time"), r.get("timeBehind"),
                 1 if r.get("outOfCompetition") else 0,
                 course.get("length"), course.get("climb"), course.get("controlCount"),
                 "anne-api"))
            n += 1
    return n


def load_legacy_results(cur, events, persons, stage_ids, anne_event_ids):
    n = 0
    canonical = re.compile(r"^\d+-\d+\.json$")
    docs = [json.loads(p.read_text())
            for p in sorted(NORM.glob("*.json")) if canonical.match(p.name)]
    # plain result lists before split-time lists, so duplicates resolve
    # in favour of the cleaner source
    docs.sort(key=lambda d: (d["eventId"], "split" in d["fileName"].lower()))
    seen = set()
    for doc in docs:
        eid = doc["eventId"]
        event = events.get(eid)
        if not event or doc.get("listType") != "race":
            continue
        if eid in anne_event_ids:
            continue  # structured API data wins over legacy files
        sid = default_stage(cur, event, stage_ids)
        for cat in doc["categories"]:
            for r in cat["results"]:
                if r.get("status") == "dns":
                    continue
                key = (sid, cat["name"], name_key(r["name"]))
                if key in seen:
                    continue
                seen.add(key)
                pid = persons.from_legacy(r["name"], r.get("yearOfBirth"))
                cur.execute(
                    "INSERT INTO result (stage_id, person_id, category, category_full,"
                    " club, rank, status, time_s, time_behind_s, out_of_competition,"
                    " course_length_m, course_climb_m, course_controls, source)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (sid, pid, cat["name"], cat["name"], r.get("club"),
                     r.get("rank"), r.get("status", "unknown"), r.get("timeS"),
                     None, 0,
                     cat.get("courseLengthM"), cat.get("courseClimbM"),
                     cat.get("courseControls"), doc["source"]))
                n += 1
    return n


def main():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    DB_PATH.unlink(missing_ok=True)
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.executescript(SCHEMA)

    events = load_events(cur)
    persons = PersonRegistry()
    stage_ids = set()

    anne_event_ids = {int(p.stem) for p in (RAW / "results").glob("*.json")}
    n_api = load_anne_results(cur, events, persons, stage_ids)
    n_legacy = load_legacy_results(cur, events, persons, stage_ids, anne_event_ids)

    for pid, (name, key, yob, nat, iof) in persons.by_id.items():
        cur.execute("INSERT INTO person VALUES (?,?,?,?,?,?)",
                    (pid, name, key, yob, nat, iof))

    # compute time_behind for legacy rows from winner time per category
    cur.execute("""
        UPDATE result SET time_behind_s = time_s - (
            SELECT winner_time_s FROM category_stats cs
            WHERE cs.stage_id = result.stage_id AND cs.category = result.category)
        WHERE time_behind_s IS NULL AND time_s IS NOT NULL AND status = 'ok'
    """)

    con.commit()
    for table in ("event", "stage", "person", "result"):
        print(table, cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    print(f"api results: {n_api}, legacy results: {n_legacy}")
    cur.execute("VACUUM")
    con.close()
    gz_path = DB_PATH.with_suffix(".db.gz")
    gz_path.write_bytes(gzip.compress(DB_PATH.read_bytes(), 9))
    print(f"wrote {DB_PATH} ({DB_PATH.stat().st_size / 1e6:.1f} MB, "
          f"gz {gz_path.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
