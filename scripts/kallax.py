#!/usr/bin/env python3
"""Build public/kallax.html (the Kallax packing plan) from data/storage.csv.

Input: data/all_owned_games.json (written by extract_data.py - every owned
  game and expansion)
Input/Output: data/storage.csv (committed, hand-edited). This script appends a
  row for each newly owned game or expansion and never rewrites values you've
  entered. Columns:
    tall, wide, deep - box size in inches, measured flat on a table, lid up
      ("11.75" or "11 3/4"). Only needed when stored_in is blank.
    stored_in - blank: needs its own spot in a cubby. Another row's id: lives
      inside that box or container. "off-shelf": not in the Kallax at all.
    group - blank: worked out by classify() (containers default to "other").
      solo / two / party / other: override.
  Containers (bins, tins, ...) are rows you add yourself with kind
  "container" and any id you like.
Output: public/kallax.html

Packing: each cubby's front face is a 2D bin. Boxes go in thin side out -
standing like a book or lying flat - in any mix (spines on flats, flats on
spines, ...) as long as is_supported() says something is under them. Free
space is tracked as the list of maximal empty rectangles ("MaxRects"), so
gaps under overhangs can be filled too, the way you'd slide a box in from
the front. Each cubby is filled greedily, preferring placements whose edges
touch the most wall and neighbouring box ("contact perimeter"), several
times over with different tie-breaks; the fullest attempt wins.
"""
import csv
import heapq
import json
import random
import re
import sys
from dataclasses import dataclass, field, replace
from fractions import Fraction
from pathlib import Path

from generate import esc

ROOT = Path(__file__).resolve().parent.parent
OWNED = ROOT / "data" / "all_owned_games.json"
STORAGE = ROOT / "data" / "storage.csv"
TEMPLATE = ROOT / "scripts" / "kallax_template.html"
OUT = ROOT / "public" / "kallax.html"

# Inside of one Kallax cubby, in inches. Measure one of yours and adjust.
CUBBY_WIDTH = 13.0
CUBBY_HEIGHT = 13.0
CUBBY_DEPTH = 15.0
# Taken off the width and height so boxes slide in without jamming.
SLACK = 0.25
# Box tops within this much of each other count as one level surface.
LEVEL_TOLERANCE = 0.25
# Layout attempts per cubby; the fullest wins. More is denser but slower.
RESTARTS = 8

STORAGE_COLUMNS = ["id", "name", "kind", "group", "tall", "wide", "deep", "stored_in", "notes"]
OFF_SHELF = "off-shelf"
GROUPS = ["solo", "two", "party", "other"]
GROUP_LABELS = {"solo": "Solo", "two": "2-Player", "party": "Party", "other": "Other"}
GROUP_ALIASES = {"1": "solo", "1p": "solo", "2": "two", "2p": "two"}


def mils(inches):
    """Inches -> whole thousandths of an inch, so box edges compare exactly."""
    return round(inches * 1000)


def parse_inches(text):
    """'11.75', '11 3/4', '11-3/4' or '3/4' -> inches as a float; blank -> None."""
    text = (text or "").strip().rstrip('"').strip()
    if not text:
        return None
    text = re.sub(r"(\d)-(\d)", r"\1 \2", text)
    total = sum((Fraction(part) for part in text.split()), Fraction(0))
    if total <= 0:
        raise ValueError(f"{text!r} isn't a positive size")
    return float(total)


# --- storage.csv -----------------------------------------------------------

def read_storage(path=STORAGE):
    if not path.exists():
        return []
    rows = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            clean = {k: (v or "").strip() for k, v in row.items() if k is not None}
            for col in STORAGE_COLUMNS:
                clean.setdefault(col, "")
            rows.append(clean)
    return rows


def write_storage(rows, path=STORAGE):
    # Keep any extra columns you've added to the spreadsheet yourself.
    extra = []
    for row in rows:
        extra += [k for k in row if k not in STORAGE_COLUMNS and k not in extra]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=STORAGE_COLUMNS + extra, restval="")
        writer.writeheader()
        writer.writerows(rows)


def sync_storage(rows, owned):
    """Append a blank row for each owned game/expansion not in storage.csv yet.

    Existing rows are never changed. Returns (rows, added, stale), where stale
    are non-container rows that don't match anything owned.
    """
    known = {row["id"] for row in rows}
    added = [
        {col: "" for col in STORAGE_COLUMNS} | {"id": g["id"], "name": g["name"], "kind": g["kind"]}
        for g in owned if g["id"] not in known
    ]
    owned_ids = {g["id"] for g in owned}
    stale = [r for r in rows if r["kind"] != "container" and r["id"] not in owned_ids]
    return rows + added, added, stale


@dataclass
class Item:
    id: str
    name: str
    kind: str
    group: str
    dims: tuple | None  # (tall, wide, deep) in inches, as entered
    stored_in: str
    contents: list = field(default_factory=list)


@dataclass
class Storage:
    shelved: list    # Items that need their own spot (measured or not)
    off_shelf: list  # (Item, [names of the boxes it's inside, innermost first])
    warnings: list


def classify(game):
    """Default group for an owned game or expansion: "solo", "two", "party" or
    "other". A group typed into storage.csv overrides this.

    game has: name, objectid, kind ("game"/"expansion"), minPlayers and
    maxPlayers (int, or None if BGG has none), and bestPlayers (list of ints
    from BGG's best-player-count poll, possibly empty).
    """
    # TODO(human): decide which games default to solo / two / party.
    return "other"


def resolve_group(row, game, warnings):
    raw = row["group"].lower()
    raw = GROUP_ALIASES.get(raw, raw)
    if raw in GROUPS:
        return raw
    if raw:
        warnings.append(f"{row['name']}: group {row['group']!r} isn't solo/two/party/other, so it was worked out automatically.")
    if game is None:
        return "other"
    group = classify(game)
    if group not in GROUPS:
        raise ValueError(f"classify() returned {group!r} for {game['name']}; expected one of {GROUPS}")
    return group


def resolve_dims(row, warnings):
    try:
        dims = tuple(parse_inches(row[col]) for col in ("tall", "wide", "deep"))
    except (ValueError, ZeroDivisionError):
        warnings.append(f"{row['name']}: couldn't read tall/wide/deep ({row['tall']!r}, {row['wide']!r}, {row['deep']!r}).")
        return None
    if all(d is None for d in dims):
        return None
    if any(d is None for d in dims):
        warnings.append(f"{row['name']}: only some of tall/wide/deep are filled in.")
        return None
    return dims


def _find_loop(item, items):
    """An item on a stored_in cycle reachable from item, or None."""
    seen = set()
    cur = item
    while cur.stored_in and cur.stored_in != OFF_SHELF:
        if cur.id in seen:
            return cur
        seen.add(cur.id)
        cur = items[cur.stored_in]
    return None


def resolve_storage(rows, owned):
    games = {g["id"]: g for g in owned}
    warnings = []
    items = {}
    for row in rows:
        item_id = row["id"]
        if not item_id:
            warnings.append(f"A row named {row['name']!r} has no id, so it was skipped.")
            continue
        if item_id not in games and row["kind"] != "container":
            continue  # no longer owned - main() reports these
        if item_id in items:
            warnings.append(f"Id {item_id} is in storage.csv more than once; only the first row is used.")
            continue
        game = games.get(item_id)
        items[item_id] = Item(
            id=item_id,
            name=row["name"] or (game["name"] if game else item_id),
            kind=game["kind"] if game else "container",
            group=resolve_group(row, game, warnings),
            dims=resolve_dims(row, warnings),
            stored_in=row["stored_in"],
        )

    for item in items.values():
        if item.stored_in.lower() == OFF_SHELF:
            item.stored_in = OFF_SHELF
        elif item.stored_in and item.stored_in not in items:
            warnings.append(f"{item.name}: stored_in {item.stored_in!r} doesn't match any id, so it needs its own spot.")
            item.stored_in = ""
    for item in items.values():
        while (looped := _find_loop(item, items)) is not None:
            warnings.append(f"{looped.name}: stored_in loops back around to itself, so it needs its own spot.")
            looped.stored_in = ""

    for item in sorted(items.values(), key=lambda i: i.name):
        if item.stored_in and item.stored_in != OFF_SHELF:
            items[item.stored_in].contents.append(item)

    off_shelf = []
    for item in items.values():
        inside = []
        cur = item
        while cur.stored_in and cur.stored_in != OFF_SHELF:
            cur = items[cur.stored_in]
            inside.append(cur.name)
        if cur.stored_in == OFF_SHELF:
            off_shelf.append((item, inside))

    shelved = sorted((i for i in items.values() if not i.stored_in), key=lambda i: i.name)
    off_shelf.sort(key=lambda pair: pair[0].name)
    return Storage(shelved, off_shelf, warnings)


# --- geometry --------------------------------------------------------------

@dataclass(frozen=True)
class Pose:
    name: str
    w: int      # mils across the cubby front
    h: int      # mils up the cubby front
    depth: int  # mils into the cubby


@dataclass(frozen=True)
class Bin:
    w: int       # usable front width (slack taken off), mils
    h: int       # usable front height, mils
    depth: int
    full_w: int  # the cubby itself, for boxes that fill it exactly
    full_h: int

    @classmethod
    def kallax(cls, width=CUBBY_WIDTH, height=CUBBY_HEIGHT, depth=CUBBY_DEPTH, slack=SLACK):
        return cls(mils(width - slack), mils(height - slack), mils(depth), mils(width), mils(height))


def poses(dims, w, h, depth):
    """Ways to shelve a box thin side out that fit a w x h front, depth deep.

    The thinnest side always faces out: showing a lid instead covers lots of
    front with one game, which looks full but stores the least. That leaves
    standing (thin side across) or flat (thin side up), each with either of
    the other two sides running back into the cubby.
    """
    s, m, l = sorted(mils(d) for d in dims)
    out, seen = [], set()
    for pose in (
        Pose("standing", s, m, l),
        Pose("standing", s, l, m),
        Pose("flat", m, s, l),
        Pose("flat", l, s, m),
    ):
        if pose.w <= w and pose.h <= h and pose.depth <= depth and (pose.w, pose.h) not in seen:
            seen.add((pose.w, pose.h))
            out.append(pose)
    return out


def support_spans(rect, placed):
    """Merged (start, end) stretches of rect's bottom edge that rest on box
    tops level with it (within LEVEL_TOLERANCE). rect and placed are
    (x, y, w, h) tuples in mils, y measured up from the cubby floor."""
    x, y, w, _ = rect
    tolerance = mils(LEVEL_TOLERANCE)
    overlaps = sorted(
        (max(x, px), min(x + w, px + pw))
        for px, py, pw, ph in placed
        if y - tolerance <= py + ph <= y and px < x + w and px + pw > x
    )
    spans = []
    for start, end in overlaps:
        if spans and start <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(spans[-1][1], end))
        else:
            spans.append((start, end))
    return spans


def is_supported(rect, placed):
    """Can a box sit at rect = (x, y, w, h), given the boxes already placed?
    Pose doesn't matter here - a spine on a flat box is fine if it's held up.
    """
    x, y, w, h = rect
    if y == 0:
        return True
    return sum(end - start for start, end in support_spans(rect, placed)) >= w


def overlaps(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def contact(rect, placed, bin):
    """Length of rect's edges touching the cubby walls or other boxes."""
    x, y, w, h = rect
    total = 0
    if x == 0:
        total += h
    if x + w == bin.w:
        total += h
    if y == 0:
        total += w
    if y + h == bin.h:
        total += w
    for px, py, pw, ph in placed:
        if px + pw == x or px == x + w:
            total += max(0, min(y + h, py + ph) - max(y, py))
        if py + ph == y or py == y + h:
            total += max(0, min(x + w, px + pw) - max(x, px))
    return total


class FreeSpace:
    """The maximal empty rectangles left on a cubby's front (MaxRects)."""

    def __init__(self, w, h):
        self.rects = [(0, 0, w, h)]

    def place(self, x, y, w, h):
        split = []
        for fx, fy, fw, fh in self.rects:
            if x >= fx + fw or x + w <= fx or y >= fy + fh or y + h <= fy:
                split.append((fx, fy, fw, fh))
                continue
            if x > fx:
                split.append((fx, fy, x - fx, fh))
            if x + w < fx + fw:
                split.append((x + w, fy, fx + fw - x - w, fh))
            if y > fy:
                split.append((fx, fy, fw, y - fy))
            if y + h < fy + fh:
                split.append((fx, y + h, fw, fy + fh - y - h))
        # Drop rectangles that sit entirely inside a bigger one.
        split = sorted(set(split), key=lambda r: r[2] * r[3], reverse=True)
        self.rects = []
        for r in split:
            if not any(k[0] <= r[0] and k[1] <= r[1] and r[0] + r[2] <= k[0] + k[2]
                       and r[1] + r[3] <= k[1] + k[3] for k in self.rects):
                self.rects.append(r)


# --- packing ---------------------------------------------------------------

def stored_area(dims):
    """Front area of a box's smallest thin-side-out face - the least cubby
    front it can take up. Layouts are compared by this rather than by how
    much front they cover, so turning a box to show a bigger face never
    looks like a win: the goal is more boxes per cubby, not bigger faces."""
    s, m, _ = sorted(mils(d) for d in dims)
    return s * m


@dataclass
class Placement:
    item: Item
    pose: Pose
    x: int
    y: int
    filler: bool = False  # an Other item filling a gap in a group's cubby

    @property
    def rect(self):
        return (self.x, self.y, self.pose.w, self.pose.h)

    @property
    def stored(self):
        return stored_area(self.item.dims)


@dataclass
class Cubby:
    group: str
    placements: list
    bin: Bin
    whole: bool = False  # one box that fills the cubby by itself

    @property
    def fill(self):
        if self.whole:
            return 1.0
        return sum(p.pose.w * p.pose.h for p in self.placements) / (self.bin.w * self.bin.h)


def _scorer(attempt):
    """Placement ranking for one attempt; higher tuples win.

    snug is the share of the box's outline touching walls or other boxes, so
    a box that exactly plugs a gap beats one that merely sits in a corner.
    Bigger boxes go first on ties, and a box shows its smallest face if that
    works just as well.
    """
    rng = random.Random(attempt)

    def score(rect, placed, bin, stored):
        x, y, w, h = rect
        snug = contact(rect, placed, bin) / (2 * (w + h))
        if attempt == 0:
            return (snug, stored, -w * h, -y, -x)
        if attempt == 1:
            return (stored, snug, -w * h, -y, -x)
        if attempt == 2:
            return (-y, snug, stored, -w * h, -x)
        return (snug * rng.uniform(0.8, 1.2), stored, -w * h, -y, -x)
    return score


def _fill(candidates, options, free, placements, bin, score, filler):
    """Greedily place candidates into one cubby until none fit."""
    remaining = list(candidates)
    while remaining:
        rects = [p.rect for p in placements]
        widest = max((r[2] for r in free.rects), default=0)
        tallest = max((r[3] for r in free.rects), default=0)
        best, tried = None, set()
        for item in remaining:
            item_poses = options[item.id]
            if item_poses in tried:  # same size as an item already tried
                continue
            tried.add(item_poses)
            stored = stored_area(item.dims)
            for pose in item_poses:
                if pose.w > widest or pose.h > tallest:
                    continue
                for fx, fy, fw, fh in free.rects:
                    if pose.w > fw or pose.h > fh:
                        continue
                    for x in {fx, fx + fw - pose.w}:
                        rect = (x, fy, pose.w, pose.h)
                        if not is_supported(rect, rects):
                            continue
                        key = score(rect, rects, bin, stored)
                        if best is None or key > best[0]:
                            best = (key, item, pose, x, fy)
        if best is None:
            return
        _, item, pose, x, y = best
        placements.append(Placement(item, pose, x, y, filler))
        free.place(x, y, pose.w, pose.h)
        remaining.remove(item)


def fill_cubby(primary, fillers, options, bin, restarts=RESTARTS):
    """The fullest layout found for one cubby: as much of primary as fits,
    then fillers in whatever gaps are left."""
    best, best_key = [], None
    for attempt in range(restarts):
        score = _scorer(attempt)
        free = FreeSpace(bin.w, bin.h)
        placements = []
        _fill(primary, options, free, placements, bin, score, filler=False)
        _fill(fillers, options, free, placements, bin, score, filler=True)
        key = (
            sum(p.stored for p in placements if not p.filler),
            sum(p.stored for p in placements),
            sum(p.pose.w * p.pose.h for p in placements),
        )
        if best_key is None or key > best_key:
            best, best_key = placements, key
    return best


def settle(placements):
    """Slide every box as far left as it will go, bottom row first - the way
    you'd push boxes together on a real shelf - so leftover space collects at
    the right wall instead of in thin slivers between boxes. Leaves the
    layout alone if sliding would leave any box without support."""
    order = sorted(placements, key=lambda p: (p.y, p.x))
    settled = []
    for i, p in enumerate(order):
        others = [q.rect for q in settled] + [q.rect for q in order[i + 1:]]
        below = [q.rect for q in settled]
        for x in sorted({0, p.x} | {r[0] + r[2] for r in others if r[0] + r[2] < p.x}):
            rect = (x, p.y, p.pose.w, p.pose.h)
            if not any(overlaps(rect, r) for r in others) and is_supported(rect, below):
                settled.append(replace(p, x=x))
                break
        else:
            return placements
    return settled


def pack_all(items, bin, restarts=RESTARTS):
    """Pack measured items into labeled cubbies. Returns (cubbies, too_big)."""
    options, whole, too_big = {}, [], []
    queues = {g: [] for g in GROUPS}
    for item in sorted(items, key=lambda i: (i.name, i.id)):
        fits = tuple(poses(item.dims, bin.w, bin.h, bin.depth))
        if fits:
            options[item.id] = fits
            queues[item.group].append(item)
        elif snug := poses(item.dims, bin.full_w, bin.full_h, bin.depth):
            whole.append((item, snug[0]))
        else:
            too_big.append(item)

    cubbies = []
    others = queues["other"]
    for group in GROUPS:
        for item, pose in whole:
            if item.group == group:
                cubbies.append(Cubby(group, [Placement(item, pose, 0, 0)], bin, whole=True))
        queue = queues[group]
        while queue:
            fillers = others if group != "other" else []
            placements = settle(fill_cubby(queue, fillers, options, bin, restarts))
            if not placements:
                raise RuntimeError(f"Nothing fit an empty cubby: {[i.name for i in queue]}")
            placed = {p.item.id for p in placements}
            queue[:] = [i for i in queue if i.id not in placed]
            others[:] = [i for i in others if i.id not in placed]
            cubbies.append(Cubby(group, placements, bin))
    return cubbies, too_big


# --- rendering -------------------------------------------------------------

EIGHTHS = {1: "⅛", 2: "¼", 3: "⅜", 4: "½", 5: "⅝", 6: "¾", 7: "⅞"}


def fmt_inches(m):
    """Mils -> inches to the nearest eighth, e.g. 4250 -> 4¼\"."""
    whole, rest = divmod(round(m / 125), 8)
    return (str(whole) if whole or not rest else "") + EIGHTHS.get(rest, "") + '"'


def fmt_dims(dims):
    return " × ".join(fmt_inches(mils(d)) for d in dims)


def describe_contents(item):
    return ", ".join(
        c.name + (f" (holding {describe_contents(c)})" if c.contents else "")
        for c in item.contents
    )


def rests_on(p, q):
    """Does box p sit on box q's top?"""
    return (p.y - mils(LEVEL_TOLERANCE) <= q.y + q.pose.h <= p.y
            and q.x < p.x + p.pose.w and q.x + q.pose.w > p.x)


def leans_on(p, q):
    """Is box q right up against p's left side?"""
    return q.x + q.pose.w == p.x and q.y < p.y + p.pose.h and q.y + q.pose.h > p.y


def loading_order(placements):
    """Bottom-up and left to right, but never before a box this one rests on
    or leans against - so every step only points back to earlier steps."""
    waiting = {
        i: {j for j, q in enumerate(placements) if j != i and (rests_on(p, q) or leans_on(p, q))}
        for i, p in enumerate(placements)
    }
    ready = [(p.y, p.x, i) for i, p in enumerate(placements) if not waiting[i]]
    heapq.heapify(ready)
    order = []
    while ready:
        *_, i = heapq.heappop(ready)
        order.append(placements[i])
        for j, deps in waiting.items():
            if i in deps:
                deps.remove(i)
                if not deps:
                    heapq.heappush(ready, (placements[j].y, placements[j].x, j))
    if len(order) < len(placements):  # boxes pinwheeled around each other
        return sorted(placements, key=lambda p: (p.y, p.x))
    return order


def loading_steps(cubby):
    """Placements in loading order, each with where it goes."""
    order = loading_order(cubby.placements)
    steps = []
    for p in order:
        if p.y == 0:
            base = "on the floor"
        else:
            under = [n for n, q in enumerate(order, 1) if rests_on(p, q)]
            base = "on top of " + " and ".join(f"#{n}" for n in under)
        beside = [n for n, q in enumerate(order, 1) if leans_on(p, q)]
        if p.x == 0:
            side = "against the left wall"
        elif beside:
            side = f"right of #{beside[0]}"
        elif p.x + p.pose.w == cubby.bin.w:
            side = "against the right wall"
        else:
            side = f"{fmt_inches(p.x)} from the left wall"
        size = f"{fmt_inches(p.pose.w)} wide × {fmt_inches(p.pose.h)} tall"
        steps.append((p, f"{p.pose.name}, {size}, {base}, {side}"))
    return steps


def render_cubby(cubby, number):
    label = f"{GROUP_LABELS[cubby.group]} {number}"
    fill = "fills the whole cubby" if cubby.whole else f"{round(cubby.fill * 100)}% full"
    boxes, items = [], []
    for n, (p, where) in enumerate(loading_steps(cubby), 1):
        classes = ["box", f"g-{p.item.group}"]
        if p.filler:
            classes.append("filler")
        if p.item.kind == "container":
            classes.append("container")
        if p.pose.h > p.pose.w:
            classes.append("upright")
        style = (
            f"left:{p.x / cubby.bin.full_w * 100:.3f}%;"
            f"bottom:{p.y / cubby.bin.full_h * 100:.3f}%;"
            f"width:{p.pose.w / cubby.bin.full_w * 100:.3f}%;"
            f"height:{p.pose.h / cubby.bin.full_h * 100:.3f}%"
        )
        title = f"{n}. {p.item.name}: {p.pose.name}, {fmt_dims(p.item.dims)} (tall × wide × deep)"
        boxes.append(
            f'<div class="{" ".join(classes)}" style="{style}" title="{esc(title)}">'
            f'<span><b>{n}</b> {esc(p.item.name)}</span></div>'
        )
        tags = ' <span class="tag">filler</span>' if p.filler else ""
        holds = f'<div class="holds">holding {esc(describe_contents(p.item))}</div>' if p.item.contents else ""
        items.append(f'<li><span class="iname">{esc(p.item.name)}</span>{tags} '
                     f'<span class="where">{esc(where)}</span>{holds}</li>')
    return f'''<article class="cubby">
  <header class="cubby-head"><span class="cubby-name g-{cubby.group}">{esc(label)}</span><span class="cubby-fill">{fill}</span></header>
  <div class="frame">{"".join(boxes)}</div>
  <ol class="steps">{"".join(items)}</ol>
</article>'''


def render_list(title, anchor, entries, open_=False):
    if not entries:
        return ""
    body = "".join(f"<li>{e}</li>" for e in entries)
    return f'''<details class="extra" id="{anchor}"{" open" if open_ else ""}>
  <summary>{esc(title)} <span class="count">{len(entries)}</span></summary>
  <ul>{body}</ul>
</details>'''


def render(cubbies, too_big, storage):
    measured = sum(1 for i in storage.shelved if i.dims)
    needing = len(storage.shelved)
    by_group = {g: [c for c in cubbies if c.group == g] for g in GROUPS}

    sections, nav = [], []
    for group in GROUPS:
        if not by_group[group]:
            continue
        cards = "\n".join(render_cubby(c, n) for n, c in enumerate(by_group[group], 1))
        count = len(by_group[group])
        sections.append(f'''<section class="group" id="g-{group}">
  <div class="shelf-tag"><span class="num g-{group}">{GROUP_LABELS[group]}</span><span class="word">cubbies</span><span class="count">{count} {"cubby" if count == 1 else "cubbies"}</span></div>
  <div class="grid">
{cards}
  </div>
</section>''')
        nav.append(f'<a href="#g-{group}">{GROUP_LABELS[group]}</a>')
    if not sections:
        sections.append('<p class="empty">Nothing measured yet. Fill in tall, wide and deep in data/storage.csv and re-run kallax.py.</p>')

    unmeasured = [
        esc(i.name) + (f' <span class="dim">holding {esc(describe_contents(i))}</span>' if i.contents else "")
        for i in storage.shelved if not i.dims
    ]
    footer = [
        render_list("Still to measure", "to-measure", unmeasured),
        render_list("Too big for a cubby", "too-big",
                    [f'{esc(i.name)} <span class="dim">{fmt_dims(i.dims)}</span>' for i in too_big], open_=True),
        render_list("Not in the Kallax", "off-shelf",
                    [esc(i.name) + f' <span class="dim">{esc("in " + ", in ".join(inside) if inside else "off-shelf")}</span>'
                     for i, inside in storage.off_shelf]),
        render_list("Warnings", "warnings", [esc(w) for w in storage.warnings], open_=True),
    ]
    if unmeasured:
        nav.append('<a href="#to-measure" class="dashed">To measure</a>')

    fills = [c.fill for c in cubbies]
    avg = round(sum(fills) / len(fills) * 100) if fills else 0
    chips = "".join(
        f'<span class="chip g-{g}">{GROUP_LABELS[g]} <b>{len(by_group[g])}</b></span>'
        for g in GROUPS if by_group[g]
    )
    summary = f'''<div class="stat"><span class="big">{len(cubbies)}</span><span class="word">{"cubby" if len(cubbies) == 1 else "cubbies"} needed</span></div>
  <div class="chips">{chips}</div>
  <div class="meta">Average fill {avg}% · {measured} of {needing} boxes measured</div>'''

    template = TEMPLATE.read_text(encoding="utf-8")
    return (template.replace("{{SUMMARY}}", summary)
            .replace("{{NAV}}", "\n".join(nav))
            .replace("{{SECTIONS}}", "\n\n".join(sections))
            .replace("{{FOOTER}}", "\n".join(f for f in footer if f)))


def main():
    if not OWNED.exists():
        sys.exit(f"Missing {OWNED} - run extract_data.py first.")
    owned = json.loads(OWNED.read_text(encoding="utf-8"))
    rows, added, stale = sync_storage(read_storage(), owned)
    if added:
        write_storage(rows)
        print(f"Added {len(added)} rows to {STORAGE}")
    for row in stale:
        print(f"Note: {row['name'] or row['id']} in storage.csv isn't an owned game. "
              "Set its kind to container if it's a container, or delete the row.")

    storage = resolve_storage(rows, owned)
    measured = [i for i in storage.shelved if i.dims]
    cubbies, too_big = pack_all(measured, Bin.kallax())
    OUT.write_text(render(cubbies, too_big, storage), encoding="utf-8")

    counts = ", ".join(
        f"{GROUP_LABELS[g]} {n}" for g in GROUPS if (n := sum(c.group == g for c in cubbies))
    )
    avg = round(sum(c.fill for c in cubbies) / len(cubbies) * 100) if cubbies else 0
    print(f"Wrote {OUT}: measured {len(measured)}/{len(storage.shelved)} · "
          f"{len(cubbies)} cubbies ({counts or 'none'}) · avg fill {avg}% · "
          f"{len(storage.warnings)} warnings")


if __name__ == "__main__":
    main()
