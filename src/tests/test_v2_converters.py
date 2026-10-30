from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "outil/converters"))
import v2_alternates as alternates
import v2_normals as normals


def put(data, offset, value):
    struct.pack_into("<I", data, offset, value)


def raw_dds(pixels, width=2, height=2, depth=1, mips=1):
    header = bytearray(128)
    header[:4] = b"DDS "
    fields = {
        4: 124, 8: 0x100F, 12: height, 16: width, 20: width * 4,
        24: depth if depth > 1 else 0, 28: mips, 76: 32, 80: 65,
        88: 32, 92: 255, 96: 65280, 100: 16711680,
        104: 4278190080, 108: 0x1000, 112: 0x200000 if depth > 1 else 0,
    }
    for offset, value in fields.items():
        put(header, offset, value)
    return bytes(header) + b"".join(bytes(pixel) for pixel in pixels)


def chunk(tag, children):
    header_size = (48 + 4 * len(children) + 15) & ~15
    data = bytearray(header_size)
    data[:len(tag)] = tag
    put(data, 24, len(children))
    put(data, 32, 48)
    for index, child in enumerate(children):
        put(data, 48 + 4 * index, len(data))
        data.extend(child)
    put(data, 16, len(data))
    return data


def model_fixture():
    normal = raw_dds([(128, 128, 0, 37)] * 4)
    color = raw_dds([(128, 128, 0, 99)] * 4)
    texture_list = bytearray(64)
    for offset, value in {
        16: 64, 24: 2, 32: 48, 36: 56,
        48: 0, 52: len(normal), 56: len(normal), 60: len(color),
    }.items():
        put(texture_list, offset, value)
    ttdm = chunk(b"TTDM", [texture_list])
    put(ttdm, 40, struct.unpack_from("<I", ttdm, 48)[0])
    lheader = bytearray(64)
    lheader[:7] = b"LHeader"
    for offset, value in {16: 64, 24: 1, 32: 48, 48: 0}.items():
        put(lheader, offset, value)
    material = bytearray(64)
    put(material, 12, 2)
    put(material, 16, 32)
    put(material, 20, 48)
    struct.pack_into("<4I", material, 32, 0, 1, 0, 0)
    struct.pack_into("<4I", material, 48, 1, 0, 1, 0)
    geometry = chunk(b"MdlGeo", [chunk(b"ObjGeo", [material])])
    model = chunk(b"TMC\0", [ttdm, lheader, geometry, bytes(32)])
    return bytes(model), normal + color, len(normal)


class NormalMapTests(unittest.TestCase):
    def setUp(self):
        self.tmc, self.tmcl, self.texture_size = model_fixture()
        self.texconv = Path("unused-texconv.exe")
        self.work = Path("unused-work")
        self.process = patch.object(
            normals.subprocess, "run",
            side_effect=AssertionError("These tests must not start external tools"),
        )
        self.process.start()
        self.addCleanup(self.process.stop)

    def convert(self, indices=None):
        return normals.fix_normals(
            self.tmc, self.tmcl, self.texconv, self.work, indices,
        )

    def test_raw_mips_and_volume_are_all_corrected(self):
        pixels = [
            (128, 128, 0, 17), (0, 0, 0, 18),
            (255, 128, 0, 19), (128, 255, 0, 20),
        ] * 2 + [(128, 128, 0, 21)]
        source = raw_dds(pixels, depth=2, mips=2)
        result, drift = normals.fix_raw(source)
        expected_blue = [255, 128, 128, 128] * 2 + [255]
        self.assertEqual(list(result[130::4]), expected_blue)
        self.assertEqual(drift, 0.0)

    def test_raw_preserves_header_length_red_green_and_alpha(self):
        source = raw_dds([
            (128, 128, 0, 17), (0, 0, 0, 18),
            (255, 128, 0, 19), (128, 255, 0, 20),
        ])
        result, _ = normals.fix_raw(source)
        self.assertEqual(len(result), len(source))
        self.assertEqual(result[:128], source[:128])
        for channel in (0, 1, 3):
            self.assertEqual(result[128 + channel::4], source[128 + channel::4])

    def test_truncated_raw_is_rejected(self):
        with self.assertRaises(ValueError):
            normals.fix_raw(self.tmcl[:self.texture_size - 1])

    def test_invalid_dds_magic_is_rejected(self):
        with self.assertRaises(ValueError):
            normals.fix_raw(b"bad!" + self.tmcl[4:self.texture_size])

    def test_unsupported_compressed_format_is_rejected_before_tool_call(self):
        with self.assertRaises(ValueError):
            normals.fix_dds(
                self.tmcl[:self.texture_size], self.work, self.texconv,
            )

    def test_truncated_model_is_rejected(self):
        with self.assertRaises(ValueError):
            normals.fix_normals(b"", b"", self.texconv, self.work)

    def test_empty_indices_leave_both_files_unchanged(self):
        self.assertEqual(self.convert([]), (self.tmc, self.tmcl))

    def test_negative_index_is_rejected(self):
        with self.assertRaises(ValueError):
            self.convert([-1])

    def test_duplicate_index_is_rejected(self):
        with self.assertRaises(ValueError):
            self.convert([0, 0])

    def test_noninteger_indices_are_rejected(self):
        for index in (True, 0.0, "0"):
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.convert([index])

    def test_color_texture_cannot_be_selected_as_a_normal(self):
        with self.assertRaises(ValueError):
            self.convert([1])

    def test_explicit_selection_only_changes_normal_blue_channel(self):
        model, textures = self.convert([0])
        self.assertEqual(model, self.tmc)
        self.assertEqual(len(textures), len(self.tmcl))
        self.assertEqual(textures[:128], self.tmcl[:128])
        self.assertEqual(textures[self.texture_size:], self.tmcl[self.texture_size:])
        self.assertEqual(list(textures[130:self.texture_size:4]), [255] * 4)
        for channel in (0, 1, 3):
            self.assertEqual(
                textures[128 + channel:self.texture_size:4],
                self.tmcl[128 + channel:self.texture_size:4],
            )

    def test_automatic_selection_is_binding_aware_and_idempotent(self):
        automatic = self.convert()
        self.assertEqual(automatic, self.convert([0]))
        self.assertEqual(
            normals.fix_normals(
                automatic[0], automatic[1], self.texconv, self.work,
            ),
            automatic,
        )

    def test_raw_invalid_channel_masks_are_rejected(self):
        for mask in (0, 0xF0, 0xFF):
            source = bytearray(self.tmcl[:self.texture_size])
            put(source, 100, mask)
            with self.subTest(mask=mask), self.assertRaises(ValueError):
                normals.fix_raw(source)


def table_fixture():
    data = bytearray(bytes(range(256)) * 4)
    data[:8] = b"texch\0\0\0"
    put(data, 20, 3)
    put(data, 32, 48)
    struct.pack_into("<3I", data, 48, 256, 512, 768)
    put(data, 264, 3)
    return bytes(data)


class AlternateTableTests(unittest.TestCase):
    def test_patch_changes_only_count_and_mapping_fields(self):
        source = table_fixture()
        result = alternates.patch_pc_table(source, 4, [[0, 1], [2, 0]], 2)
        self.assertEqual(len(result), len(source))
        self.assertEqual(result[:264], source[:264])
        self.assertEqual(result[268:512], source[268:512])
        self.assertEqual(result[768:], source[768:])
        self.assertEqual(struct.unpack_from("<I", result, 264)[0], 4)
        self.assertEqual(
            struct.unpack_from("<64i", result, 512),
            (1, -1, 0, -1) + (-1,) * 60,
        )

    def test_invalid_target_counts_are_rejected(self):
        for count in (0, 65, -1, True, 2.0):
            with self.subTest(count=count), self.assertRaises(ValueError):
                alternates.patch_pc_table(table_fixture(), count, [])

    def test_invalid_or_duplicate_mappings_are_rejected(self):
        mappings = (
            [[0, 0], [0, 1]], [[4, 0]], [[-1, 0]], [[0, -1]],
            [[0, 64]], [[True, 0]], [[0, 0.0]], [[0]],
        )
        for mapping in mappings:
            with self.subTest(mapping=mapping), self.assertRaises(ValueError):
                alternates.patch_pc_table(table_fixture(), 4, mapping)

    def test_mapping_cannot_exceed_alternate_texture_count(self):
        with self.assertRaises(ValueError):
            alternates.patch_pc_table(table_fixture(), 4, [[0, 2]], 2)

    def test_truncated_or_unknown_tables_are_rejected(self):
        for data in (b"", table_fixture()[:767], b"badmagic" + table_fixture()[8:]):
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                alternates.patch_pc_table(data, 4, [])


class PcDecoderBoundsTests(unittest.TestCase):
    def test_decoded_resource_is_preserved(self):
        for magic in (b"TMC\0", b"texc", b"dlic", b"dlci"):
            data = magic + bytes(range(256))
            with self.subTest(magic=magic):
                self.assertEqual(alternates.decode_pc(data), data)

    def test_truncated_size_header_is_rejected(self):
        for size in range(4):
            with self.subTest(size=size), self.assertRaises(ValueError):
                alternates.decode_pc(bytes(size))

    def test_invalid_declared_sizes_are_rejected_without_allocation(self):
        for size in (0, alternates.MAX_RESOURCE + 1, 0xFFFFFFFF):
            with self.subTest(size=size), self.assertRaises(ValueError):
                alternates.decode_pc(struct.pack("<I", size))

    def test_missing_compressed_body_is_rejected(self):
        with self.assertRaises(ValueError):
            alternates.decode_pc(struct.pack("<I", 128))


if __name__ == "__main__":
    unittest.main()
