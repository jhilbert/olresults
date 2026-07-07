#!/usr/bin/env python3
"""Parse SportSoftware (Stephan Krämer OE/OS/OE12) HTML result exports.

Reads the attachment index produced by anne_sync.py, downloads text/html
result files (cached under data/raw/anne/files/), parses them into the
normalized results shape and writes data/normalized/{eventId}-{n}.json.

SportSoftware HTML uses HTML4 with unclosed <td>/<tr> tags. Structure per
category:
    <a id="D14"></a>
    <table><tr><td id=c00>D14  (1)<td id=c01>2,3 km  130 Hm<td id=c02>8 P ...
    <table><thead><tr><th>Pl</th><th>Name</th>...</thead>...
    <table><tbody><tr><td>1<td><nobr>Name</nobr><td>...
"""
import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "anne"
FILES = RAW / "files"
OUT = ROOT / "data" / "normalized"

HEADERS = {"User-Agent": "olresults-sync/0.1 (+https://github.com/josefhilbert/olresults)"}

# German status strings SportSoftware prints in the time column
STATUS_MAP = {
    "aufg": "dnf", "aufgegeben": "dnf",
    "fehlst": "mp", "fehlstempel": "mp",
    "disq": "dsq", "disqualifiziert": "dsq",
    "n. angetr.": "dns", "n.angetr.": "dns", "nicht angetreten": "dns",
    "ohne wertung": "nc", "außer konkurrenz": "nc", "wertungsfrei": "nc",
    "dnf": "dnf", "dns": "dns", "dsq": "dsq", "mp": "mp",
}


class TableExtractor(HTMLParser):
    """Flatten the document into a list of tables, each a list of row-cell-lists."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self._rows = None
        self._cells = None
        self._buf = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._flush_row()
            self._rows = []
            self.tables.append(self._rows)
        elif tag == "tr" and self._rows is not None:
            self._flush_row()
            self._cells = []
        elif tag in ("td", "th") and self._cells is not None:
            self._flush_cell()
            self._buf = []

    def handle_endtag(self, tag):
        if tag == "table":
            self._flush_row()
            self._rows = None
        elif tag == "tr":
            self._flush_row()
        elif tag in ("td", "th"):
            self._flush_cell()

    def handle_data(self, data):
        if self._buf is not None:
            self._buf.append(data)

    def _flush_cell(self):
        if self._buf is not None and self._cells is not None:
            text = re.sub(r"\s+", " ", "".join(self._buf)).strip().strip("\xa0").strip()
            self._cells.append(text)
        self._buf = None

    def _flush_row(self):
        self._flush_cell()
        if self._cells is not None and self._rows is not None:
            self._rows.append(self._cells)
        self._cells = None


CAT_RE = re.compile(r"^(?P<name>.+?)\s+\((?P<starters>\d+)\)\s*$")
JUNK_NAME_RE = re.compile(r"^[\d\s:.,()/-]*$")
JUNK_NAMES = {"empty", "vacant", "leer", "frei"}
COURSE_RE = re.compile(r"(?:(?P<km>[\d.,]+)\s*km)?\s*(?:(?P<climb>\d+)\s*Hm)?")
TIME_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})$")


def parse_time(text):
    """'26:21' -> seconds; '1:02:33' -> seconds; else None."""
    m = TIME_RE.match(text.strip())
    if not m:
        return None
    h, mi, s = m.groups()
    return (int(h or 0)) * 3600 + int(mi) * 60 + int(s)


def parse_status(text):
    t = text.strip().lower().rstrip(".")
    for key, val in STATUS_MAP.items():
        if key in t:
            return val
    return None


def parse_document(html_text):
    ex = TableExtractor()
    ex.feed(html_text)

    categories = []
    current = None
    columns = None
    for table in ex.tables:
        for row in table:
            if not row or all(c in ("", "&nbsp") for c in row):
                continue
            first = row[0]
            m = CAT_RE.match(first)
            if m and len(row) >= 2:
                # category header: "D14  (1)" | "2,3 km  130 Hm" | "8 P"
                current = {
                    "name": m.group("name").strip(),
                    "declaredStarters": int(m.group("starters")),
                    "results": [],
                }
                cm = COURSE_RE.search(row[1]) if len(row) > 1 else None
                if cm and cm.group("km"):
                    current["courseLengthM"] = int(float(cm.group("km").replace(",", ".")) * 1000)
                if cm and cm.group("climb"):
                    current["courseClimbM"] = int(cm.group("climb"))
                for cell in row[2:]:
                    pm = re.match(r"(\d+)\s*P\b", cell)
                    if pm:
                        current["courseControls"] = int(pm.group(1))
                categories.append(current)
                columns = None
                continue
            if first == "Pl" or (current and "Name" in row):
                columns = row
                continue
            if current is None or columns is None:
                continue
            # data row: align cells to columns
            rec = dict(zip([c or f"col{i}" for i, c in enumerate(columns)], row))
            name = rec.get("Name", "").strip()
            if not name or JUNK_NAME_RE.match(name) or name.lower() in JUNK_NAMES:
                continue
            time_text = (rec.get("Zeit") or rec.get("Gesamt") or "").strip()
            rank_ok = rec.get("Pl", "").strip().isdigit()
            # club/spacer rows in split-time lists carry neither rank nor time
            if not rank_ok and not time_text:
                continue
            result = {
                "name": name,
                "club": (rec.get("Verein") or rec.get("Verein/Schule") or "").strip(),
                "timeText": time_text,
            }
            rank_text = rec.get("Pl", "").strip()
            if rank_text.isdigit():
                result["rank"] = int(rank_text)
            seconds = parse_time(time_text)
            if seconds is not None:
                result["timeS"] = seconds
                result["status"] = "ok"
            else:
                result["status"] = parse_status(time_text) or "unknown"
            yob = (rec.get("Jg") or "").strip()
            if yob.isdigit():
                y = int(yob)
                result["yearOfBirth"] = y + (2000 if y <= 26 else 1900) if y < 100 else y
            if rec.get("Pkt"):
                result["scoreText"] = rec["Pkt"].strip()
            current["results"].append(result)

    return [c for c in categories if c["results"]]


def detect_list_type(file_name, html_text):
    """Relay lists need team/leg modelling (not yet supported); cumulative
    multi-day standings should not count as a single race."""
    head = html_text[:4000]
    if re.search(r"staffel|relay", file_name, re.I) or re.search(r"Staffel", head):
        return "relay"
    if re.search(r"gesamt", file_name, re.I) or "Gesamtwertung" in head:
        return "overall"
    return "race"


def fetch(url, dest):
    if dest.exists():
        return dest.read_bytes()
    safe_url = urllib.parse.quote(url, safe=":/?&=%")
    data = urllib.request.urlopen(
        urllib.request.Request(safe_url, headers=HEADERS), timeout=30).read()
    dest.write_bytes(data)
    time.sleep(0.15)
    return data


def decode(data):
    head = data[:600].decode("ascii", "ignore").lower()
    if "charset=utf-8" in head:
        return data.decode("utf-8", "replace")
    return data.decode("windows-1252", "replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="only process N files (0 = all)")
    args = ap.parse_args()

    FILES.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    attachments = json.loads((RAW / "attachments.json").read_text())

    jobs = []
    for eid, files in attachments.items():
        for n, f in enumerate(files or []):
            if f["mimeType"] == "text/html":
                jobs.append((int(eid), n, f))
    if args.limit:
        jobs = jobs[: args.limit]
    print(f"html files to parse: {len(jobs)}")

    ok = empty = failed = 0
    for eid, n, f in jobs:
        out_path = OUT / f"{eid}-{n}.json"
        try:
            data = fetch(f["url"], FILES / f"{eid}-{n}.html")
            text = decode(data)
            cats = parse_document(text)
            if not cats:
                empty += 1
                continue
            out_path.write_text(json.dumps({
                "eventId": eid,
                "source": "sportsoftware-html",
                "sourceUrl": f["url"],
                "fileName": f["fileName"],
                "listType": detect_list_type(f["fileName"], text),
                "categories": cats,
            }, ensure_ascii=False))
            ok += 1
        except Exception as e:
            failed += 1
            print(f"  FAIL {eid}-{n} {f['fileName']}: {e}", file=sys.stderr)
    print(f"parsed: {ok}, empty: {empty}, failed: {failed}")


if __name__ == "__main__":
    main()
