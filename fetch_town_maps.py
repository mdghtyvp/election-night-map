#!/usr/bin/env python3
"""
Fetch the Vermont SOS town-level results feed (election-result-data.xml) and
write one town-by-town CSV per office listed in town_maps.json, for a
Datawrapper choropleth map using "linked external data".

Key points:
  * The feed splits some towns into several <Town> entries (one per house
    district -- Burlington appears 6 times). Votes are summed per town Id.
  * Margin = (leader votes - runner-up votes) / total town votes * 100.
    Sign comes from the LEADER's party via "party_map" in town_maps.json:
        D leader -> positive,  R leader -> negative.
    If someone outside the party_map leads (independent etc.), Margin is
    blank (grey on the map) and LeaderParty says who. Exact tie -> 0.
    No votes yet -> Margin blank.
  * Same safety rules as fetch_races.py: if the feed is unreachable or the
    office isn't found, the existing CSV is left untouched; a CSV is only
    rewritten when its content changes.

Usage:
  python fetch_town_maps.py
  python fetch_town_maps.py --feed-file election-result-data.xml   # local test
"""

import argparse
import csv
import io
import json
import sys
from collections import OrderedDict
from pathlib import Path

import urllib.request
import xml.etree.ElementTree as ET


# ---- helpers (self-contained copy so this repo needs only one script) ----

SUFFIXES = {"JR": "Jr.", "SR": "Sr.", "II": "II", "III": "III", "IV": "IV"}


def norm(s):
    return " ".join((s or "").upper().split())

def load_xml_from_url(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (results-bot)"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return ET.fromstring(resp.read())

def load_xml_from_file(path):
    return ET.parse(path).getroot()

def _title_token(tok):
    """Title-case one space-separated token (handles O'BRIEN, MCRAE, hyphens)."""
    if "-" in tok:
        return "-".join(_title_token(t) for t in tok.split("-"))
    bare = tok.strip(".,").upper()
    if bare in SUFFIXES:
        return SUFFIXES[bare] + ("," if tok.endswith(",") else "")
    if len(bare) == 1:  # middle initial
        return tok.upper()
    if "'" in tok:
        return "'".join(p[:1].upper() + p[1:].lower() for p in tok.split("'"))
    if bare.startswith("MC") and len(bare) > 2:
        return "Mc" + tok[2:3].upper() + tok[3:].lower()
    return tok[:1].upper() + tok[1:].lower()

def title_case(name):
    return " ".join(_title_token(t) for t in name.split())

def party_initials(party):
    """'DEM/PROG' -> 'D/P', 'REPUBLICAN' -> 'R'. Empty if no party."""
    parts = [p.strip() for p in (party or "").split("/") if p.strip()]
    return "/".join(p[0].upper() for p in parts)

def display_name(name, party, overrides=None):
    key = " ".join((name or "").upper().split())
    base = (overrides or {}).get(key) or title_case(name)
    initials = party_initials(party)
    return f"{base} ({initials})" if initials else base

def to_int(text):
    try:
        return int((text or "0").strip().replace(",", ""))
    except ValueError:
        return 0


# ---- end helpers ----


def collect(root, office_name):
    """Return (candidates, towns).

    candidates: OrderedDict cand_id -> {"name","party"}  (feed order)
    towns:      OrderedDict town_id -> {"name","county","votes":{cand_id:int}}
    """
    want = norm(office_name)
    candidates = OrderedDict()
    towns = OrderedDict()
    found = False
    for t in root.iter("Town"):
        tid = (t.findtext("Id") or "").strip()
        for o in t.findall("Office"):
            if norm(o.findtext("OfficeName")) != want:
                continue
            found = True
            rec = towns.setdefault(
                tid,
                {
                    "name": " ".join((t.findtext("TownName") or "").split()),
                    "county": " ".join((t.findtext("County") or "").split()),
                    "votes": {},
                },
            )
            for c in o.findall("Candidate"):
                cid = (c.findtext("CandidateId") or "").strip()
                candidates.setdefault(
                    cid,
                    {
                        "name": " ".join((c.findtext("Name") or "").split()),
                        "party": " ".join((c.findtext("PartyName") or "").split()),
                    },
                )
                rec["votes"][cid] = rec["votes"].get(cid, 0) + to_int(c.findtext("Votes"))
    if not found:
        raise ValueError(f"office '{office_name}' not found in feed")
    return candidates, towns


def build(candidates, towns, party_map, overrides):
    labels = {cid: display_name(c["name"], c["party"], overrides) for cid, c in candidates.items()}
    # Stable column order: alphabetical by candidate name (not by who's winning).
    order = sorted(candidates, key=lambda cid: candidates[cid]["name"])

    fields = ["Town", "County", "Id", "Margin", "Leader", "LeaderParty", "TotalVotes"]
    for cid in order:
        fields += [f"{labels[cid]} votes", f"{labels[cid]} %"]

    rows = []
    for tid, t in sorted(towns.items(), key=lambda kv: (kv[1]["name"], kv[0])):
        votes = {cid: t["votes"].get(cid, 0) for cid in order}
        total = sum(votes.values())
        row = {
            "Town": title_case(t["name"]),
            "County": title_case(t["county"]),
            "Id": tid,
            "TotalVotes": total,
            "Margin": "",
            "Leader": "",
            "LeaderParty": "",
        }
        for cid in order:
            row[f"{labels[cid]} votes"] = votes[cid]
            row[f"{labels[cid]} %"] = round(votes[cid] / total * 100, 1) if total else ""
        if total:
            ranked = sorted(order, key=lambda cid: (-votes[cid], candidates[cid]["name"]))
            first, second = ranked[0], ranked[1] if len(ranked) > 1 else None
            lead = votes[first]
            gap = lead - (votes[second] if second else 0)
            tied = second is not None and gap == 0
            if tied:
                row["Leader"] = "Tie"
                row["Margin"] = 0.0
            else:
                row["Leader"] = labels[first]
                code = party_map.get(candidates[first]["party"], "")
                row["LeaderParty"] = code or candidates[first]["party"].title()
                pts = round(gap / total * 100, 1)
                if code == "D":
                    row["Margin"] = pts
                elif code == "R":
                    row["Margin"] = -pts
                # else: leave blank so the map shows no-data grey
        rows.append(row)
    return fields, rows, labels, order


def render(fields, rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def write_if_changed(path, text):
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return "unchanged"
    path.write_text(text, encoding="utf-8")
    return "updated"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(Path(__file__).with_name("town_maps.json")))
    ap.add_argument("--feed-file")
    args = ap.parse_args()

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    out_dir = Path(config.get("output_dir", "data"))
    out_dir.mkdir(parents=True, exist_ok=True)
    overrides = config.get("name_overrides")

    try:
        root = load_xml_from_file(args.feed_file) if args.feed_file else load_xml_from_url(config["feed_url"])
    except Exception as e:
        print(f"[FAILED] could not load feed: {e}", file=sys.stderr)
        sys.exit(1)

    precincts = (root.findtext(".//PrecinctsReporting") or "").strip()
    failures = 0
    for m in config["maps"]:
        slug = m["slug"]
        try:
            candidates, towns = collect(root, m["office"])
            if len(candidates) < 1 or not towns:
                raise ValueError("no candidates or towns found")
            fields, rows, labels, order = build(candidates, towns, m.get("party_map", {}), overrides)
            status = write_if_changed(out_dir / f"{slug}.csv", render(fields, rows))

            # Statewide totals + precincts reporting, for a companion chart / chart notes.
            totals = {cid: sum(t["votes"].get(cid, 0) for t in towns.values()) for cid in order}
            grand = sum(totals.values())
            srows = [
                {
                    "Candidate": labels[cid],
                    "Votes": totals[cid],
                    "Percent": round(totals[cid] / grand * 100, 1) if grand else 0.0,
                    "PrecinctsReporting": precincts,
                }
                for cid in sorted(order, key=lambda c: (-totals[c], candidates[c]["name"]))
            ]
            sstatus = write_if_changed(
                out_dir / f"{slug}-summary.csv",
                render(["Candidate", "Votes", "Percent", "PrecinctsReporting"], srows),
            )
            print(f"[{status}] {slug}.csv ({len(rows)} towns)   [{sstatus}] {slug}-summary.csv   precincts: {precincts}")
        except Exception as e:
            failures += 1
            print(f"[FAILED] {slug} ({m['office']}): {e}", file=sys.stderr)

    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
