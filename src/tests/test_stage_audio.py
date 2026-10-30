import hashlib
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "outil"))
import extracteur as e
sys.path.insert(0, str(e.HOME / "converters"))
import stage_audio
import dnz_preview

SHA_DLL = "fe82f1ae13a798337e03942c35351997c23caadb5eb0c6b7a2dc2a521ed8b02e"
GROUPES = {"crimson1", "crimson2", "danger-zone", "fireworks", "azuchi1", "azuchi2", "titan"}


class StageAudioRecipeTests(unittest.TestCase):
    def test_jobs_in_recipe(self):
        recipe = e.read_recipe()
        jobs = [j for j in recipe["jobs"] if j["operation"] == "stage_audio"]
        self.assertEqual({j["group"] for j in jobs}, GROUPES)
        self.assertEqual(len(jobs), 7)
        modele = None
        for job in jobs:
            self.assertTrue(job["installable"])
            self.assertFalse(job.get("intermediate"))
            self.assertEqual(len(job["outputs"]), 1)
            self.assertRegex(job["outputs"][0]["path"], r"^maps/.+\.(xws|XWS)$")
            for key in job["inputs"]:
                self.assertEqual(recipe["sources"][key]["sha256"], key)
            modele = modele or job["inputs"][1]
            self.assertEqual(job["inputs"][1], modele)
            if job["group"] in ("azuchi1", "azuchi2"):
                self.assertEqual(len(job["inputs"]), 3)
                self.assertEqual(job["append_program"], 4)
                self.assertEqual(recipe["sources"][job["inputs"][2]]["catalog"], "stage_common.bin")
            else:
                self.assertEqual(len(job["inputs"]), 2)
                self.assertNotIn("append_program", job)

    def test_installer_format_accepts_sounds(self):
        recipe = e.read_recipe()
        info = e.manifest(recipe, e.choose(recipe, sorted(GROUPES)))
        files = e.installer_manifest(info)["files"]
        self.assertIn("maps/PS4Stages/TITAN/TITAN.XWS", files)

    def test_decoder_bundled(self):
        dll = e.HOME / "tools" / "libatrac9.dll"
        if not dll.is_file():
            self.skipTest("libatrac9.dll not in outil/tools (binaries are not in the public sources)")
        self.assertEqual(hashlib.sha256(dll.read_bytes()).hexdigest(), SHA_DLL)
        self.assertIn("MIT License", (e.HOME / "tools" / "LibAtrac9-LICENSE.txt").read_text())


class StageAudioCodecTests(unittest.TestCase):
    def signal(self, n, rate):
        t = np.arange(n) / rate
        return np.round(12000 * np.sin(2 * np.pi * 440 * t) + 3000 * np.sin(2 * np.pi * 3100 * t)).astype(np.int64)

    def test_msadpcm_round_trip(self):
        x = self.signal(1500, 32000)
        data, n = stage_audio.msadpcm_encoder(x)
        self.assertEqual(n, 1500)
        self.assertEqual(len(data), 256 * 3)
        y = stage_audio.msadpcm_decoder(data, 256, 500)[:n]
        self.assertEqual(len(y), n)
        self.assertLess(np.abs(y - x).mean(), 300)

    def test_resample_lengths(self):
        x = self.signal(4800, 48000)
        self.assertIs(stage_audio.reechantillonner(x, 32000), x)
        y = stage_audio.reechantillonner(x, 48000)
        self.assertLessEqual(abs(len(y) - 3200), 40)
        z = stage_audio.reechantillonner(self.signal(3000, 31997), 31997)
        self.assertLessEqual(abs(len(z) - 3000), 40)
        self.assertLessEqual(np.abs(z).max(), 32767)

    def test_rejects_wrong_sources(self):
        with self.assertRaises(ValueError):
            stage_audio.banque_stage([b"XWSFILE"], "unused.dll")
        with self.assertRaises(ValueError):
            stage_audio.banque_stage([b"a", b"b", b"c"], "unused.dll")
        with self.assertRaises(ValueError):
            stage_audio.construire(b"XWSFILE" + bytes(0x100), b"not a bank", "unused.dll")


class DnzPreviewTests(unittest.TestCase):
    def table(self):
        b = bytearray(struct.pack("<Q", 0x10))
        b += bytes(0x10 - len(b))
        listes = 0x10 + 5 * 16
        for g in range(5):
            b += struct.pack("<QQ", 2, listes + 16 * g)
        scripts = listes + 5 * 16
        for g in range(5):
            b += struct.pack("<QQ", scripts, scripts + 12)
        b += struct.pack("<HHI", 0x0B, 2, 300) + struct.pack("<HH", 0x0C, 1)
        b += struct.pack("<HH", 0x26, 1)
        return bytes(b)

    def test_blocks(self):
        blocs = dnz_preview.pistes(self.table())
        self.assertEqual(len(blocs), 10)
        self.assertEqual(blocs["Group0Track0"], struct.pack("<HHIHH", 0x0B, 2, 300, 0x0C, 1))
        self.assertEqual(blocs["Group4Track1"], struct.pack("<HH", 0x26, 1))
        d = dnz_preview.apercu(self.table())
        self.assertEqual(d[:12], b"D5SD" + struct.pack("<II", 1, 10))

    def test_rejects_unterminated_script(self):
        with self.assertRaises(ValueError):
            dnz_preview.pistes(self.table()[:-4])

    def test_job_in_recipe(self):
        recipe = e.read_recipe()
        job = next(j for j in recipe["jobs"] if j["operation"] == "dnz_preview")
        self.assertEqual(job["group"], "danger-zone")
        self.assertEqual(job["outputs"][0]["path"], "maps/DOA5Stages/DNZPreview.d5sd")
        self.assertEqual(recipe["sources"][job["inputs"][0]]["size"], 916)


class SoftEngineLRTests(unittest.TestCase):
    def test_job_in_recipe(self):
        recipe = e.read_recipe()
        jobs = e.choose(recipe, ["softengine-lr"])
        self.assertEqual([j["operation"] for j in jobs], ["softengine_lr"])
        source = recipe["sources"][jobs[0]["inputs"][0]]
        self.assertEqual(source["file"], "eboot.bin")
        self.assertNotIn("catalog", source)
        self.assertEqual(jobs[0]["outputs"][0]["path"], "maps/DOA5Stages/SoftEngineLR.d5sd")
        self.assertEqual(e.split_groups(["softengine-lr"]), {"stages": ["softengine-lr"]})

    def test_rejects_other_files(self):
        import softengine_lr
        with self.assertRaises(ValueError):
            softengine_lr.donnees(b"not an eboot")

    @unittest.skipUnless(os.environ.get("DOA5LR_PS4"), "Set DOA5LR_PS4 for the real eboot")
    def test_real_eboot(self):
        recipe = e.read_recipe()
        job = e.choose(recipe, ["softengine-lr"])[0]
        sources = e.Sources([os.environ["DOA5LR_PS4"]], {job["inputs"][0]: recipe["sources"][job["inputs"][0]]},
                            log=lambda _: None)
        data = e.convert(job["operation"], [sources.read(job["inputs"][0])], ".", None, job)[0]
        self.assertEqual(e.digest(data), job["outputs"][0]["sha256"])


@unittest.skipUnless(os.environ.get("DOA5LR_PC_GAME") and os.environ.get("DOA5LR_PS4"),
                     "Set DOA5LR_PC_GAME and DOA5LR_PS4 for the real sound banks")
class StageAudioRealTests(unittest.TestCase):
    def test_build_banks(self):
        recipe = e.read_recipe()
        jobs = [j for j in recipe["jobs"] if j["id"] in ("danger-zone-audio", "azuchi1-audio")]
        wanted = {k: recipe["sources"][k] for j in jobs for k in j["inputs"]}
        sources = e.Sources([os.environ["DOA5LR_PC_GAME"], os.environ["DOA5LR_PS4"]], wanted, log=lambda _: None)
        with tempfile.TemporaryDirectory() as t:
            for job in jobs:
                data = e.convert(job["operation"], [sources.read(k) for k in job["inputs"]], t, None, job)[0]
                spec = job["outputs"][0]
                self.assertEqual((len(data), e.digest(data)), (spec["size"], spec["sha256"]))
                self.assertEqual(struct.unpack_from("<I", data, 0x30)[0], 0x800)


if __name__ == "__main__":
    unittest.main()
