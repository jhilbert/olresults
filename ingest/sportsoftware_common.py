"""Shared parsing helpers for Stephan Krämer SportSoftware (OE/OE2003/OE12/
OEScore) result exports, used by both the HTML and PDF adapters.
"""
import re

CAT_RE = re.compile(r"^(?P<name>.+?)\s+\((?P<starters>\d+)\)\s*$")
# same, but for formats (PDF, fixed-width text) where course info trails the
# category on the same line: "H21-Wien (21) 7.8 km 280 Hm 27 P"
CAT_LINE_RE = re.compile(r"^(?P<name>.+?)\s+\((?P<starters>\d+)\)\s*(?P<rest>.*)$")
COURSE_RE = re.compile(r"(?:(?P<km>[\d.,]+)\s*km)?\s*(?:(?P<climb>\d+)\s*Hm)?")
CONTROLS_RE = re.compile(r"(\d+)\s*P\b")
TIME_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})$")
JUNK_NAME_RE = re.compile(r"^[\d\s:.,()/-]*$")
JUNK_NAMES = {"empty", "vacant", "leer", "frei"}

# German status strings SportSoftware prints in the time column
STATUS_MAP = {
    "aufg": "dnf", "aufgegeben": "dnf",
    "fehlst": "mp", "fehlstempel": "mp",
    "disq": "dsq", "disqualifiziert": "dsq",
    "n. angetr.": "dns", "n.angetr.": "dns", "nicht angetreten": "dns",
    "n ang": "dns",
    "ohne wertung": "nc", "außer konkurrenz": "nc", "wertungsfrei": "nc",
    "dnf": "dnf", "dns": "dns", "dsq": "dsq", "mp": "mp",
}


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


def detect_list_type(file_name, doc_text):
    """Relay lists need team/leg modelling (not yet supported); cumulative
    multi-day standings should not count as a single race."""
    head = doc_text[:4000]
    if re.search(r"staffel|relay", file_name, re.I) or re.search(r"Staffel", head):
        return "relay"
    if re.search(r"gesamt", file_name, re.I) or "Gesamtwertung" in head:
        return "overall"
    return "race"


def parse_course_info(text):
    """'2,3 km  130 Hm  8 P' -> {courseLengthM, courseClimbM, courseControls}."""
    out = {}
    m = COURSE_RE.search(text)
    if m and m.group("km"):
        out["courseLengthM"] = int(float(m.group("km").replace(",", ".")) * 1000)
    if m and m.group("climb"):
        out["courseClimbM"] = int(m.group("climb"))
    cm = CONTROLS_RE.search(text)
    if cm:
        out["courseControls"] = int(cm.group(1))
    return out


def is_junk_name(name):
    return not name or bool(JUNK_NAME_RE.match(name)) or name.lower() in JUNK_NAMES
