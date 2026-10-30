import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "outil"))
import extracteur as e

class ExtracteurTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.recipe = self.root / "recipe.json"
        self.payload = b"verified resource"
        self.out = {"path": "maps/PS4Stages/test.bin", "size": len(self.payload),
                    "sha256": e.digest(self.payload)}
        self.spec = {"catalog": "catalog.bin", "name": "encoded", **{k:self.out[k] for k in ("size","sha256")}}
        self.data = {"format": 1, "id": "fixture", "sources": {"s": self.spec},
                     "jobs": [{"id": "test", "group": "test", "operation": "copy",
                               "inputs": ["s"], "outputs": [self.out], "installable": True}]}
        self.recipe.write_text(json.dumps(self.data))
        self.recipe_patch = patch.object(e, "RECIPE", self.recipe)
        self.recipe_patch.start()
        self.closed_patch = patch.object(e, "game_closed", lambda: None)
        self.closed_patch.start()
        self.game = self.root / "game"
        self.game.mkdir()
        (self.game / "game.exe").write_bytes(b"fixture")

    def tearDown(self):
        self.closed_patch.stop()
        self.recipe_patch.stop()
        self.tmp.cleanup()

    def make_zip(self, payload=None, extra=None, alter_manifest=False):
        p = self.root / (str(len(list(self.root.glob("*.zip")))) + ".zip")
        m = e.manifest(self.data, self.data["jobs"])
        if alter_manifest:
            m["files"][0] = dict(m["files"][0], sha256=e.digest(b"malicious"))
        with zipfile.ZipFile(p, "w") as z:
            e.zip_add(z, self.out["path"], self.payload if payload is None else payload)
            e.zip_add(z, "manifest.json", json.dumps(m).encode())
            if extra:
                z.writestr(extra, b"bad")
        return p

    def test_zip_accepts_trusted_payload(self):
        self.assertTrue(e.verify(self.make_zip(), lambda _: None)["installable"])

    def test_tamper_rejected(self):
        with self.assertRaises(ValueError):
            e.verify(self.make_zip(b"x" * len(self.payload)))

    def test_untrusted_manifest_rejected(self):
        with self.assertRaises(ValueError):
            e.verify(self.make_zip(alter_manifest=True))

    def test_unknown_file_rejected(self):
        with self.assertRaises(ValueError):
            e.verify(self.make_zip(extra="unexpected.bin"))

    def test_traversal_rejected(self):
        with self.assertRaises(ValueError):
            e.verify(self.make_zip(extra="../escape"))

    def test_case_duplicate_rejected(self):
        with self.assertRaises(ValueError):
            e.verify(self.make_zip(extra=self.out["path"].upper()))

    def test_windows_paths_rejected(self):
        for name in ("C:/foo", "/foo", "a\\b", "a/../b", "a//b", "a/NUL.txt", "a/b:ads",
                     "a/file.", "a/file ", "a/\x00bad"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                e.safe_name(name)

    def test_install_restore_existing(self):
        target = e.under(self.game, self.out["path"])
        target.parent.mkdir(parents=True)
        target.write_bytes(b"old")
        e.install(self.make_zip(), self.game)
        self.assertEqual(target.read_bytes(), self.payload)
        state = next(self.game.glob("DOA5LR-PS4-Backups/*/state.json"))
        e.restore_state(state)
        self.assertEqual(target.read_bytes(), b"old")

    def test_restore_refuses_user_changes(self):
        e.install(self.make_zip(), self.game)
        e.under(self.game, self.out["path"]).write_bytes(b"user change")
        state = next(self.game.glob("DOA5LR-PS4-Backups/*/state.json"))
        with self.assertRaises(ValueError):
            e.restore_state(state)

    def test_legacy_layout_install_and_restore(self):
        target = e.under(self.game, "PS4Stages/test.bin")
        target.parent.mkdir()
        target.write_bytes(b"previous legacy file")
        e.install(self.make_zip(), self.game)
        self.assertEqual(target.read_bytes(), self.payload)
        self.assertFalse(e.under(self.game, self.out["path"]).exists())
        state = next(self.game.glob("DOA5LR-PS4-Backups/*/state.json"))
        e.restore_state(state)
        self.assertEqual(target.read_bytes(), b"previous legacy file")

    def test_ambiguous_layout_refused(self):
        (self.game / "PS4Stages").mkdir()
        (self.game / "maps/PS4Stages").mkdir(parents=True)
        with self.assertRaises(ValueError):
            e.install(self.make_zip(), self.game)

    def test_damaged_backup_refused(self):
        target = e.under(self.game, self.out["path"])
        target.parent.mkdir(parents=True)
        target.write_bytes(b"old")
        e.install(self.make_zip(), self.game)
        state = next(self.game.glob("DOA5LR-PS4-Backups/*/state.json"))
        e.under(state.parent, "originals/"+self.out["path"]).write_bytes(b"broken backup")
        with self.assertRaises(ValueError):
            e.restore_state(state)
        self.assertEqual(target.read_bytes(), self.payload)

    def test_failed_install_rolls_back(self):
        target = e.under(self.game, self.out["path"])
        original = e.os.replace
        calls = 0
        def replace(src, dst):
            nonlocal calls
            if Path(dst) == target:
                calls += 1
                raise OSError("simulated I/O failure")
            return original(src, dst)
        with patch.object(e.os, "replace", replace), self.assertRaises(OSError):
            e.install(self.make_zip(), self.game)
        self.assertFalse(target.exists())
        self.assertFalse((self.game / ".doa5lr-ps4-import.lock").exists())

    def test_intermediate_zip_not_installable(self):
        self.data["jobs"][0]["installable"] = False
        self.recipe.write_text(json.dumps(self.data))
        with self.assertRaises(ValueError):
            e.install(self.make_zip(), self.game)
        self.assertFalse(e.under(self.game, self.out["path"]).exists())

    def test_source_resolution_uses_name_and_hash(self):
        source = self.root / "arbitrary-region" / "renamed-folder"
        source.mkdir(parents=True)
        cat = bytearray(52)
        cat[:4] = b"LFMO"
        struct.pack_into("<6I", cat, 4, 1, 1, 32, 40, 52, 52)
        struct.pack_into("<3I", cat, 40, 0, 1, 52)
        cat.extend(b"/encoded\0")
        (source / "catalog.bin").write_bytes(cat)
        arc = bytearray(160)
        arc[:4] = b"PCDM"
        struct.pack_into("<Q", arc, 8, 2)
        struct.pack_into("<4Q", arc, 64, 160, len(self.payload), len(self.payload), 0)
        arc.extend(self.payload)
        (source / "catalog.lnk").write_bytes(arc)
        resolver = e.Sources([self.root / "arbitrary-region"], {"s": self.spec}, lambda _: None)
        self.assertEqual(resolver.read("s"), self.payload)
        with (source / "catalog.lnk").open("r+b") as f:
            f.seek(160)
            f.write(b"x")
        with self.assertRaises(ValueError):
            resolver.read("s")

    def test_whole_file_source(self):
        folder = self.root / "ps4" / "Update"
        folder.mkdir(parents=True)
        (folder / "eboot.bin").write_bytes(b"other")
        (self.root / "ps4" / "eboot.bin").write_bytes(self.payload)
        spec = {"file": "eboot.bin", "size": len(self.payload), "sha256": e.digest(self.payload)}
        resolver = e.Sources([self.root / "ps4"], {"f": spec}, lambda _: None)
        self.assertEqual(resolver.read("f"), self.payload)
        (self.root / "ps4" / "eboot.bin").write_bytes(b"x" * len(self.payload))
        with self.assertRaisesRegex(ValueError, "eboot.bin"):
            resolver.read("f")

    def test_build_set_one_zip_per_card(self):
        source = self.root / "ps4"
        source.mkdir()
        cat = bytearray(52)
        cat[:4] = b"LFMO"
        struct.pack_into("<6I", cat, 4, 1, 1, 32, 40, 52, 52)
        struct.pack_into("<3I", cat, 40, 0, 1, 52)
        cat.extend(b"/encoded\0")
        (source / "catalog.bin").write_bytes(cat)
        arc = bytearray(160)
        arc[:4] = b"PCDM"
        struct.pack_into("<Q", arc, 8, 2)
        struct.pack_into("<4Q", arc, 64, 160, len(self.payload), len(self.payload), 0)
        arc.extend(self.payload)
        (source / "catalog.lnk").write_bytes(arc)
        missing = {"catalog": "catalog.bin", "name": "absent", "size": 3, "sha256": e.digest(b"abc")}
        self.data["sources"]["m"] = missing
        self.data["jobs"].append({"id": "c", "group": "costumes-test", "operation": "copy", "inputs": ["m"],
                                  "outputs": [{"path": "costume.bin", "size": 3, "sha256": missing["sha256"]}],
                                  "installable": True})
        self.recipe.write_text(json.dumps(self.data))
        self.assertEqual(e.split_groups(["test", "costumes-test", "costumes-particules"]),
                         {"stages": ["test"], "costumes": ["costumes-test"], "particles": ["costumes-particules"]})
        out = self.root / "zips"
        with self.assertRaisesRegex(ValueError, "Made: DOA5MissingContent.zip.*DOA5LR-PS4-Costumes.zip"):
            e.build_set([source], ["all"], out, log=lambda _: None)
        stages = out / "DOA5MissingContent.zip"
        info = e.verify(stages, lambda _: None)
        self.assertEqual([f["path"] for f in info["files"]], [self.out["path"]])
        with zipfile.ZipFile(stages) as z:
            self.assertEqual(json.loads(z.read("manifest.json"))["format"], e.PACK_FORMAT)
        self.assertEqual(sorted(p.name for p in out.iterdir()), ["DOA5MissingContent.zip"])
        with self.assertRaisesRegex(ValueError, "already in this folder"):
            e.build_set([source], ["test"], out, log=lambda _: None)
        e.build_set([source], ["test"], out, log=lambda _: None, replace=True)
        self.assertEqual(sorted(p.name for p in out.iterdir()), ["DOA5MissingContent.zip"])

class EnglishNamesTests(unittest.TestCase):
    def test_french_recipe_groups_are_shown_and_accepted_in_english(self):
        recipe = e.read_recipe()
        groups = {j["group"] for j in recipe["jobs"]}
        self.assertTrue(set(e.GROUP_NAMES) <= groups)
        for internal, name in e.GROUP_NAMES.items():
            self.assertEqual(e.shown(internal), name)
            self.assertEqual(e.choose(recipe, [name]), e.choose(recipe, [internal]))
        for job in recipe["jobs"]:
            self.assertNotRegex(e.shown(job["id"]), "classiques|particules")
        with self.assertRaisesRegex(ValueError, "Unknown group: nothing"):
            e.choose(recipe, ["nothing"])

    def test_bundled_old_recipes_are_still_known(self):
        names = sorted(p.name for p in (e.HOME / "recipes").glob("*.json"))
        self.assertEqual(len(names), 4)
        self.assertFalse([n for n in names if "prototype" in n])

if __name__ == "__main__":
    unittest.main(verbosity=2)
