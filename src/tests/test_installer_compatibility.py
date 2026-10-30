import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "outil"))
import extracteur as core

INSTALLER_SOURCE = Path(os.environ.get(
    "DOA5LR_INSTALLER_SOURCE", ROOT / "installeur"))
REAL_ZIP = os.environ.get("DOA5LR_REAL_STAGE_ZIP")
WINDOWS = os.name == "nt"

HARNESS = r'''
#include "@SOURCE@"
int wmain(int argc, wchar_t **argv)
{
    if (argc != 5) return 2;
    if (wcslen(argv[2]) >= MAX_PATH || wcslen(argv[3]) >= MAX_PATH - 2 ||
        wcslen(argv[4]) >= MAX_PATH - 2) return 2;
    wcscpy(g_zip, argv[2]);
    wcscpy(g_game, argv[3]); wcscat(g_game, L"\\");
    wcscpy(g_tool, argv[4]); wcscat(g_tool, L"\\");
    wcscpy(g_exeDir, g_game);
    /* 1.3 : modules livres avec l'exe, chemin fixe par wWinMain (remplace ici) */
    wcscpy(g_mods, g_tool); wcscat(g_mods, L"modules\\");
    WCHAR marker[MAX_PATH];
    if (!Join(marker, g_game, L".installer-test-fixture") || !Exists(marker)) return 2;
    InitializeCriticalSection(&g_csStatus);
    Chemins(C_CONTENT);
    const WCHAR *err = NULL;
    int nf = 0, nm = 0, rc = 0;
    if (!wcscmp(argv[1], L"recognize")) {
        WCHAR info[160]; int official = 0;
        int kind = Reconnaitre(g_zip, info, &official);
        wprintf(L"%ls\n", info);
        if (kind != C_CONTENT) rc = 1;
    } else if (!wcscmp(argv[1], L"install")) {
        err = Install(&nf, &nm);
        CleanTemp();
        if (g_rec) { fclose(g_rec); g_rec = NULL; }
        printf("files=%d modules=%d\n", nf, nm);
    } else if (!wcscmp(argv[1], L"uninstall")) {
        err = Uninstall(&nf);
        printf("removed=%d\n", nf);
    } else rc = 2;
    if (err) { wprintf(L"ERROR: %ls\n", err); rc = 1; }
    ZipClose();
    if (g_files) { HeapFree(GetProcessHeap(), 0, g_files); g_files = NULL; }
    DeleteCriticalSection(&g_csStatus);
    return rc;
}
'''


def load_installer():
    spec = importlib.util.spec_from_file_location(
        "doa5installer_unmodified", INSTALLER_SOURCE / "doa5installer.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InstallerEngines:

    @classmethod
    def setUpClass(cls):
        if not (INSTALLER_SOURCE / "doa5installer.py").is_file():
            raise unittest.SkipTest("DOA5Installer source directory is not available")
        if not hasattr(core, "export_installer"):
            raise AssertionError("extracteur.export_installer is required")
        cls.installer = load_installer()
        cls.harness_dir = tempfile.TemporaryDirectory(prefix="doa5-installer-engine-")
        cls.addClassCleanup(cls.harness_dir.cleanup)
        cls.c_engine = None
        compiler = shutil.which("i686-w64-mingw32-gcc")
        if WINDOWS and compiler:
            folder = Path(cls.harness_dir.name)
            source = folder / "engine.c"
            source.write_text(HARNESS.replace(
                "@SOURCE@", (INSTALLER_SOURCE / "doa5installer.c").as_posix()),
                encoding="utf-8")
            cls.c_engine = folder / "engine.exe"
            result = subprocess.run(
                [compiler, "-O2", "-static", "-municode", "-o",
                 str(cls.c_engine), str(source),
                 *([str(INSTALLER_SOURCE / "controls.c")] if (INSTALLER_SOURCE / "controls.c").is_file() else []),
                 "-lbcrypt", "-lcomdlg32",
                 "-lshell32", "-lole32", "-lgdi32", "-luser32", "-lwinmm"],
                capture_output=True, text=True, timeout=90,
                **({"creationflags": subprocess.CREATE_NO_WINDOW} if WINDOWS else {}))
            if result.returncode:
                raise AssertionError("Original C installer failed to compile:\n" +
                                     result.stdout + result.stderr)

    def make_game(self, label, outputs):
        game = self.root / label
        game.mkdir()
        (game / ".installer-test-fixture").write_text("isolated installer test")
        (game / "game.exe").write_bytes(b"inert fixture; never executed")
        tool = game / "DOA5Installer"
        scripts = tool / "modules" / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "DOA5LR-Maps.asi").write_bytes(b"inert module; never loaded")
        target = game / outputs[0]["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"pre-existing resource to restore")
        unrelated = game / "unrelated.txt"
        unrelated.write_bytes(b"must survive both operations")
        return game, tool, target, unrelated

    def run_c(self, command, archive, game, tool, expected=0):
        self.assertTrue(game.resolve().is_relative_to(self.root.resolve()))
        self.assertTrue(tool.resolve().is_relative_to(self.root.resolve()))
        result = subprocess.run(
            [str(self.c_engine), command, str(archive), str(game), str(tool)],
            capture_output=True, text=True, timeout=180,
            creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, expected,
                         msg=result.stdout + result.stderr)
        return result.stdout

    def assert_files(self, game, outputs):
        for spec in outputs:
            target = game / spec["path"]
            self.assertTrue(target.is_file(), spec["path"])
            self.assertEqual(target.stat().st_size, spec["size"])
            self.assertEqual(core.file_hash(target), spec["sha256"])

    def assert_restored(self, game, outputs, target, unrelated):
        self.assertEqual(target.read_bytes(), b"pre-existing resource to restore")
        self.assertEqual(unrelated.read_bytes(), b"must survive both operations")
        for spec in outputs[1:]:
            self.assertFalse((game / spec["path"]).exists(), spec["path"])
        self.assertFalse((game / "scripts" / "DOA5LR-Maps.asi").exists())
        self.assertFalse(list(game.rglob("*.d5p-tmp")))

    def install_restore_python(self, archive, outputs, label):
        game, tool, target, unrelated = self.make_game(label, outputs)
        with patch.object(self.installer, "TOOL", str(tool)), \
                patch.object(self.installer, "PATCHER", str(game / "DOA5Patcher")), \
                contextlib.redirect_stdout(io.StringIO()):
            component, _, recognized = self.installer.reconnaitre(str(archive))
            self.assertEqual(component, "content")
            self.assertTrue(recognized)
            self.installer.installer_contenu(str(game), str(archive))
            self.assert_files(game, outputs)
            self.assertEqual(
                (tool / "backup" / "content" / outputs[0]["path"]).read_bytes(),
                b"pre-existing resource to restore")
            self.installer.uninstall(str(game), "content")
            self.assert_restored(game, outputs, target, unrelated)

    def install_restore_c(self, archive, outputs, label):
        if not self.c_engine:
            self.skipTest("Original C engine requires Windows and LLVM-MinGW")
        game, tool, target, unrelated = self.make_game(label, outputs)
        self.run_c("recognize", archive, game, tool)
        message = self.run_c("install", archive, game, tool)
        self.assertIn("files=%d modules=1" % len(outputs), message)
        self.assert_files(game, outputs)
        self.assertEqual(
            (tool / "backup" / "content" / outputs[0]["path"]).read_bytes(),
            b"pre-existing resource to restore")
        self.run_c("uninstall", archive, game, tool)
        self.assert_restored(game, outputs, target, unrelated)


class InstallerCompatibilityTests(InstallerEngines, unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="doa5-installer-fixture-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.payload = b"converted stage fixture"
        self.outputs = [
            {"path": "maps/PS4Stages/test.TMC", "size": len(self.payload),
             "sha256": hashlib.sha256(self.payload).hexdigest()},
            {"path": "maps/CodexCrimson/test.TMCL", "size": len(self.payload),
             "sha256": hashlib.sha256(self.payload).hexdigest()},
        ]
        self.recipe = {
            "format": 1, "id": "installer-compatibility-fixture", "sources": {},
            "jobs": [{"id": "stage", "group": "stage", "operation": "copy",
                      "inputs": [], "outputs": self.outputs, "installable": True}]
        }
        recipe_file = self.root / "recipe.json"
        recipe_file.write_text(json.dumps(self.recipe), encoding="utf-8")
        self.recipe_file = recipe_file
        self.recipe_patch = patch.object(core, "RECIPE", recipe_file)
        self.recipe_patch.start()
        self.addCleanup(self.recipe_patch.stop)
        self.internal = self.root / "internal.zip"
        self.write_internal()

    def write_internal(self, payload=None, forged=False):
        expected = core.manifest(self.recipe, self.recipe["jobs"])
        if forged:
            expected["files"] = [
                dict(spec, sha256=hashlib.sha256(payload).hexdigest())
                for spec in expected["files"]]
        with zipfile.ZipFile(self.internal, "w") as z:
            for spec in self.outputs:
                core.zip_add(z, spec["path"], self.payload if payload is None else payload)
            core.zip_add(z, "manifest.json", json.dumps(expected).encode("ascii"))

    def export(self):
        destination = self.root / "DOA5MissingContent.zip"
        core.export_installer(self.internal, destination, log=lambda _: None)
        return destination

    def test_export_installs_and_uninstalls_in_original_python(self):
        self.install_restore_python(self.export(), self.outputs, "python-game")

    def test_export_installs_and_uninstalls_in_original_c(self):
        self.install_restore_c(self.export(), self.outputs, "c-game")

    def test_manifest_and_zip_limits_and_trusted_verification(self):
        exported = self.export()
        verified = core.verify(exported, log=lambda _: None)
        self.assertEqual(verified["files"], sorted(self.outputs, key=lambda x: x["path"]))
        with zipfile.ZipFile(exported) as z:
            raw = z.read("manifest.json")
            raw.decode("ascii")
            m = json.loads(raw)
            self.assertEqual(m["format"], "doa5pack/1")
            self.assertEqual(m["name"], core.PACK_NAME)
        for name in (core.PACK_NAME, *core.OLD_PACK_NAMES):
            self.assertRegex(name, r"^[ -~]{1,63}$")
        with zipfile.ZipFile(exported) as z:
            m = json.loads(z.read("manifest.json"))
            self.assertIsInstance(m["files"], dict)
            self.assertLessEqual(len(m["name"]), 63)
            self.assertLess(len(raw), 1 << 20)
            self.assertLessEqual(len(z.infolist()), 4096)
            self.assertEqual(set(z.namelist()), {o["path"] for o in self.outputs} | {"manifest.json"})
            for info in z.infolist():
                self.assertIn(info.compress_type, (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED))
                self.assertLessEqual(info.file_size, 512 << 20)
                self.assertLessEqual(info.compress_size, 512 << 20)
                self.assertFalse(info.flag_bits & 1)
                self.assertLess(info.extract_version, 45)

    def rename_pack(self, name):
        exported = self.export()
        renamed = self.root / "renamed.zip"
        with zipfile.ZipFile(exported) as src, zipfile.ZipFile(renamed, "w") as dst:
            for info in src.infolist():
                data = src.read(info.filename)
                if info.filename == "manifest.json":
                    m = json.loads(data)
                    m["name"] = name
                    data = json.dumps(m, sort_keys=True, indent=2).encode("ascii")
                core.zip_add(dst, info.filename, data)
        exported.unlink()
        return renamed

    def test_verify_accepts_old_pack_name_and_rejects_others(self):
        for name in core.OLD_PACK_NAMES:
            core.verify(self.rename_pack(name), log=lambda _: None)
            (self.root / "renamed.zip").unlink()
        with self.assertRaisesRegex(ValueError, "differs"):
            core.verify(self.rename_pack("Some other pack"), log=lambda _: None)

    def test_verify_rejects_installer_zip_recompressed_with_bzip2(self):
        exported = self.export()
        repacked = self.root / "bzip2.zip"
        with zipfile.ZipFile(exported) as src, \
                zipfile.ZipFile(repacked, "w", compression=zipfile.ZIP_BZIP2) as dst:
            for name in src.namelist():
                dst.writestr(name, src.read(name))
        with zipfile.ZipFile(repacked) as z:
            self.assertIsNone(z.testzip())
            self.assertEqual(z.read(self.outputs[0]["path"]), self.payload)
        with self.assertRaisesRegex(ValueError, "compression|incompatible"):
            core.verify(repacked, log=lambda _: None)

    def test_verify_rejects_forced_zip64_installer_container(self):
        exported = self.export()
        repacked = self.root / "zip64.zip"
        with zipfile.ZipFile(exported) as src, patch.object(zipfile, "ZIP64_LIMIT", 1):
            with zipfile.ZipFile(repacked, "w", allowZip64=True) as dst:
                for name in src.namelist():
                    dst.writestr(name, src.read(name))
        self.assertIn(b"PK\x06\x06", repacked.read_bytes())
        with zipfile.ZipFile(repacked) as z:
            self.assertIsNone(z.testzip())
            self.assertEqual(z.read(self.outputs[0]["path"]), self.payload)
        with self.assertRaisesRegex(ValueError, "ZIP64|incompatible"):
            core.verify(repacked, log=lambda _: None)

    def test_verify_rejects_installer_manifest_over_one_megabyte(self):
        exported = self.export()
        repacked = self.root / "oversize-manifest.zip"
        with zipfile.ZipFile(exported) as src, zipfile.ZipFile(repacked, "w") as dst:
            for name in src.namelist():
                data = src.read(name)
                if name == "manifest.json":
                    data += b" " * (1 << 20)
                dst.writestr(name, data)
        with zipfile.ZipFile(repacked) as z:
            self.assertIsNone(z.testzip())
            self.assertGreater(z.getinfo("manifest.json").file_size, 1 << 20)
        with self.assertRaises(ValueError):
            core.verify(repacked, log=lambda _: None)

    def test_export_rejects_forged_source_manifest(self):
        self.write_internal(payload=b"x" * len(self.payload), forged=True)
        with self.assertRaises(ValueError):
            self.export()
        self.assertFalse((self.root / "DOA5MissingContent.zip").exists())

    def test_export_rejects_noninstallable_costumes(self):
        self.recipe["jobs"][0]["installable"] = False
        self.recipe_file.write_text(json.dumps(self.recipe), encoding="utf-8")
        self.write_internal()
        with self.assertRaises(ValueError):
            self.export()
        self.assertFalse((self.root / "DOA5MissingContent.zip").exists())

    def test_export_does_not_overwrite_existing_destination(self):
        destination = self.root / "DOA5MissingContent.zip"
        destination.write_bytes(b"user's existing zip")
        with self.assertRaises((ValueError, FileExistsError)):
            core.export_installer(self.internal, destination, log=lambda _: None)
        self.assertEqual(destination.read_bytes(), b"user's existing zip")

    def test_both_installers_reject_modified_payload_before_replacement(self):
        exported = self.export()
        damaged = self.root / "damaged.zip"
        with zipfile.ZipFile(exported) as src, zipfile.ZipFile(damaged, "w") as dst:
            for info in src.infolist():
                data = src.read(info)
                if info.filename == self.outputs[0]["path"]:
                    data = b"x" * len(data)
                dst.writestr(info, data)
        with self.assertRaises(ValueError):
            core.verify(damaged, log=lambda _: None)
        game, tool, target, unrelated = self.make_game("python-damaged", self.outputs)
        with patch.object(self.installer, "TOOL", str(tool)), \
                patch.object(self.installer, "PATCHER", str(game / "DOA5Patcher")), \
                contextlib.redirect_stdout(io.StringIO()), \
                self.assertRaisesRegex(self.installer.Erreur, "SHA-256"):
            self.installer.installer_contenu(str(game), str(damaged))
        self.assert_restored(game, self.outputs, target, unrelated)
        self.assertFalse((tool / "installed-content.txt").exists())
        if self.c_engine:
            game, tool, target, unrelated = self.make_game("c-damaged", self.outputs)
            output = self.run_c("install", damaged, game, tool, expected=1)
            self.assertIn("SHA-256", output)
            self.assert_restored(game, self.outputs, target, unrelated)
            self.assertFalse((tool / "installed-content.txt").exists())

    def test_both_installers_require_their_stage_module_bundle(self):
        exported = self.export()
        for engine in ("python", "c"):
            if engine == "c" and not self.c_engine:
                continue
            game, tool, target, unrelated = self.make_game(engine + "-missing", self.outputs)
            (tool / "modules" / "scripts" / "DOA5LR-Maps.asi").unlink()
            if engine == "python":
                with patch.object(self.installer, "TOOL", str(tool)), \
                        patch.object(self.installer, "PATCHER", str(game / "DOA5Patcher")), \
                        self.assertRaisesRegex(self.installer.Erreur, r"modules/scripts|DOA5LR-Maps\.asi"):
                    self.installer.installer_contenu(str(game), str(exported))
            else:
                self.assertIn("modules", self.run_c("install", exported, game, tool, expected=1))
            self.assert_restored(game, self.outputs, target, unrelated)


@unittest.skipUnless(REAL_ZIP, "Set DOA5LR_REAL_STAGE_ZIP for real-resource integration")
class InstallerRealStageTests(InstallerEngines, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmp = tempfile.TemporaryDirectory(prefix="doa5-installer-real-")
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.root = Path(cls.tmp.name)
        cls.exported = cls.root / "DOA5MissingContent.zip"
        manifest = core.verify(Path(REAL_ZIP), log=lambda _: None)
        if not manifest["installable"]:
            raise AssertionError("Real integration input must contain stages only")
        cls.outputs = manifest["files"]
        core.export_installer(Path(REAL_ZIP), cls.exported, log=lambda _: None)

    def test_real_stage_zip_installs_and_restores_in_original_python(self):
        self.install_restore_python(self.exported, self.outputs, "python-game")

    def test_real_stage_zip_installs_and_restores_in_original_c(self):
        self.install_restore_c(self.exported, self.outputs, "c-game")


if __name__ == "__main__":
    unittest.main(verbosity=2)
