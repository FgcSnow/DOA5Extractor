from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "outil"))
import extracteur as core


class ArchiveStreamingTests(unittest.TestCase):
    def test_streamed_pack_is_byte_identical_and_source_indexable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            generated = root / "generated"
            generated.mkdir()
            payloads = [b"first resource", bytes(range(256)) * 17]
            resources = []
            for i, data in enumerate(payloads):
                name = f"{i}.bin"
                (generated / name).write_bytes(data)
                resources.append(dict(path=name, asset_id=41+i,
                                      asset_name=f"PS4SHARED02_990016_{41+i:05d}"))
            job = dict(archive_id=990016, resources=resources,
                       costumes=[dict(character_id=2, dlcu=7, details=1,
                                      hair_face_pairs=[[1, 1], [2, 1]])])
            expected = core.make_archive(job, generated)
            with patch.object(Path, "read_bytes", side_effect=AssertionError("whole-file read")):
                actual = core.make_archive(job, generated, root / "archive")
            self.assertEqual(expected[:3], actual[:3])
            self.assertEqual(expected[3], actual[3].read_bytes())
            self.assertEqual(struct.unpack_from("<Q", expected[3], 16)[0], 10240)
            for ext, data in zip(("bcm", "bin", "blp"), actual[:3]):
                (actual[3].parent / ("990016." + ext)).write_bytes(data)
            wanted = {str(i): dict(catalog="990016.bin", name=r["asset_name"],
                                  size=len(payloads[i]), sha256=core.digest(payloads[i]))
                      for i, r in enumerate(resources)}
            indexed = core.Sources([actual[3].parent], wanted, log=lambda _: None)
            self.assertEqual([indexed.read(str(i)) for i in range(2)], payloads)
            for i, data in enumerate(payloads):
                offset, size, original_size, flags = struct.unpack_from("<4Q", expected[3], 32+i*32)
                self.assertEqual(offset % 2048, 0)
                self.assertEqual((size, original_size, flags), (len(data), len(data), 0))
                self.assertEqual(expected[3][offset:offset+size], data)
            archive = root / "test.zip"
            with zipfile.ZipFile(archive, "w") as z:
                core.zip_add(z, "DLC/990016/990016.lnk", actual[3])
            with zipfile.ZipFile(archive) as z:
                self.assertEqual(z.read("DLC/990016/990016.lnk"), expected[3])
                self.assertEqual(z.getinfo("DLC/990016/990016.lnk").date_time,
                                 (1980, 1, 1, 0, 0, 0))


if __name__ == "__main__":
    unittest.main()
