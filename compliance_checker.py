"""
FOSSology CI Compliance Checker
A beginner-friendly tool to evaluate FOSSology license scan reports
against a configurable open source compliance policy.
"""

import argparse
import csv
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple


# Default compliance policy
# Permissive licenses are allowed; restrictive copyleft licenses are denied by default.
DEFAULT_POLICY = {
    "allowed_licenses": [
        "MIT",
        "Apache-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "ISC",
        "Unlicense",
        "CC0-1.0",
    ],
    "denied_licenses": [
        "AGPL-3.0-only",
        "AGPL-3.0-or-later",
        "GPL-2.0-only",
        "GPL-2.0-or-later",
        "GPL-3.0-only",
        "GPL-3.0-or-later",
        "SSPL-1.0",
    ],
    "allow_unlicensed": False,
}


def load_json_file(file_path: str) -> Any:
    """
    Safely load and parse a JSON file.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file is not valid JSON.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as err:
        raise ValueError(f"Invalid JSON format in {file_path}: {err}") from err


def _split_license_string(cell: Optional[str]) -> List[str]:
    """
    Split a CSV license cell into individual license identifiers.
    Handles delimiters like commas, semicolons, and SPDX 'NONE'/'NOASSERTION'.
    """
    if not cell:
        return []

    text = cell.strip()
    if not text:
        return []

    if text in ("NOASSERTION", "NONE", "No_license_found"):
        return ["No_license_found"]

    # Normalize delimiters: replace semicolons with commas
    normalized = text.replace(";", ",")
    parts = [p.strip() for p in normalized.split(",")]
    return [p for p in parts if p]


def load_csv_file(file_path: str) -> List[Dict[str, Any]]:
    """
    Safely load and parse a FOSSology CSV report into normalized file items.

    Supports standard column headers:
      - File path: 'filePath', 'FileName', or 'file_path'
      - Scanner licenses: 'scanner', 'LicenseInfoInFile', 'license', or 'licenses'
      - Concluded licenses: 'conclusion', 'LicenseConcluded', or 'concluded'

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the CSV is empty, malformed, or missing required columns.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    try:
        with open(file_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                raise ValueError(f"CSV file is empty or missing headers: {file_path}")

            # Normalize headers for case-insensitive lookup
            field_map = {
                name.lower().replace(" ", "").replace("_", ""): name
                for name in reader.fieldnames
                if name
            }

            # Identify file path column
            path_col = None
            for candidate in ("filepath", "filename", "file"):
                if candidate in field_map:
                    path_col = field_map[candidate]
                    break

            if not path_col:
                raise ValueError(
                    f"CSV report missing file path column (expected 'filePath' or 'FileName'): {file_path}"
                )

            # Identify scanner / license column
            scanner_col = None
            for candidate in ("scanner", "licenseinfoinfile", "license", "licenses", "scannerfindings"):
                if candidate in field_map:
                    scanner_col = field_map[candidate]
                    break

            # Identify conclusion column (optional)
            conclusion_col = None
            for candidate in ("conclusion", "licenseconcluded", "concluded", "conclusions"):
                if candidate in field_map:
                    conclusion_col = field_map[candidate]
                    break

            items = []
            for row in reader:
                # Skip blank rows
                if not any(row.values()):
                    continue

                raw_path = row.get(path_col)
                if not raw_path or not raw_path.strip():
                    continue

                file_path_val = raw_path.strip()

                scanner_val = row.get(scanner_col, "") if scanner_col else ""
                scanner_licenses = _split_license_string(scanner_val)

                conclusion_val = row.get(conclusion_col, "") if conclusion_col else ""
                conclusion_licenses = _split_license_string(conclusion_val) if conclusion_val else None

                item = {
                    "filePath": file_path_val,
                    "findings": {
                        "scanner": scanner_licenses,
                        "conclusion": conclusion_licenses if conclusion_licenses else None,
                    },
                }
                items.append(item)

            if not items:
                raise ValueError(f"No valid scan entries found in CSV report: {file_path}")

            return items
    except csv.Error as err:
        raise ValueError(f"Invalid CSV format in {file_path}: {err}") from err


def load_report_file(file_path: str) -> Any:
    """
    Safely load a FOSSology report from either JSON or CSV format.
    Automatically detects format based on file extension.
    """
    if file_path.lower().endswith(".csv"):
        return load_csv_file(file_path)
    return load_json_file(file_path)


def load_policy(policy_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Load compliance policy from a JSON file, or return DEFAULT_POLICY if None.
    """
    if not policy_path:
        return DEFAULT_POLICY.copy()

    loaded = load_json_file(policy_path)
    if not isinstance(loaded, dict):
        raise ValueError("Policy file must contain a JSON object.")

    # Merge loaded keys with defaults to guarantee all expected fields exist
    policy = {
        "allowed_licenses": loaded.get("allowed_licenses", DEFAULT_POLICY["allowed_licenses"]),
        "denied_licenses": loaded.get("denied_licenses", DEFAULT_POLICY["denied_licenses"]),
        "allow_unlicensed": loaded.get("allow_unlicensed", DEFAULT_POLICY["allow_unlicensed"]),
    }
    return policy


def extract_file_licenses(item: Dict[str, Any]) -> Tuple[str, List[str]]:
    """
    Extract file path and effective licenses from a FOSSology report item.

    Based on the official FOSSology REST API schema (`GET /uploads/{id}/licenses`):
    Each item contains:
      - filePath: relative path to the file
      - findings: {
            scanner: [...],
            conclusion: [...] or null
        }

    Returns:
        (file_path, effective_licenses)
    """
    file_path = item.get("filePath", "Unknown File")
    findings = item.get("findings", {})

    if not isinstance(findings, dict):
        return file_path, []

    # If human audit conclusions are available, they take precedence over automated scanner results
    conclusions = findings.get("conclusion")
    scanner_findings = findings.get("scanner", [])

    if conclusions and isinstance(conclusions, list) and len(conclusions) > 0:
        raw_licenses = conclusions
    elif isinstance(scanner_findings, list):
        raw_licenses = scanner_findings
    else:
        raw_licenses = []

    # Filter out empty entries and nulls
    licenses = [str(lic) for lic in raw_licenses if lic is not None and str(lic).strip()]
    return file_path, licenses


def evaluate_file(file_path: str, licenses: List[str], policy: Dict[str, Any]) -> Dict[str, Any]:
    """
    Evaluate a single file's licenses against the compliance policy.

    Statuses:
      - PASS: All detected licenses are on the allowed list.
      - DENIED: Detected at least one explicitly prohibited license.
      - NEEDS_REVIEW: License is unknown or file has no detected license.
    """
    allowed = set(policy.get("allowed_licenses", []))
    denied = set(policy.get("denied_licenses", []))
    allow_unlicensed = policy.get("allow_unlicensed", False)

    # Filter out placeholder indicators used by FOSSology when no license is detected
    clean_licenses = [l for l in licenses if l != "No_license_found"]

    # Case 1: No license detected
    if not clean_licenses:
        if allow_unlicensed:
            return {
                "filePath": file_path,
                "status": "PASS",
                "licenses": licenses,
                "reason": "No license detected (permitted by policy)",
            }
        return {
            "filePath": file_path,
            "status": "NEEDS_REVIEW",
            "licenses": licenses if licenses else ["No_license_found"],
            "reason": "Missing license statement; requires compliance review",
        }

    # Case 2: Check for explicitly prohibited (denied) licenses
    found_denied = [lic for lic in clean_licenses if lic in denied]
    if found_denied:
        return {
            "filePath": file_path,
            "status": "DENIED",
            "licenses": clean_licenses,
            "reason": f"Prohibited license(s) detected: {', '.join(found_denied)}",
        }

    # Case 3: Check for unknown licenses (neither allowed nor denied)
    unapproved = [lic for lic in clean_licenses if lic not in allowed]
    if unapproved:
        return {
            "filePath": file_path,
            "status": "NEEDS_REVIEW",
            "licenses": clean_licenses,
            "reason": f"Unapproved license(s) require review: {', '.join(unapproved)}",
        }

    # Case 4: All detected licenses are on the allowed list
    return {
        "filePath": file_path,
        "status": "PASS",
        "licenses": clean_licenses,
        "reason": f"Compliant: {', '.join(clean_licenses)}",
    }


def parse_fossology_report(raw_data: Any) -> List[Dict[str, Any]]:
    """
    Normalize report data into a list of file items.
    Accepts either a JSON list of items or an object wrapping the list under
    standard keys such as 'licenses', 'findings', or 'files'.
    """
    if isinstance(raw_data, list):
        return raw_data

    if isinstance(raw_data, dict):
        for key in ("licenses", "findings", "files"):
            if key in raw_data and isinstance(raw_data[key], list):
                return raw_data[key]
        if "filePath" in raw_data:
            return [raw_data]

    raise ValueError(
        "Invalid report format: Expected a list of file findings matching the FOSSology schema."
    )


def process_report(report_data: Any, policy: Dict[str, Any]) -> Dict[str, Any]:
    """
    Process all entries in a FOSSology report and produce a compliance summary.
    """
    file_items = parse_fossology_report(report_data)

    passed_files = []
    denied_files = []
    review_files = []
    all_detected_licenses = set()

    for item in file_items:
        if not isinstance(item, dict):
            continue

        file_path, licenses = extract_file_licenses(item)
        for lic in licenses:
            if lic != "No_license_found":
                all_detected_licenses.add(lic)

        result = evaluate_file(file_path, licenses, policy)

        if result["status"] == "PASS":
            passed_files.append(result)
        elif result["status"] == "DENIED":
            denied_files.append(result)
        else:
            review_files.append(result)

    # Determine overall status:
    # PASS only if there are zero denied and zero review-required files
    has_issues = len(denied_files) > 0 or len(review_files) > 0
    overall_status = "FAIL" if has_issues else "PASS"

    return {
        "status": overall_status,
        "total_files": len(file_items),
        "all_licenses": sorted(list(all_detected_licenses)),
        "passed_files": passed_files,
        "denied_files": denied_files,
        "review_files": review_files,
    }


def print_summary(summary: Dict[str, Any]) -> None:
    """
    Print an easy-to-read compliance report to standard output.
    """
    print("=" * 60)
    print("       FOSSology CI License Compliance Summary")
    print("=" * 60)
    print(f"Total Files Scanned : {summary['total_files']}")
    print(f"Passed Files        : {len(summary['passed_files'])}")
    print(f"Denied Files        : {len(summary['denied_files'])}")
    print(f"Review Required     : {len(summary['review_files'])}")

    detected = ", ".join(summary["all_licenses"]) if summary["all_licenses"] else "None"
    print(f"Detected Licenses   : {detected}")
    print("-" * 60)

    if summary["denied_files"]:
        print("\n[!] PROHIBITED LICENSES (POLICY FAILURES):")
        for item in summary["denied_files"]:
            print(f"  - {item['filePath']}")
            print(f"    Reason: {item['reason']}")

    if summary["review_files"]:
        print("\n[?] FINDINGS REQUIRING COMPLIANCE REVIEW:")
        for item in summary["review_files"]:
            print(f"  - {item['filePath']}")
            print(f"    Reason: {item['reason']}")

    print("\n" + "=" * 60)
    print(f"BUILD STATUS: {summary['status']}")
    print("=" * 60)


def main(argv: Optional[List[str]] = None) -> int:
    """
    Command-line interface entry point.
    Returns:
        0 if build status is PASS
        1 if build status is FAIL
        2 if an error occurred (e.g., file not found or invalid JSON)
    """
    parser = argparse.ArgumentParser(
        description="FOSSology CI Compliance Checker: Validate scan reports against license policies."
    )
    parser.add_argument(
        "--report",
        "-r",
        required=True,
        help="Path to the FOSSology JSON or CSV report file.",
    )
    parser.add_argument(
        "--policy",
        "-p",
        default=None,
        help="Path to an optional custom policy JSON file.",
    )

    args = parser.parse_args(argv)

    try:
        policy = load_policy(args.policy)
        raw_report = load_report_file(args.report)
        summary = process_report(raw_report, policy)
        print_summary(summary)
        return 0 if summary["status"] == "PASS" else 1
    except (FileNotFoundError, ValueError) as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
