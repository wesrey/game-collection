"""Tests for kallax.py. Run: python3 -m unittest discover -s scripts -p 'test_*.py'"""
import random
import tempfile
import unittest
from pathlib import Path

import kallax
from kallax import Bin, Item, mils

EXACT = Bin.kallax(13, 13, 15, slack=0)


def box(item_id, dims, group="other", kind="game"):
    return Item(str(item_id), f"Box {item_id}", kind, group, dims, "")


def row(item_id, name="", kind="game", group="", tall="", wide="", deep="", stored_in=""):
    return {"id": item_id, "name": name or item_id, "kind": kind, "group": group,
            "tall": tall, "wide": wide, "deep": deep, "stored_in": stored_in, "notes": ""}


def owned(item_id, name="", kind="game", max_players=4):
    return {"id": item_id, "name": name or item_id, "objectid": int(item_id.split("-")[0]),
            "kind": kind, "minPlayers": 1, "maxPlayers": max_players, "bestPlayers": [3]}


class PackingTest(unittest.TestCase):

    def check_layout(self, cubbies, items, bin):
        placed = []
        for cubby in cubbies:
            rects = []
            for p in cubby.placements:
                placed.append(p.item.id)
                if p.filler:
                    self.assertEqual(p.item.group, "other")
                    self.assertNotEqual(cubby.group, "other")
                else:
                    self.assertEqual(p.item.group, cubby.group)
                if cubby.whole:
                    continue
                x, y, w, h = p.rect
                self.assertTrue(x >= 0 and y >= 0 and x + w <= bin.w and y + h <= bin.h, p)
                self.assertLessEqual(p.pose.depth, bin.depth)
                self.assertFalse(any(kallax.overlaps(p.rect, r) for r in rects), p)
                rects.append(p.rect)
            for p in cubby.placements:
                others = [q.rect for q in cubby.placements if q is not p]
                self.assertTrue(kallax.is_supported(p.rect, others), f"{p} is floating")
        self.assertCountEqual(placed, [i.id for i in items])

    def test_spines_flat_spines_tile_exactly(self):
        items = [box(f"spine{i}", (8, 3.25, 10)) for i in range(4)]
        items += [box("flat", (2, 13, 12))]
        items += [box(f"small{i}", (3, 1, 10)) for i in range(13)]
        cubbies, too_big = kallax.pack_all(items, EXACT)
        self.check_layout(cubbies, items, EXACT)
        self.assertEqual((len(cubbies), too_big), (1, []))
        self.assertAlmostEqual(cubbies[0].fill, 1.0)

    def test_flats_spines_flat_tile_exactly(self):
        items = [box(f"flat{i}", (3, 13, 12)) for i in range(2)]
        items += [box(f"spine{i}", (5, 3.25, 10)) for i in range(4)]
        items += [box("top", (2, 13, 12))]
        cubbies, too_big = kallax.pack_all(items, EXACT)
        self.check_layout(cubbies, items, EXACT)
        self.assertEqual((len(cubbies), too_big), (1, []))
        self.assertAlmostEqual(cubbies[0].fill, 1.0)

    def test_four_square_boxes_per_cubby(self):
        items = [box(i, (2.75, 11.75, 11.75)) for i in range(5)]
        cubbies, _ = kallax.pack_all(items, Bin.kallax())
        self.assertEqual([len(c.placements) for c in cubbies], [4, 1])

    def test_random_collection_keeps_invariants(self):
        rng = random.Random(7)
        sizes = [(2.75, 11.75, 11.75), (4, 12, 12), (2.5, 10.5, 7.5), (1.5, 6.25, 4.5),
                 (1.25, 4, 3), (2.75, 12.5, 8.75), (6, 12.5, 12.5)]
        items = [box(i, rng.choice(sizes), rng.choice(kallax.GROUPS)) for i in range(80)]
        bin = Bin.kallax()
        cubbies, too_big = kallax.pack_all(items, bin, restarts=3)
        self.assertEqual(too_big, [])
        self.check_layout(cubbies, items, bin)

    def test_settle_pushes_boxes_left_and_keeps_support(self):
        flat = kallax.Placement(box("flat", (2, 6, 9)), kallax.Pose("flat", mils(6), mils(2), mils(9)), mils(4), 0)
        spine = kallax.Placement(box("spine", (1, 5, 9)), kallax.Pose("standing", mils(1), mils(5), mils(9)), mils(8), mils(2))
        settled = {p.item.id: p for p in kallax.settle([flat, spine])}
        self.assertEqual(settled["flat"].x, 0)
        self.assertLess(settled["spine"].x, mils(6))
        self.assertTrue(kallax.is_supported(settled["spine"].rect, [settled["flat"].rect]))

    def test_too_big_and_whole_cubby(self):
        gloomhaven = box("big", (7.75, 16.25, 11.75))
        fabric_bin = box("bin", (13, 13, 15), kind="container")
        cubbies, too_big = kallax.pack_all([gloomhaven, fabric_bin], Bin.kallax())
        self.assertEqual(too_big, [gloomhaven])
        self.assertEqual(len(cubbies), 1)
        self.assertTrue(cubbies[0].whole)

    def test_spine_on_flat_is_supported(self):
        flat = (0, 0, mils(12), mils(3))
        self.assertTrue(kallax.is_supported((0, mils(3), mils(3), mils(9)), [flat]))

    def test_floating_box_is_not_supported(self):
        self.assertFalse(kallax.is_supported((0, mils(3), mils(3), mils(9)), []))

    def test_nearly_level_tops_count_as_level(self):
        short, tall = (0, 0, mils(3), mils(11.5)), (mils(3), 0, mils(3), mils(11.75))
        self.assertTrue(kallax.is_supported((0, mils(11.75), mils(6), mils(1)), [short, tall]))

    def test_poses_keep_thin_side_out_and_respect_depth(self):
        options = kallax.poses((2, 12, 9), mils(13), mils(13), mils(15))
        self.assertTrue(all(mils(2) in (p.w, p.h) for p in options))
        self.assertEqual(kallax.poses((2, 12, 16), mils(13), mils(13), mils(15)), [])

    def test_loading_steps_name_what_a_box_sits_on(self):
        flat = box("flat", (3, 12, 12))
        spine = box("spine", (1, 9, 9))
        cubby = kallax.Cubby("other", [
            kallax.Placement(spine, kallax.Pose("standing", mils(1), mils(9), mils(9)), 0, mils(3)),
            kallax.Placement(flat, kallax.Pose("flat", mils(12), mils(3), mils(12)), 0, 0),
        ], Bin.kallax())
        steps = kallax.loading_steps(cubby)
        self.assertEqual([p.item.id for p, _ in steps], ["flat", "spine"])
        self.assertIn("on top of #1", steps[1][1])

    def test_loading_order_never_points_forward(self):
        def at(item_id, w, h, x, y):
            pose = kallax.Pose("standing", mils(w), mils(h), mils(9))
            return kallax.Placement(box(item_id, (1, 1, 1)), pose, mils(x), mils(y))
        left, right = at("left", 3, 7.5, 0, 0), at("right", 3, 7.4, 3, 0)
        # "leaner" sits a hair lower than "first" but leans on it from the right,
        # so a plain bottom-up sort would list it first and point forward.
        first, leaner = at("first", 3, 4, 0, 7.5), at("leaner", 2, 4, 3, 7.4)
        order = [p.item.id for p in kallax.loading_order([leaner, right, first, left])]
        self.assertLess(order.index("first"), order.index("leaner"))
        self.assertLess(order.index("left"), order.index("first"))

class StorageTest(unittest.TestCase):

    def test_parse_inches(self):
        self.assertEqual(kallax.parse_inches("11.75"), 11.75)
        self.assertEqual(kallax.parse_inches("11 3/4"), 11.75)
        self.assertEqual(kallax.parse_inches('11-3/4"'), 11.75)
        self.assertEqual(kallax.parse_inches("3/4"), 0.75)
        self.assertIsNone(kallax.parse_inches(" "))
        with self.assertRaises(ValueError):
            kallax.parse_inches("big")

    def test_fmt_inches(self):
        self.assertEqual(kallax.fmt_inches(4250), '4¼"')
        self.assertEqual(kallax.fmt_inches(125), '⅛"')
        self.assertEqual(kallax.fmt_inches(12000), '12"')

    def test_sync_appends_without_touching_edits(self):
        rows = [row("1", tall="3", wide="12", deep="12"), row("bin", kind="container"), row("9")]
        rows, added, stale = kallax.sync_storage(rows, [owned("1"), owned("2")])
        self.assertEqual([r["id"] for r in added], ["2"])
        self.assertEqual(rows[0]["tall"], "3")
        self.assertEqual([r["id"] for r in stale], ["9"])

    def test_read_write_keeps_extra_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "storage.csv"
            path.write_text("id,name,shelf\n1,One,top\n", encoding="utf-8")
            rows = kallax.read_storage(path)
            kallax.write_storage(rows, path)
            self.assertEqual(kallax.read_storage(path)[0]["shelf"], "top")

    def test_stored_in_and_off_shelf_chains(self):
        rows = [
            row("1", tall="3", wide="12", deep="12"),
            row("2", stored_in="1"),
            row("tin", kind="container", group="two", tall="4", wide="8", deep="6"),
            row("3", stored_in="tin"),
            row("bag", kind="container", stored_in="Off-Shelf"),
            row("4", stored_in="bag"),
        ]
        games = [owned(i) for i in ("1", "2", "3", "4")]
        storage = kallax.resolve_storage(rows, games)
        self.assertEqual([i.id for i in storage.shelved], ["1", "tin"])
        self.assertEqual([c.id for c in storage.shelved[0].contents], ["2"])
        self.assertEqual(storage.shelved[1].group, "two")
        self.assertEqual({i.id: inside for i, inside in storage.off_shelf}, {"4": ["bag"], "bag": []})
        self.assertEqual(storage.warnings, [])

    def test_unknown_id_and_loop_warn_instead_of_hanging(self):
        rows = [row("1", stored_in="nope"), row("2", stored_in="3"), row("3", stored_in="2"),
                row("5", stored_in="5")]
        storage = kallax.resolve_storage(rows, [owned(i) for i in ("1", "2", "3", "5")])
        self.assertEqual(len(storage.warnings), 3)
        self.assertEqual(sorted(i.id for i in storage.shelved), ["1", "2", "5"])

    def test_group_override_and_bad_dims(self):
        rows = [row("1", group="2P"), row("2", tall="3", wide="12")]
        storage = kallax.resolve_storage(rows, [owned("1"), owned("2")])
        self.assertEqual(storage.shelved[0].group, "two")
        self.assertIsNone(storage.shelved[1].dims)
        self.assertEqual(len(storage.warnings), 1)


if __name__ == "__main__":
    unittest.main()
