"""Tests for the production-source archive used by GitHub Actions."""

import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from create_source_archive import SOURCE_FILES, create_source_archive


class TestSourceArchive(unittest.TestCase):
    def test_archive_contains_only_runtime_source_and_is_repeatable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for relative_path in SOURCE_FILES:
                (root / relative_path).write_text(f"source: {relative_path}", encoding="utf-8")

            noise = (
                ".git/config",
                ".venv/lib/site.py",
                "__pycache__/module.pyc",
                "tests/test_example.py",
                "samples/old_report.json",
                "README.md",
                "compliance_policy.json",
                "source_archive.zip",
            )
            for relative_path in noise:
                path = root / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("generated or non-product file", encoding="utf-8")

            archive_path = root / "source_archive.zip"
            first_archive = create_source_archive(str(archive_path), temp_dir)
            first_bytes = first_archive.read_bytes()
            second_archive = create_source_archive(str(archive_path), temp_dir)

            self.assertEqual(second_archive.read_bytes(), first_bytes)
            with ZipFile(second_archive) as archive:
                self.assertEqual(archive.namelist(), list(SOURCE_FILES))

    def test_archive_fails_if_required_source_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(FileNotFoundError):
                create_source_archive(str(Path(temp_dir) / "source.zip"), temp_dir)


if __name__ == "__main__":
    unittest.main()
