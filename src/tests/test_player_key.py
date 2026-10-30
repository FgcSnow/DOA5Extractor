import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "outil"))
import extracteur as core
import player_key
from v2_alternates import decode_pc

class PlayerKeyTests(unittest.TestCase):
    def test_missing_key_does_not_decode(self):
        with self.assertRaisesRegex(player_key.KeyRequired, "Key required"):
            core.decode(b"\x08\x00\x00\x00garbage")
        with self.assertRaises(player_key.KeyRequired):
            decode_pc(b"\x08\x00\x00\x00garbage")

    def test_invalid_key_does_not_create_output(self):
        with tempfile.TemporaryDirectory() as t:
            folder = Path(t); key = folder / "key"; key.write_bytes(b"synthetic wrong key")
            out = folder / "result.zip"
            with self.assertRaisesRegex(player_key.KeyRequired, "SHA-256"):
                core.build([], ["costumes-v2"], out, key_file=key)
            self.assertFalse(out.exists())

    def test_validated_context_is_scoped_and_reset_on_error(self):
        value = b"synthetic test key, no game secret"
        with tempfile.TemporaryDirectory() as t:
            key = Path(t) / "key"; key.write_bytes(value)
            with patch.object(player_key, "EXPECTED_SHA256", hashlib.sha256(value).hexdigest()):
                with self.assertRaisesRegex(RuntimeError, "test"):
                    with player_key.key_context(key):
                        self.assertEqual(player_key.require_key(), value)
                        with player_key.key_context():
                            with self.assertRaises(player_key.KeyRequired): player_key.require_key()
                        raise RuntimeError("test")
                with self.assertRaises(player_key.KeyRequired): player_key.require_key()

    def test_plain_pc_resource_needs_no_key(self):
        self.assertEqual(decode_pc(b"TMC\0plain"), b"TMC\0plain")

    def test_oversized_key_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            key = Path(t) / "key"; key.write_bytes(b"x" * 65537)
            with self.assertRaises(player_key.KeyRequired): player_key.read_key(key)
