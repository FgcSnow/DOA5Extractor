import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "outil"))
import extracteur as e
sys.path.insert(0, str(e.HOME / "converters"))
import common_lnk

RECETTE_04 = "018a923b5c4f9f4ae5d5b10249870b69c892db1a085ef81d9f64c6d021b5a895"


class CommonLnkRecipeTests(unittest.TestCase):
    def test_job_in_recipe(self):
        recipe = e.read_recipe()
        jobs = e.choose(recipe, ["costumes-particules"])
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["operation"], "common_lnk")
        self.assertTrue(job["installable"])
        self.assertEqual(len(job["inputs"]), 4)
        self.assertEqual(job["outputs"], [{"path": "common.lnk", "size": common_lnk.TAILLE_SORTIE,
                                           "sha256": common_lnk.SHA_SORTIE}])
        for key in job["inputs"]:
            self.assertEqual(recipe["sources"][key]["sha256"], key)

    def test_previous_recipe_kept(self):
        old = e.HOME / "recipes" / "doa5lr-ps4-local-20261020-v2-924.json"
        self.assertEqual(e.read_recipe(old)["id"], "doa5lr-ps4-local-20261020-v2-924")
        self.assertEqual(e.file_hash(old), RECETTE_04)
        new = {j["id"] for j in e.read_recipe()["jobs"]}
        self.assertTrue({j["id"] for j in e.read_recipe(old)["jobs"]} <= new)

    def test_mixed_groups_refused(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaisesRegex(ValueError, "separate ZIP"):
                e.build([], ["costumes-particules", "titan"], Path(t) / "out.zip")
            self.assertFalse((Path(t) / "out.zip").exists())

    def test_wrong_archive_refused(self):
        with tempfile.TemporaryDirectory() as t:
            fake = Path(t) / "common.lnk"
            fake.write_bytes(b"not the game")
            with self.assertRaisesRegex(ValueError, "original Steam"):
                common_lnk.construire(fake, [b"", b""], [b"", b""], Path(t) / "out")
            self.assertFalse((Path(t) / "out" / "common.lnk").exists())

    def test_already_modified_detected_by_size_only_first(self):
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "common.lnk").write_bytes(b"small")
            self.assertFalse(common_lnk.deja_modifie([t]))


@unittest.skipUnless(all(os.environ.get(v) for v in ("DOA5LR_COMMON_PC", "DOA5LR_COMMON_PS4", "DOA5LR_KEY")),
                     "DOA5LR_COMMON_PC, DOA5LR_COMMON_PS4 and DOA5LR_KEY required (player's local files)")
class CommonLnkRealTests(unittest.TestCase):

    def test_build_verify_import_restore(self):
        pc = Path(os.environ["DOA5LR_COMMON_PC"])
        with tempfile.TemporaryDirectory() as t, patch.object(e, "game_closed", lambda: None):
            game = Path(t) / "game"
            game.mkdir()
            (game / "game.exe").write_bytes(b"fixture")
            for name in ("common.bin", "common.lnk"):
                shutil.copyfile(pc / name, game / name)
            out = Path(t) / "particles.zip"
            e.build([str(game), os.environ["DOA5LR_COMMON_PS4"]], ["costumes-particules"], out,
                    log=lambda _: None, key_file=os.environ["DOA5LR_KEY"])
            info = e.verify(out, log=lambda _: None)
            self.assertTrue(info["installable"])
            self.assertEqual([f["path"] for f in info["files"]], ["common.lnk"])
            e.install(out, game, log=lambda _: None)
            self.assertEqual(e.file_hash(game / "common.lnk"), common_lnk.SHA_SORTIE)
            with self.assertRaisesRegex(ValueError, "already the costume particles"):
                e.build([str(game), os.environ["DOA5LR_COMMON_PS4"]], ["costumes-particules"],
                        Path(t) / "again.zip", log=lambda _: None, key_file=os.environ["DOA5LR_KEY"])
            state = next((game / "DOA5LR-PS4-Backups").glob("*/state.json"))
            e.restore_state(state)
            self.assertEqual(e.file_hash(game / "common.lnk"), common_lnk.SHA_PC)
            self.assertEqual(json.loads(state.read_text(encoding="utf-8"))["status"], "restored")


if __name__ == "__main__":
    unittest.main()
