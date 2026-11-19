"""
Unit tests for run_ci_compliance.py.
Using Python's built-in unittest framework and mock objects.
"""

import io
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from fossology_client import (
    FossologyAuthError,
    FossologyClient,
    FossologyHttpError,
    FossologyTimeoutError,
)
from run_ci_compliance import (
    main,
    poll_jobs,
    run_pipeline,
)


class TestRunCICompliance(unittest.TestCase):

    def setUp(self):
        # Create a dummy archive file for testing
        self.temp_file = tempfile.NamedTemporaryFile("wb", delete=False, suffix=".zip")
        self.temp_file.write(b"PK\x03\x04dummy_zip_content")
        self.temp_file.close()
        self.archive_path = self.temp_file.name

        self.server_url = "http://fossology.example.com"
        self.token = "test_token_123"

    def tearDown(self):
        if os.path.exists(self.archive_path):
            os.remove(self.archive_path)

    # -------------------------------------------------------------
    # Polling Logic Tests
    # -------------------------------------------------------------

    def test_poll_jobs_immediate_complete(self):
        mock_client = MagicMock()
        mock_client.get_job_status.return_value = [
            {"id": 1, "name": "nomos", "status": "COMPLETED"},
            {"id": 2, "name": "monk", "status": "COMPLETED"},
        ]

        jobs = poll_jobs(
            client=mock_client,
            upload_id=42,
            poll_interval=1,
            timeout=10,
            sleep_fn=lambda s: None,
        )

        self.assertEqual(len(jobs), 2)
        mock_client.get_job_status.assert_called_once_with(upload_id=42)

    def test_poll_jobs_empty_then_processing_then_complete(self):
        mock_client = MagicMock()
        # 1st call: empty list, 2nd call: PROCESSING, 3rd call: COMPLETED
        mock_client.get_job_status.side_effect = [
            [],
            [{"id": 1, "status": "PROCESSING"}],
            [{"id": 1, "status": "COMPLETED"}],
        ]

        sleep_calls = []
        jobs = poll_jobs(
            client=mock_client,
            upload_id=42,
            poll_interval=5,
            timeout=30,
            sleep_fn=lambda s: sleep_calls.append(s),
        )

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["status"], "COMPLETED")
        self.assertEqual(mock_client.get_job_status.call_count, 3)
        self.assertEqual(sleep_calls, [5, 5])

    def test_poll_jobs_failure_raises(self):
        mock_client = MagicMock()
        mock_client.get_job_status.return_value = [
            {"id": 1, "name": "nomos", "status": "COMPLETED"},
            {"id": 2, "name": "monk", "status": "FAILED"},
        ]

        with self.assertRaises(RuntimeError) as ctx:
            poll_jobs(
                client=mock_client,
                upload_id=42,
                poll_interval=1,
                timeout=10,
                sleep_fn=lambda s: None,
            )
        self.assertIn("FOSSology scan job(s) failed", str(ctx.exception))
        self.assertIn("monk", str(ctx.exception))

    def test_poll_jobs_unknown_status_handled_safely(self):
        mock_client = MagicMock()
        # 1st call: unknown custom status, 2nd call: COMPLETED
        mock_client.get_job_status.side_effect = [
            [{"id": 1, "status": "INITIALIZING_CONTAINER"}],
            [{"id": 1, "status": "COMPLETED"}],
        ]

        jobs = poll_jobs(
            client=mock_client,
            upload_id=42,
            poll_interval=1,
            timeout=10,
            sleep_fn=lambda s: None,
        )

        self.assertEqual(jobs[0]["status"], "COMPLETED")
        self.assertEqual(mock_client.get_job_status.call_count, 2)

    def test_poll_jobs_timeout(self):
        mock_client = MagicMock()
        mock_client.get_job_status.return_value = [{"id": 1, "status": "PROCESSING"}]

        with self.assertRaises(FossologyTimeoutError):
            poll_jobs(
                client=mock_client,
                upload_id=42,
                poll_interval=1,
                timeout=0,  # 0 second timeout triggers immediately
                sleep_fn=lambda s: None,
            )

    # -------------------------------------------------------------
    # Full Pipeline Tests
    # -------------------------------------------------------------

    @patch("run_ci_compliance.FossologyClient")
    def test_run_pipeline_pass(self, mock_client_cls):
        mock_instance = mock_client_cls.return_value
        mock_instance.upload_archive.return_value = 101
        mock_instance.schedule_scan.return_value = 202
        mock_instance.get_job_status.return_value = [{"id": 202, "status": "COMPLETED"}]
        mock_instance.get_license_findings.return_value = [
            {"filePath": "src/main.py", "findings": {"scanner": ["MIT"], "conclusion": ["MIT"]}},
            {"filePath": "src/utils.py", "findings": {"scanner": ["Apache-2.0"], "conclusion": None}},
        ]

        code = run_pipeline(
            archive_path=self.archive_path,
            server_url=self.server_url,
            token=self.token,
            poll_interval=1,
            timeout=10,
            sleep_fn=lambda s: None,
        )

        self.assertEqual(code, 0)
        mock_instance.upload_archive.assert_called_once()
        mock_instance.schedule_scan.assert_called_once_with(upload_id=101, folder_id=1)
        mock_instance.get_license_findings.assert_called_once_with(upload_id=101)

    @patch("run_ci_compliance.FossologyClient")
    def test_run_pipeline_fail_prohibited_license(self, mock_client_cls):
        mock_instance = mock_client_cls.return_value
        mock_instance.upload_archive.return_value = 101
        mock_instance.schedule_scan.return_value = 202
        mock_instance.get_job_status.return_value = [{"id": 202, "status": "COMPLETED"}]
        mock_instance.get_license_findings.return_value = [
            {"filePath": "src/bad.py", "findings": {"scanner": ["GPL-3.0-only"], "conclusion": None}},
        ]

        code = run_pipeline(
            archive_path=self.archive_path,
            server_url=self.server_url,
            token=self.token,
            poll_interval=1,
            timeout=10,
            sleep_fn=lambda s: None,
        )

        # Prohibited license must exit with code 1
        self.assertEqual(code, 1)

    def test_run_pipeline_archive_not_found(self):
        code = run_pipeline(
            archive_path="non_existent_archive_xyz.zip",
            server_url=self.server_url,
            token=self.token,
        )
        self.assertEqual(code, 2)

    @patch("run_ci_compliance.FossologyClient")
    def test_run_pipeline_auth_error(self, mock_client_cls):
        mock_instance = mock_client_cls.return_value
        mock_instance.upload_archive.side_effect = FossologyAuthError("Invalid API token")

        code = run_pipeline(
            archive_path=self.archive_path,
            server_url=self.server_url,
            token=self.token,
        )
        self.assertEqual(code, 2)

    @patch("run_ci_compliance.FossologyClient")
    def test_run_pipeline_timeout_error(self, mock_client_cls):
        mock_instance = mock_client_cls.return_value
        mock_instance.upload_archive.return_value = 101
        mock_instance.schedule_scan.return_value = 202
        mock_instance.get_job_status.return_value = [{"id": 202, "status": "PROCESSING"}]

        code = run_pipeline(
            archive_path=self.archive_path,
            server_url=self.server_url,
            token=self.token,
            timeout=0,
            sleep_fn=lambda s: None,
        )
        self.assertEqual(code, 2)

    @patch("run_ci_compliance.FossologyClient")
    def test_run_pipeline_job_failure_error(self, mock_client_cls):
        mock_instance = mock_client_cls.return_value
        mock_instance.upload_archive.return_value = 101
        mock_instance.schedule_scan.return_value = 202
        mock_instance.get_job_status.return_value = [{"id": 202, "name": "nomos", "status": "FAILED"}]

        code = run_pipeline(
            archive_path=self.archive_path,
            server_url=self.server_url,
            token=self.token,
            sleep_fn=lambda s: None,
        )
        self.assertEqual(code, 2)

    # -------------------------------------------------------------
    # CLI Tests
    # -------------------------------------------------------------

    @patch("run_ci_compliance.run_pipeline")
    def test_cli_main(self, mock_run_pipeline):
        mock_run_pipeline.return_value = 0
        exit_code = main(["--archive", self.archive_path, "--folder-id", "3"])
        self.assertEqual(exit_code, 0)
        mock_run_pipeline.assert_called_once()
        args = mock_run_pipeline.call_args[1]
        self.assertEqual(args["archive_path"], self.archive_path)
        self.assertEqual(args["folder_id"], 3)


if __name__ == "__main__":
    unittest.main()
