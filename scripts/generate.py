#!/usr/bin/env python3
"""Build public/index.html (the "Shelf" page) from data/games.json.

Grouping and math were reverse-engineered from the original hand-built page:
  - A game belongs to the shelf for player count N if N appears in its BGG
    "Best Players" poll results (bestPlayers list) AND its wanttoplay flag
    was set when data/games.json was extracted.
  - Rel. Complexity% = avgweight / 2.5 * 100 (2.5 is the midpoint of BGG's
    1-5 weight scale, so this is "how far from medium complexity", not a
    per-shelf ranking - it's the same number on every shelf a game is on).
  - Rel. Rating% = min-max normalization of BGG's Bayesian average rating
    (baverage) within that specific shelf's group of games, rescaled to a
    5%-100% range (5% = worst rated game at that count, 100% = best rated
    game at that count). The 5% floor is cosmetic, so the worst-rated game
    on a shelf doesn't render as a stark 0%.
"""
import colorsys
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "games.json"
OUT = ROOT / "public" / "index.html"

COMPLEXITY_DIVISOR = 2.5
RATING_FLOOR = 5

SHELVES = [
    (1, "1", "Player"), (2, "2", "Players"), (3, "3", "Players"),
    (4, "4", "Players"), (5, "5", "Players"), (6, "6", "Players"),
    (7, "7", "Players"), (8, "8", "Players"),
    ("9-12", "9–12", "Players"), ("13+", "13+", "Players"),
]


def bucket_key(count):
    if isinstance(count, int) and count >= 13:
        return "13+"
    if isinstance(count, int) and 9 <= count <= 12:
        return "9-12"
    return count


def esc(text):
    return html.escape(text, quote=True).replace("'", "&#x27;")


def rating_color(pct):
    t = min(max(pct, 0), 100) / 100
    h = (0 + t * 130) / 360
    l = 0.38 + t * (0.30 - 0.38)
    s = 0.68
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return "#{:02x}{:02x}{:02x}".format(
        round(r * 255), round(g * 255), round(b * 255)
    )


def complexity_color(pct):
    if pct <= 100:
        t = max(pct, 0) / 100
        h, s = 230 / 360, 0.76
        l = 0.915 + t * (0.50 - 0.915)
        r, g, b = colorsys.hls_to_rgb(h, l, s)
    else:
        ceiling = 160
        t = min((pct - 100) / (ceiling - 100), 1)
        start = (31, 63, 224)
        end = (19, 16, 21)
        r, g, b = (start[i] + t * (end[i] - start[i]) for i in range(3))
        r, g, b = r / 255, g / 255, b / 255
    return "#{:02x}{:02x}{:02x}".format(
        round(r * 255), round(g * 255), round(b * 255)
    )


def load_games():
    return json.loads(DATA.read_text(encoding="utf-8"))


def build_shelf(games, count):
    key = bucket_key(count)
    members = [g for g in games if bucket_key_matches(g, key)]
    if not members:
        return []

    baverages = [g["baverage"] for g in members]
    lo, hi = min(baverages), max(baverages)
    spread = hi - lo

    rows = []
    for g in members:
        weight_pct = round(g["avgweight"] / COMPLEXITY_DIVISOR * 100)
        rating_pct = (
            round(RATING_FLOOR + (g["baverage"] - lo) / spread * (100 - RATING_FLOOR))
            if spread else RATING_FLOOR
        )
        rows.append({
            "name": g["name"],
            "weight_pct": weight_pct,
            "rating_pct": rating_pct,
            "easy_to_learn": g.get("easyToLearn", False),
        })

    rows.sort(key=lambda r: r["rating_pct"], reverse=True)
    return rows


def bucket_key_matches(game, key):
    if key == "13+":
        return any(n >= 13 for n in game["bestPlayers"])
    if key == "9-12":
        return any(9 <= n <= 12 for n in game["bestPlayers"])
    return key in game["bestPlayers"]


def render_row(row, alt):
    cls = "row alt" if alt else "row"
    width = min(row["weight_pct"], 100)
    color = complexity_color(row["weight_pct"])
    pick_badge = ' <span class="pick" title="Simple rules, quick to teach">Easy to Learn</span>' if row["easy_to_learn"] else ""
    return f'''<div class="{cls}">
  <div class="name">{esc(row["name"])}{pick_badge}</div>
  <div class="complexity">
    <div class="cbar"><div class="cfill" style="width:{width}%; background:{color};"></div></div>
    <span class="cpct">{row["weight_pct"]}%</span>
  </div>
  <div class="rating" style="color:{rating_color(row["rating_pct"])};">{row["rating_pct"]}%</div>
</div>'''


def render_shelf(section_id, num_label, word_label, rows):
    plural = "game" if len(rows) == 1 else "games"
    body = "\n".join(render_row(r, i % 2 == 1) for i, r in enumerate(rows))
    return f'''<section class="shelf" id="{section_id}">
  <div class="shelf-tag"><span class="num">{num_label}</span><span class="word">{word_label}</span><span class="count">{len(rows)} {plural}</span></div>
  <div class="colhead">
  <div></div>
  <div>Rel. Complexity</div>
  <div>Rel. Rating</div>
</div>
  <div class="rows">
    {body}
  </div>
</section>'''


def section_id_for(count):
    if count == "9-12":
        return "p9to12"
    if count == "13+":
        return "p13plus"
    return f"p{count}"


def main():
    games = load_games()

    shelves_html = []
    nav_links = []
    solo_html = ""

    for count, num_label, word_label in SHELVES:
        rows = build_shelf(games, count)
        if not rows:
            continue
        section_id = section_id_for(count)
        shelf_html = render_shelf(section_id, num_label, word_label, rows)

        if count == 1:
            solo_html = shelf_html
        else:
            shelves_html.append(shelf_html)
            nav_links.append(f'<a href="#{section_id}">{num_label}</a>')

    nav_links.append('<a href="#psolo" class="solo-link">Solo</a>')

    template = (ROOT / "scripts" / "template.html").read_text(encoding="utf-8")
    output = template.replace("{{NAV}}", "\n".join(nav_links))
    output = output.replace("{{SHELVES}}", "\n\n".join(shelves_html))
    output = output.replace("{{SOLO}}", solo_html)

    OUT.write_text(output, encoding="utf-8")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
