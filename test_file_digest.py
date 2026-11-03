import hashlib
import tempfile
import unittest
from pathlib import Path

from tools.file_digest import sha256_bytes, sha256_file


class FileDigestTests(unittest.TestCase):
    def test_bytes_matches_hashlib(self):
        payload = (b"a" * (1024 * 1024 + 1)) + "끝".encode("utf-8")
        self.assertEqual(sha256_bytes(payload), hashlib.sha256(payload).hexdigest())

    def test_file_streaming_matches_hashlib(self):
        payload = b"x" * (1024 * 1024 + 17)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload.bin"
            path.write_bytes(payload)
            self.assertEqual(sha256_file(path), hashlib.sha256(payload).hexdigest())


if __name__ == "__main__":
    unittest.main()
