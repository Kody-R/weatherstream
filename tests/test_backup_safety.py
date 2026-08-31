from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path


_temp_config = tempfile.TemporaryDirectory()
os.environ.setdefault("WEATHERSTREAM_CONFIG", _temp_config.name)

from app.operations import inspect_backup  # noqa: E402


def make_zip(files: dict[str, bytes], compression: int = zipfile.ZIP_STORED) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return output.getvalue()


class BackupSafetyTests(unittest.TestCase):
    def test_accepts_expected_backup_members(self) -> None:
        data = make_zip({"settings.json": json.dumps({"version": 14}).encode(), "branding/logo.png": b"small"})
        info = inspect_backup(data)
        self.assertEqual(info["settings"]["version"], 14)
        self.assertEqual(info["branding"], ["branding/logo.png"])

    def test_rejects_unknown_member(self) -> None:
        data = make_zip({"settings.json": b"{}", "branding/other.bin": b"bad"})
        with self.assertRaisesRegex(ValueError, "unsupported file"):
            inspect_backup(data)

    def test_rejects_high_compression_ratio(self) -> None:
        data = make_zip({"settings.json": b" " * 1_000_000}, zipfile.ZIP_DEFLATED)
        with self.assertRaisesRegex(ValueError, "unsafe compression ratio"):
            inspect_backup(data)

    def test_requires_settings(self) -> None:
        data = make_zip({"manifest.json": b"{}"})
        with self.assertRaisesRegex(ValueError, "missing settings"):
            inspect_backup(data)


if __name__ == "__main__":
    unittest.main()

