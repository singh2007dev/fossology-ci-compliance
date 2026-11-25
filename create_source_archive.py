"""Create a repeatable archive of the runtime Python source being analyzed."""

import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


SOURCE_FILES = (
    "compliance_checker.py",
    "fossology_client.py",
    "run_ci_compliance.py",
)
ARCHIVE_FILES = ("LICENSE", *SOURCE_FILES)
ARCHIVE_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def create_source_archive(output_path: str, project_root: str = ".") -> Path:
    """Write only the runtime source files, replacing any prior archive."""
    root = Path(project_root)
    archive_path = Path(output_path)
    source_paths = [(relative_path, root / relative_path) for relative_path in ARCHIVE_FILES]
    for _, source_path in source_paths:
        if not source_path.is_file():
            raise FileNotFoundError(f"Required source file not found: {source_path}")

    with ZipFile(archive_path, mode="w", compression=ZIP_DEFLATED) as archive:
        for relative_path, source_path in source_paths:
            entry = ZipInfo(relative_path, date_time=ARCHIVE_TIMESTAMP)
            entry.compress_type = ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, source_path.read_bytes())

    return archive_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="source_archive.zip")
    args = parser.parse_args(argv)
    archive_path = create_source_archive(args.output)
    print(f"Created source archive: {archive_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
