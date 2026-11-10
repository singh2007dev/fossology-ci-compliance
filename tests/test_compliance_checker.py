"""
Unit tests for the FOSSology CI Compliance Checker.
Using Python's built-in unittest framework.
"""

import io
import os
import tempfile
import unittest
from unittest.mock import patch

from compliance_checker import (
    DEFAULT_POLICY,
    _split_license_string,
    evaluate_file,
    extract_file_licenses,
    load_csv_file,
    load_json_file,
    load_policy,
    load_report_file,
    main,
    parse_fossology_report,
    process_report,
)


class TestComplianceChecker(unittest.TestCase):

    def setUp(self):
        self.policy = {
            "allowed_licenses": ["MIT", "Apache-2.0", "BSD-3-Clause"],
            "denied_licenses": ["GPL-3.0-only", "AGPL-3.0-only"],
            "allow_unlicensed": False,
        }

    # -------------------------------------------------------------
    # Tests for load_json_file and load_policy
    # -------------------------------------------------------------

    def test_load_json_file_success(self):
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json") as f:
            f.write('{"testKey": "testVal"}')
            temp_path = f.name

        try:
            data = load_json_file(temp_path)
            self.assertEqual(data, {"testKey": "testVal"})
        finally:
            os.remove(temp_path)

    def test_load_json_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            load_json_file("non_existent_file_path_12345.json")

    def test_load_json_file_invalid_json(self):
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json") as f:
            f.write('{ invalid json content')
            temp_path = f.name

        try:
            with self.assertRaises(ValueError):
                load_json_file(temp_path)
        finally:
            os.remove(temp_path)

    def test_load_policy_defaults(self):
        policy = load_policy(None)
        self.assertIn("MIT", policy["allowed_licenses"])
        self.assertIn("GPL-3.0-only", policy["denied_licenses"])
        self.assertFalse(policy["allow_unlicensed"])

    def test_load_policy_custom(self):
        custom_data = (
            '{"allowed_licenses": ["MIT"], "denied_licenses": ["LGPL-3.0-only"], "allow_unlicensed": true}'
        )
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json") as f:
            f.write(custom_data)
            temp_path = f.name

        try:
            policy = load_policy(temp_path)
            self.assertEqual(policy["allowed_licenses"], ["MIT"])
            self.assertEqual(policy["denied_licenses"], ["LGPL-3.0-only"])
            self.assertTrue(policy["allow_unlicensed"])
        finally:
            os.remove(temp_path)

    def test_load_policy_invalid_root(self):
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json") as f:
            f.write('["not", "a", "dict"]')
            temp_path = f.name

        try:
            with self.assertRaises(ValueError):
                load_policy(temp_path)
        finally:
            os.remove(temp_path)

    # -------------------------------------------------------------
    # Tests for extract_file_licenses
    # -------------------------------------------------------------

    def test_extract_scanner_findings(self):
        item = {
            "filePath": "src/module.py",
            "findings": {
                "scanner": ["Apache-2.0"],
                "conclusion": None,
            },
        }
        path, licenses = extract_file_licenses(item)
        self.assertEqual(path, "src/module.py")
        self.assertEqual(licenses, ["Apache-2.0"])

    def test_extract_conclusion_precedence(self):
        # When human conclusion is present, it overrides automated scanner findings
        item = {
            "filePath": "src/module.py",
            "findings": {
                "scanner": ["GPL-3.0-only"],
                "conclusion": ["MIT"],
            },
        }
        path, licenses = extract_file_licenses(item)
        self.assertEqual(path, "src/module.py")
        self.assertEqual(licenses, ["MIT"])

    def test_extract_empty_findings(self):
        item = {"filePath": "docs/readme.txt", "findings": {}}
        path, licenses = extract_file_licenses(item)
        self.assertEqual(path, "docs/readme.txt")
        self.assertEqual(licenses, [])

    # -------------------------------------------------------------
    # Tests for evaluate_file
    # -------------------------------------------------------------

    def test_evaluate_file_allowed(self):
        result = evaluate_file("src/app.py", ["MIT"], self.policy)
        self.assertEqual(result["status"], "PASS")
        self.assertIn("Compliant", result["reason"])

    def test_evaluate_file_denied(self):
        result = evaluate_file("src/vendor.py", ["GPL-3.0-only"], self.policy)
        self.assertEqual(result["status"], "DENIED")
        self.assertIn("Prohibited", result["reason"])

    def test_evaluate_file_unknown_needs_review(self):
        result = evaluate_file("src/lib.py", ["MPL-2.0"], self.policy)
        self.assertEqual(result["status"], "NEEDS_REVIEW")
        self.assertIn("Unapproved", result["reason"])

    def test_evaluate_file_no_license_disallowed(self):
        result = evaluate_file("src/unknown.py", ["No_license_found"], self.policy)
        self.assertEqual(result["status"], "NEEDS_REVIEW")
        self.assertIn("Missing license", result["reason"])

    def test_evaluate_file_no_license_allowed(self):
        policy_allow_unlicensed = self.policy.copy()
        policy_allow_unlicensed["allow_unlicensed"] = True
        result = evaluate_file("src/unknown.py", ["No_license_found"], policy_allow_unlicensed)
        self.assertEqual(result["status"], "PASS")

    # -------------------------------------------------------------
    # Tests for parse_fossology_report & process_report
    # -------------------------------------------------------------

    def test_parse_wrapped_report(self):
        raw = {
            "uploadId": 42,
            "findings": [
                {"filePath": "a.py", "findings": {"scanner": ["MIT"], "conclusion": None}}
            ],
        }
        parsed = parse_fossology_report(raw)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["filePath"], "a.py")

    def test_parse_invalid_report_structure(self):
        with self.assertRaises(ValueError):
            parse_fossology_report("not a list or dict")

    def test_process_report_pass(self):
        report = [
            {"filePath": "main.py", "findings": {"scanner": ["MIT"], "conclusion": ["MIT"]}},
            {"filePath": "utils.py", "findings": {"scanner": ["Apache-2.0"], "conclusion": None}},
        ]
        summary = process_report(report, self.policy)
        self.assertEqual(summary["status"], "PASS")
        self.assertEqual(summary["total_files"], 2)
        self.assertEqual(len(summary["passed_files"]), 2)
        self.assertEqual(len(summary["denied_files"]), 0)
        self.assertEqual(len(summary["review_files"]), 0)

    def test_process_report_fail_on_denied(self):
        report = [
            {"filePath": "main.py", "findings": {"scanner": ["MIT"], "conclusion": None}},
            {"filePath": "bad.py", "findings": {"scanner": ["GPL-3.0-only"], "conclusion": None}},
        ]
        summary = process_report(report, self.policy)
        self.assertEqual(summary["status"], "FAIL")
        self.assertEqual(len(summary["denied_files"]), 1)
        self.assertEqual(summary["denied_files"][0]["filePath"], "bad.py")

    def test_process_report_fail_on_review(self):
        report = [
            {"filePath": "main.py", "findings": {"scanner": ["MIT"], "conclusion": None}},
            {"filePath": "mystery.py", "findings": {"scanner": ["Unknown-License"], "conclusion": None}},
        ]
        summary = process_report(report, self.policy)
        self.assertEqual(summary["status"], "FAIL")
        self.assertEqual(len(summary["review_files"]), 1)

    # -------------------------------------------------------------
    # CLI Tests
    # -------------------------------------------------------------

    def test_cli_pass(self):
        exit_code = main(["--report", "samples/sample_report_pass.json"])
        self.assertEqual(exit_code, 0)

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_cli_fail_denied(self, mock_stdout):
        exit_code = main(["--report", "samples/sample_report_fail.json"])
        self.assertEqual(exit_code, 1)
        output = mock_stdout.getvalue()
        self.assertIn("PROHIBITED LICENSES (POLICY FAILURES)", output)
        self.assertNotIn("PROHIBITED LICENSES (VIOLATIONS)", output)

    def test_cli_fail_review(self):
        exit_code = main(["--report", "samples/sample_report_review.json"])
        self.assertEqual(exit_code, 1)

    def test_cli_file_not_found(self):
        exit_code = main(["--report", "non_existent_sample.json"])
        self.assertEqual(exit_code, 2)

    # -------------------------------------------------------------
    # CSV Processing Tests
    # -------------------------------------------------------------

    def test_split_license_string(self):
        self.assertEqual(_split_license_string("MIT, Apache-2.0"), ["MIT", "Apache-2.0"])
        self.assertEqual(_split_license_string("BSD-3-Clause; ISC"), ["BSD-3-Clause", "ISC"])
        self.assertEqual(_split_license_string("NONE"), ["No_license_found"])
        self.assertEqual(_split_license_string("NOASSERTION"), ["No_license_found"])
        self.assertEqual(_split_license_string(""), [])
        self.assertEqual(_split_license_string(None), [])

    def test_load_csv_file_success(self):
        items = load_csv_file("samples/sample_report_pass.csv")
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0]["filePath"], "src/main.py")
        self.assertEqual(items[0]["findings"]["scanner"], ["MIT"])
        self.assertEqual(items[0]["findings"]["conclusion"], ["MIT"])
        self.assertIsNone(items[1]["findings"]["conclusion"])

    def test_load_csv_file_spdx_headers(self):
        csv_content = (
            "FileName,LicenseInfoInFile,LicenseConcluded\n"
            "src/core.py,MIT,Apache-2.0\n"
        )
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".csv") as f:
            f.write(csv_content)
            temp_path = f.name

        try:
            items = load_csv_file(temp_path)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["filePath"], "src/core.py")
            self.assertEqual(items[0]["findings"]["scanner"], ["MIT"])
            self.assertEqual(items[0]["findings"]["conclusion"], ["Apache-2.0"])
        finally:
            os.remove(temp_path)

    def test_load_csv_file_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            load_csv_file("non_existent_report.csv")

    def test_load_csv_file_empty(self):
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".csv") as f:
            f.write("")
            temp_path = f.name

        try:
            with self.assertRaises(ValueError):
                load_csv_file(temp_path)
        finally:
            os.remove(temp_path)

    def test_load_csv_file_missing_path_column(self):
        csv_content = "some_random_column,another_col\n1,2\n"
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".csv") as f:
            f.write(csv_content)
            temp_path = f.name

        try:
            with self.assertRaises(ValueError):
                load_csv_file(temp_path)
        finally:
            os.remove(temp_path)

    def test_load_csv_file_no_entries(self):
        csv_content = "filePath,scanner,conclusion\n"
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".csv") as f:
            f.write(csv_content)
            temp_path = f.name

        try:
            with self.assertRaises(ValueError):
                load_csv_file(temp_path)
        finally:
            os.remove(temp_path)

    def test_load_report_file_routing(self):
        json_report = load_report_file("samples/sample_report_pass.json")
        csv_report = load_report_file("samples/sample_report_pass.csv")
        self.assertIsInstance(json_report, list)
        self.assertIsInstance(csv_report, list)

    def test_cli_csv_pass(self):
        exit_code = main(["--report", "samples/sample_report_pass.csv"])
        self.assertEqual(exit_code, 0)

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_cli_csv_fail(self, mock_stdout):
        exit_code = main(["--report", "samples/sample_report_fail.csv"])
        self.assertEqual(exit_code, 1)
        output = mock_stdout.getvalue()
        self.assertIn("PROHIBITED LICENSES (POLICY FAILURES)", output)

    def test_cli_csv_missing_file(self):
        exit_code = main(["--report", "samples/non_existent.csv"])
        self.assertEqual(exit_code, 2)


if __name__ == "__main__":
    unittest.main()
