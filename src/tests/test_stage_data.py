import os
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "outil"))
import extracteur as e
sys.path.insert(0, str(e.HOME / "converters"))
import crimson_vfx
import extrastages_d5sd
import x64emu


def lancer(code, avant=None):
    image = bytearray(0x3000); image[0x1000:0x1000 + len(code)] = code
    n = x64emu.Natif(bytes(image), {})
    if avant:
        avant(n.mc)
    n.appeler(0x1000)
    return n.mc


class InterpreteurTests(unittest.TestCase):
    def test_entiers_et_sauts(self):
        mc = lancer(bytes.fromhex("b805000000" "83c0fa" "7505" "b809000000" "0f94c1" "0fb6c9" "c3"))
        self.assertEqual(mc.r["rax"], 0xFFFFFFFF)
        self.assertEqual(mc.r["rcx"], 0)
        self.assertTrue(mc.cf is False and mc.sf)

    def test_pile_et_appel(self):
        mc = lancer(bytes.fromhex("6a07" "e801000000" "c3" "58" "5b" "50" "c3"))
        self.assertEqual((mc.r["rbx"], mc.r["rax"]), (7, 0x1007))

    def test_flottants_exacts(self):
        def prep(mc):
            mc.m.ecrire(0x2000, struct.pack("<4f", 1.1, 2.2, -3.5, 1e30) + struct.pack("<4f", 3.3, 1e-38, 0.5, 1e30))
        mc = lancer(bytes.fromhex("c5f8280425" "00200000" "c5f8590c25" "10200000" "c5fa5bd1" "c3"), prep)
        import numpy as np
        a = np.array([1.1, 2.2, -3.5, 1e30], np.float32); b = np.array([3.3, 1e-38, 0.5, 1e30], np.float32)
        self.assertEqual(bytes(mc.y[1][:16]), (a * b).tobytes())
        self.assertEqual(struct.unpack("<4i", bytes(mc.y[2][:16])), (3, 0, -1, -2147483648))
        self.assertEqual(bytes(mc.y[1][16:]), bytes(16))

    def test_instruction_inconnue_refusee(self):
        with self.assertRaises(x64emu.Arret):
            lancer(bytes.fromhex("0fa2" "c3"))


class StageDataRecipeTests(unittest.TestCase):
    def test_jobs(self):
        recipe = e.read_recipe()
        jobs = {j["operation"]: j for j in e.choose(recipe, ["stage-data"])}
        self.assertEqual(set(jobs), {"extrastages_d5sd", "crimson_vfx"})
        self.assertEqual(len(jobs["extrastages_d5sd"]["inputs"]), 10)
        files = [recipe["sources"][k].get("file") for j in jobs.values() for k in j["inputs"]]
        self.assertEqual(files.count("eboot.bin"), 2)
        self.assertEqual(e.split_groups(["stage-data"]), {"stages": ["stage-data"]})
        self.assertEqual(sorted(o["path"] for j in jobs.values() for o in j["outputs"]),
                         ["maps/DOA5Stages/CrimsonVFX.d5sd", "maps/DOA5Stages/ExtraStages.d5sd"])

    def test_crimson_spec(self):
        noms = {"G%d_r" % ev for ev, _ in crimson_vfx.NOMS}
        listes = {n for n, _, _ in crimson_vfx.LISTES}
        self.assertEqual(len(listes), len(crimson_vfx.LISTES))
        for ev, _, groupes, _, _ in crimson_vfx.PARTIES:
            self.assertTrue(1 <= len(groupes) <= 4)
            for _, _, g in groupes:
                self.assertIn(g, listes)
                self.assertTrue(any(g.startswith(p) for p in noms))

    def test_ecriture_pointeurs(self):
        tables = {nom: bytes(el) for nom, el in extrastages_d5sd.ORDRE}
        tables["TitanNames"] = (bytes(8), {0: b"A", 4: b"B"})
        tables["TITANNames"] = (bytes(8), {0: b"B", 4: b"C"})
        d = extrastages_d5sd.ecrire(tables)
        self.assertEqual(d[:4], b"D5SD")
        self.assertTrue(d.endswith(b"B\0C\0A\0"))
        self.assertIn(b"TitanNames.ptr", d)


@unittest.skipUnless(os.environ.get("DOA5LR_PC_GAME") and os.environ.get("DOA5LR_PS4"),
                     "Set DOA5LR_PC_GAME and DOA5LR_PS4 for the real stage data")
class StageDataRealTests(unittest.TestCase):
    def test_build(self):
        recipe = e.read_recipe(); jobs = e.choose(recipe, ["stage-data"])
        wanted = {k: recipe["sources"][k] for j in jobs for k in j["inputs"]}
        sources = e.Sources([os.environ["DOA5LR_PC_GAME"], os.environ["DOA5LR_PS4"]], wanted, log=lambda _: None)
        for job in jobs:
            data = e.convert(job["operation"], [sources.read(k) for k in job["inputs"]], ".", None, job)[0]
            spec = job["outputs"][0]
            self.assertEqual((len(data), e.digest(data)), (spec["size"], spec["sha256"]))


if __name__ == "__main__":
    unittest.main()
