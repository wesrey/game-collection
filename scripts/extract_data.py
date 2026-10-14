#!/usr/bin/env python3
"""Extract the fields the Shelf page needs from a BGG collection CSV export.

Input: data/collection.csv (gitignored - re-export it from
  https://boardgamegeek.com/geekcollection.php?action=exportcsv&subtype=boardgame&username=wesrey&exporttype=csv&all=0
  whenever the collection changes)

Output: data/games.json (committed - contains only name/rating/complexity/
  player-count/easy-to-learn fields, never pricing or location data from the
  export)
Output: data/all_owned_games.json (committed - every owned game AND expansion,
  with only id/name/kind/player-count fields, for the Kallax packing plan)

Easy to Learn flag: BGG's "preordered" checkbox is repurposed as an
introductory-friendly flag, since it's otherwise unused in this collection.
Toggle it on a game's collection entry on boardgamegeek.com to mark it,
then re-export.

Also regenerates public/index.html (via generate.py) and public/kallax.html
(via kallax.py) once the JSON is written, so a single run of this script is
enough to update the site.
"""
import csv
import json
import sys
from pathlib import Path

import generate
import kallax

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "collection.csv"
DEST = ROOT / "data" / "games.json"
OWNED_DEST = ROOT / "data" / "all_owned_games.json"


def parse_best_players(raw):
    counts = []
    for token in raw.split(","):
        token = token.strip()
        if token.isdigit():
            counts.append(int(token))
    return counts


def parse_int(raw):
    raw = (raw or "").strip()
    return int(raw) if raw.isdigit() else None


def owned_entry(row, copy_number):
    """One physical box. A second copy of the same game gets id "<objectid>-2"
    so each copy can have its own row in data/storage.csv."""
    objectid = int(row["objectid"])
    return {
        "id": str(objectid) if copy_number == 1 else f"{objectid}-{copy_number}",
        "name": row["objectname"],
        "objectid": objectid,
        "kind": "expansion" if row.get("itemtype") == "expansion" else "game",
        "minPlayers": parse_int(row.get("minplayers")),
        "maxPlayers": parse_int(row.get("maxplayers")),
        "bestPlayers": parse_best_players(row.get("bggbestplayers", "")),
    }


def main():
    if not SRC.exists():
        sys.exit(f"Missing {SRC} - export your BGG collection CSV there first.")

    games = []
    owned = []
    copies = {}
    with SRC.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("own") == "1":
                copies[row["objectid"]] = copies.get(row["objectid"], 0) + 1
                owned.append(owned_entry(row, copies[row["objectid"]]))
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

    owned.sort(key=lambda g: (g["name"], g["id"]))
    OWNED_DEST.write_text(json.dumps(owned, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(owned)} owned games and expansions to {OWNED_DEST}")

    generate.main()
    kallax.main()


if __name__ == "__main__":
    main()
