#!/usr/bin/env python3
"""Extract the fields the Shelf page needs from a BGG collection CSV export.

Input: data/collection.csv (gitignored - re-export it from
  https://boardgamegeek.com/geekcollection.php?action=exportcsv&subtype=boardgame&username=wesrey&exporttype=csv&all=0
  whenever the collection changes)

Output: data/games.json (committed - contains only name/rating/complexity/
  player-count/easy-to-learn fields, never pricing or location data from the
  export)

Easy to Learn flag: BGG's "preordered" checkbox is repurposed as an
introductory-friendly flag, since it's otherwise unused in this collection.
Toggle it on a game's collection entry on boardgamegeek.com to mark it,
then re-export.
"""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "collection.csv"
DEST = ROOT / "data" / "games.json"


def parse_best_players(raw):
    counts = []
    for token in raw.split(","):
        token = token.strip()
        if token.isdigit():
            counts.append(int(token))
    return counts


def main():
    if not SRC.exists():
        sys.exit(f"Missing {SRC} - export your BGG collection CSV there first.")

    games = []
    with SRC.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("wanttoplay") != "1":
                continue
            best_players = parse_best_players(row.get("bggbestplayers", ""))
            if not best_players:
                continue
            avgweight = float(row.get("avgweight") or 0)
            baverage = float(row.get("baverage") or 0)
            if avgweight <= 0 or baverage <= 0:
                continue
            games.append({
                "name": row["objectname"],
                "objectid": int(row["objectid"]),
                "avgweight": avgweight,
                "baverage": baverage,
                "bestPlayers": best_players,
                "easyToLearn": row.get("preordered") == "1",
            })

    games.sort(key=lambda g: g["name"])
    DEST.write_text(json.dumps(games, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(games)} games to {DEST}")


if __name__ == "__main__":
    main()
