"""
Unit tests for run_ci_compliance.py.
Using Python's built-in unittest framework and mock objects.
"""

import io
import os
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch

from fossology_client import (
    FossologyAuthError,
    FossologyClient,
    FossologyHttpError,
    FossologyTimeoutError,
)
from run_ci_compliance import (
    _normalize_status,
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
    # Status Normalization & Polling Logic Tests
    # -------------------------------------------------------------

    def test_normalize_status(self):
        self.assertEqual(_normalize_status("Completed"), "COMPLETED")
        self.assertEqual(_normalize_status("completed"), "COMPLETED")
        self.assertEqual(_normalize_status("Queued"), "QUEUED")
        self.assertEqual(_normalize_status("Running"), "RUNNING")
        self.assertEqual(_normalize_status("Failed"), "FAILED")
        self.assertEqual(_normalize_status("  failed  "), "FAILED")
        self.assertEqual(_normalize_status(None), "UNKNOWN")

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

    def test_poll_jobs_case_insensitive_completed(self):
        mock_client = MagicMock()
        mock_client.get_job_status.return_value = [
            {"id": 1, "name": "ununpack", "status": "Completed"},
            {"id": 2, "name": "adj2nest", "status": "completed"},
        ]

        jobs = poll_jobs(
            client=mock_client,
            upload_id=42,
            stage_name="initial upload-processing",
            required_job_names=("ununpack", "adj2nest"),
            poll_interval=1,
            timeout=10,
            sleep_fn=lambda s: None,
        )

        self.assertEqual(len(jobs), 2)

    def test_poll_jobs_initial_processing_only_ununpack_initially_waits_for_adj2nest(self):
        """
        Verify that if only ununpack appears initially, polling continues
        until adj2nest also appears and both are completed.
        """
        mock_client = MagicMock()
        mock_client.get_job_status.side_effect = [
            # 1st poll: only completed ununpack appears (adj2nest not yet in queue)
            [{"id": 1, "name": "ununpack", "status": "Completed"}],
            # 2nd poll: adj2nest appears, running
            [
                {"id": 1, "name": "ununpack", "status": "Completed"},
                {"id": 2, "name": "adj2nest", "status": "Running"},
            ],
            # 3rd poll: both completed
            [
                {"id": 1, "name": "ununpack", "status": "Completed"},
                {"id": 2, "name": "adj2nest", "status": "Completed"},
            ],
        ]

        sleep_calls = []
        jobs = poll_jobs(
            client=mock_client,
            upload_id=42,
            stage_name="initial upload-processing",
            required_job_names=("ununpack", "adj2nest"),
            poll_interval=2,
            timeout=10,
            sleep_fn=lambda s: sleep_calls.append(s),
        )

        self.assertEqual(len(jobs), 2)
        self.assertEqual(mock_client.get_job_status.call_count, 3)
        self.assertEqual(sleep_calls, [2, 2])
        job_names = {j["name"] for j in jobs}
        self.assertEqual(job_names, {"ununpack", "adj2nest"})

    def test_poll_jobs_initial_processing_either_job_fails(self):
        """
        Verify that if either ununpack or adj2nest fails, poll_jobs halts immediately.
        """
        # Case 1: ununpack fails while adj2nest is completed
        mock_client1 = MagicMock()
        mock_client1.get_job_status.return_value = [
            {"id": 1, "name": "ununpack", "status": "Failed"},
            {"id": 2, "name": "adj2nest", "status": "Completed"},
        ]
        with self.assertRaises(RuntimeError) as ctx1:
            poll_jobs(
                client=mock_client1,
                upload_id=42,
                stage_name="initial upload-processing",
                required_job_names=("ununpack", "adj2nest"),
                poll_interval=1,
                timeout=10,
                sleep_fn=lambda s: None,
            )
        self.assertIn("initial upload-processing", str(ctx1.exception))
        self.assertIn("ununpack", str(ctx1.exception))

        # Case 2: adj2nest fails while ununpack is completed
        mock_client2 = MagicMock()
        mock_client2.get_job_status.return_value = [
            {"id": 1, "name": "ununpack", "status": "Completed"},
            {"id": 2, "name": "adj2nest", "status": "Failed"},
        ]
        with self.assertRaises(RuntimeError) as ctx2:
            poll_jobs(
                client=mock_client2,
                upload_id=42,
                stage_name="initial upload-processing",
                required_job_names=("ununpack", "adj2nest"),
                poll_interval=1,
                timeout=10,
                sleep_fn=lambda s: None,
            )
        self.assertIn("initial upload-processing", str(ctx2.exception))
        self.assertIn("adj2nest", str(ctx2.exception))

        # Case 3: ununpack fails before adj2nest even appears
        mock_client3 = MagicMock()
        mock_client3.get_job_status.return_value = [
            {"id": 1, "name": "ununpack", "status": "Failed"},
        ]
        with self.assertRaises(RuntimeError) as ctx3:
            poll_jobs(
                client=mock_client3,
                upload_id=42,
                stage_name="initial upload-processing",
                required_job_names=("ununpack", "adj2nest"),
                poll_interval=1,
                timeout=10,
                sleep_fn=lambda s: None,
            )
        self.assertIn("initial upload-processing", str(ctx3.exception))
        self.assertIn("ununpack", str(ctx3.exception))

    def test_poll_jobs_initial_processing_timeout_when_adj2nest_never_appears(self):
        """
        Verify that if adj2nest never appears, polling times out.
        """
        mock_client = MagicMock()
        mock_client.get_job_status.return_value = [
            {"id": 1, "name": "ununpack", "status": "Completed"},
        ]

        with self.assertRaises(FossologyTimeoutError) as ctx:
            poll_jobs(
                client=mock_client,
                upload_id=42,
                stage_name="initial upload-processing",
                required_job_names=("ununpack", "adj2nest"),
                poll_interval=1,
                timeout=0,  # immediate timeout
                sleep_fn=lambda s: None,
            )
        self.assertIn("Timeout after 0s waiting for initial upload-processing jobs", str(ctx.exception))

    def test_poll_jobs_case_insensitive_failed(self):
        mock_client = MagicMock()
        mock_client.get_job_status.return_value = [
            {"id": 1, "name": "ununpack", "status": "Failed"},
        ]

        with self.assertRaises(RuntimeError) as ctx:
            poll_jobs(
                client=mock_client,
                upload_id=42,
                stage_name="initial upload-processing",
                poll_interval=1,
                timeout=10,
                sleep_fn=lambda s: None,
            )
        self.assertIn("initial upload-processing", str(ctx.exception))
        self.assertIn("ununpack", str(ctx.exception))

    def test_poll_jobs_case_insensitive_queued_running_then_complete(self):
        mock_client = MagicMock()
        mock_client.get_job_status.side_effect = [
            [{"id": 1, "status": "Queued"}],
            [{"id": 1, "status": "Running"}],
            [{"id": 1, "status": "Completed"}],
        ]

        sleep_calls = []
        jobs = poll_jobs(
            client=mock_client,
            upload_id=42,
            poll_interval=2,
            timeout=10,
            sleep_fn=lambda s: sleep_calls.append(s),
        )

        self.assertEqual(len(jobs), 1)
        self.assertEqual(mock_client.get_job_status.call_count, 3)
        self.assertEqual(sleep_calls, [2, 2])

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

        with self.assertRaises(FossologyTimeoutError) as ctx:
            poll_jobs(
                client=mock_client,
                upload_id=42,
                stage_name="scan",
                poll_interval=1,
                timeout=0,  # 0 second timeout triggers immediately
                sleep_fn=lambda s: None,
            )
        self.assertIn("Timeout after 0s waiting for scan jobs", str(ctx.exception))

    # -------------------------------------------------------------
    # License Scan Polling Tests (Nomos & Monk synchronization)
    # -------------------------------------------------------------

    def test_poll_jobs_license_scan_ignores_initial_unpack_jobs_until_scan_jobs_appear(self):
        """
        Verify that during license scan polling, only completed ununpack and adj2nest
        jobs appearing initially does NOT cause early completion; polling continues
        until nomos and monk appear and complete.
        """
        mock_client = MagicMock()
        mock_client.get_job_status.side_effect = [
            # 1st poll: only completed ununpack and adj2nest exist (race condition)
            [
                {"id": 1, "name": "ununpack", "status": "Completed"},
                {"id": 2, "name": "adj2nest", "status": "Completed"},
            ],
            # 2nd poll: nomos and monk have both appeared and completed
            [
                {"id": 1, "name": "ununpack", "status": "Completed"},
                {"id": 2, "name": "adj2nest", "status": "Completed"},
                {"id": 3, "name": "nomos", "status": "Completed"},
                {"id": 4, "name": "monk", "status": "Completed"},
            ],
        ]

        sleep_calls = []
        jobs = poll_jobs(
            client=mock_client,
            upload_id=42,
            stage_name="license scan",
            required_job_names=("nomos", "monk"),
            ignored_job_names=("ununpack", "adj2nest"),
            poll_interval=2,
            timeout=10,
            sleep_fn=lambda s: sleep_calls.append(s),
        )

        self.assertEqual(len(jobs), 2)
        job_names = {j["name"] for j in jobs}
        self.assertEqual(job_names, {"nomos", "monk"})
        self.assertEqual(mock_client.get_job_status.call_count, 2)
        self.assertEqual(sleep_calls, [2])

    def test_poll_jobs_license_scan_nomos_appears_before_monk(self):
        """
        Verify that if Nomos appears before Monk, polling continues until
        Monk also appears and both are completed.
        """
        mock_client = MagicMock()
        mock_client.get_job_status.side_effect = [
            # 1st poll: nomos running, monk not yet in queue
            [
                {"id": 1, "name": "ununpack", "status": "Completed"},
                {"id": 2, "name": "adj2nest", "status": "Completed"},
                {"id": 3, "name": "nomos", "status": "Running"},
            ],
            # 2nd poll: nomos completed, monk running
            [
                {"id": 1, "name": "ununpack", "status": "Completed"},
                {"id": 2, "name": "adj2nest", "status": "Completed"},
                {"id": 3, "name": "nomos", "status": "Completed"},
                {"id": 4, "name": "monk", "status": "Running"},
            ],
            # 3rd poll: both nomos and monk completed
            [
                {"id": 1, "name": "ununpack", "status": "Completed"},
                {"id": 2, "name": "adj2nest", "status": "Completed"},
                {"id": 3, "name": "nomos", "status": "Completed"},
                {"id": 4, "name": "monk", "status": "Completed"},
            ],
        ]

        sleep_calls = []
        jobs = poll_jobs(
            client=mock_client,
            upload_id=42,
            stage_name="license scan",
            required_job_names=("nomos", "monk"),
            ignored_job_names=("ununpack", "adj2nest"),
            poll_interval=1,
            timeout=10,
            sleep_fn=lambda s: sleep_calls.append(s),
        )

        self.assertEqual(len(jobs), 2)
        self.assertEqual(mock_client.get_job_status.call_count, 3)
        self.assertEqual(sleep_calls, [1, 1])

    def test_poll_jobs_license_scan_both_scan_jobs_complete_successfully(self):
        """
        Verify that when both nomos and monk have Completed or Closed status,
        the license scan stage completes successfully.
        """
        mock_client = MagicMock()
        mock_client.get_job_status.return_value = [
            {"id": 1, "name": "ununpack", "status": "Completed"},
            {"id": 2, "name": "adj2nest", "status": "Completed"},
            {"id": 3, "name": "nomos", "status": "Completed"},
            {"id": 4, "name": "monk", "status": "Closed"},
        ]

        jobs = poll_jobs(
            client=mock_client,
            upload_id=42,
            stage_name="license scan",
            required_job_names=("nomos", "monk"),
            ignored_job_names=("ununpack", "adj2nest"),
            poll_interval=1,
            timeout=10,
            sleep_fn=lambda s: None,
        )

        self.assertEqual(len(jobs), 2)
        statuses = {j["status"] for j in jobs}
        self.assertEqual(statuses, {"Completed", "Closed"})

    def test_poll_jobs_license_scan_either_scan_job_failing(self):
        """
        Verify that if either nomos or monk fails, poll_jobs immediately stops
        with a clear error.
        """
        # Case 1: Nomos fails while Monk is completed
        mock_client1 = MagicMock()
        mock_client1.get_job_status.return_value = [
            {"id": 1, "name": "ununpack", "status": "Completed"},
            {"id": 3, "name": "nomos", "status": "Failed"},
            {"id": 4, "name": "monk", "status": "Completed"},
        ]

        with self.assertRaises(RuntimeError) as ctx1:
            poll_jobs(
                client=mock_client1,
                upload_id=42,
                stage_name="license scan",
                required_job_names=("nomos", "monk"),
                ignored_job_names=("ununpack", "adj2nest"),
                poll_interval=1,
                timeout=10,
                sleep_fn=lambda s: None,
            )
        self.assertIn("license scan", str(ctx1.exception))
        self.assertIn("nomos", str(ctx1.exception))

        # Case 2: Monk fails while Nomos is completed
        mock_client2 = MagicMock()
        mock_client2.get_job_status.return_value = [
            {"id": 1, "name": "ununpack", "status": "Completed"},
            {"id": 3, "name": "nomos", "status": "Completed"},
            {"id": 4, "name": "monk", "status": "Failed"},
        ]

        with self.assertRaises(RuntimeError) as ctx2:
            poll_jobs(
                client=mock_client2,
                upload_id=42,
                stage_name="license scan",
                required_job_names=("nomos", "monk"),
                ignored_job_names=("ununpack", "adj2nest"),
                poll_interval=1,
                timeout=10,
                sleep_fn=lambda s: None,
            )
        self.assertIn("license scan", str(ctx2.exception))
        self.assertIn("monk", str(ctx2.exception))

        # Case 3: Nomos fails before Monk even appears
        mock_client3 = MagicMock()
        mock_client3.get_job_status.return_value = [
            {"id": 1, "name": "ununpack", "status": "Completed"},
            {"id": 3, "name": "nomos", "status": "Failed"},
        ]

        with self.assertRaises(RuntimeError) as ctx3:
            poll_jobs(
                client=mock_client3,
                upload_id=42,
                stage_name="license scan",
                required_job_names=("nomos", "monk"),
                ignored_job_names=("ununpack", "adj2nest"),
                poll_interval=1,
                timeout=10,
                sleep_fn=lambda s: None,
            )
        self.assertIn("license scan", str(ctx3.exception))
        self.assertIn("nomos", str(ctx3.exception))

    def test_poll_jobs_license_scan_timeout_when_monk_never_appears(self):
        """
        Verify that if Monk never appears in the job queue, polling continues
        until the timeout is exceeded, raising FossologyTimeoutError.
        """
        mock_client = MagicMock()
        # Monk is completely missing; only ununpack, adj2nest and nomos exist
        mock_client.get_job_status.return_value = [
            {"id": 1, "name": "ununpack", "status": "Completed"},
            {"id": 2, "name": "adj2nest", "status": "Completed"},
            {"id": 3, "name": "nomos", "status": "Completed"},
        ]

        with self.assertRaises(FossologyTimeoutError) as ctx:
            poll_jobs(
                client=mock_client,
                upload_id=42,
                stage_name="license scan",
                required_job_names=("nomos", "monk"),
                ignored_job_names=("ununpack", "adj2nest"),
                poll_interval=1,
                timeout=0,  # 0s timeout triggers immediately
                sleep_fn=lambda s: None,
            )
        self.assertIn("Timeout after 0s waiting for license scan jobs", str(ctx.exception))

    # -------------------------------------------------------------
    # Full Pipeline Tests
    # -------------------------------------------------------------

    @patch("run_ci_compliance.FossologyClient")
    def test_run_pipeline_pass(self, mock_client_cls):
        mock_instance = mock_client_cls.return_value
        mock_instance.upload_archive.return_value = 101
        mock_instance.schedule_scan.return_value = 202
        mock_instance.get_job_status.return_value = [
            {"id": 1, "name": "ununpack", "status": "COMPLETED"},
            {"id": 2, "name": "adj2nest", "status": "COMPLETED"},
            {"id": 201, "name": "nomos", "status": "COMPLETED"},
            {"id": 202, "name": "monk", "status": "COMPLETED"},
        ]
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
    def test_run_pipeline_two_stage_execution(self, mock_client_cls):
        """
        Verify the exact two-stage execution:
        Stage 1: wait for initial upload processing (both ununpack and adj2nest) to complete.
        Stage 2: schedule_scan() is called only after both are completed.
        Stage 3: wait for license scan (nomos/monk) to complete.
        Stage 4: get_license_findings() called.
        """
        mock_instance = mock_client_cls.return_value
        mock_instance.upload_archive.return_value = 101
        mock_instance.schedule_scan.return_value = 202

        # 1st call: ununpack is Running, adj2nest is Queued
        # 2nd call: ununpack is Completed, adj2nest is Running
        # 3rd call: ununpack is Completed, adj2nest is Completed
        # (schedule_scan is called)
        # 4th call: scan nomos is Running, monk is Queued
        # 5th call: scan nomos is Completed, monk is Completed
        mock_instance.get_job_status.side_effect = [
            [{"name": "ununpack", "status": "Running"}, {"name": "adj2nest", "status": "Queued"}],
            [{"name": "ununpack", "status": "Completed"}, {"name": "adj2nest", "status": "Running"}],
            [{"name": "ununpack", "status": "Completed"}, {"name": "adj2nest", "status": "Completed"}],
            [{"name": "nomos", "status": "Running"}, {"id": 202, "name": "monk", "status": "Queued"}],
            [{"name": "nomos", "status": "Completed"}, {"id": 202, "name": "monk", "status": "Completed"}],
        ]

        mock_instance.get_license_findings.return_value = [
            {"filePath": "src/main.py", "findings": {"scanner": ["MIT"], "conclusion": ["MIT"]}},
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
        self.assertEqual(mock_instance.get_job_status.call_count, 5)
        mock_instance.schedule_scan.assert_called_once_with(upload_id=101, folder_id=1)
        mock_instance.get_license_findings.assert_called_once_with(upload_id=101)

    @patch("run_ci_compliance.FossologyClient")
    def test_schedule_scan_not_called_until_both_ununpack_and_adj2nest_complete(self, mock_client_cls):
        """
        Explicitly verify that schedule_scan() is NOT called while either ununpack
        or adj2nest is Running or Queued, and is only called once both are Completed.
        """
        mock_instance = mock_client_cls.return_value
        mock_instance.upload_archive.return_value = 101
        mock_instance.schedule_scan.return_value = 202

        poll_history = []

        def mock_get_job_status(upload_id=None, job_id=None):
            call_idx = len(poll_history)
            if call_idx == 0:
                # ununpack Running, adj2nest Queued
                res = [{"name": "ununpack", "status": "Running"}, {"name": "adj2nest", "status": "Queued"}]
            elif call_idx == 1:
                # ununpack Completed, adj2nest Running
                res = [{"name": "ununpack", "status": "Completed"}, {"name": "adj2nest", "status": "Running"}]
            elif call_idx == 2:
                # Both completed
                res = [{"name": "ununpack", "status": "Completed"}, {"name": "adj2nest", "status": "Completed"}]
            else:
                # Scan jobs completed (both nomos and monk)
                res = [{"name": "nomos", "status": "Completed"}, {"id": 202, "name": "monk", "status": "Completed"}]

            poll_history.append((res, mock_instance.schedule_scan.call_count))
            return res

        mock_instance.get_job_status.side_effect = mock_get_job_status
        mock_instance.get_license_findings.return_value = []

        code = run_pipeline(
            archive_path=self.archive_path,
            server_url=self.server_url,
            token=self.token,
            poll_interval=1,
            timeout=10,
            sleep_fn=lambda s: None,
        )

        self.assertEqual(code, 0)

        # During call 0 (ununpack Running, adj2nest Queued), schedule_scan must NOT have been called
        self.assertEqual(poll_history[0][1], 0)
        # During call 1 (ununpack Completed, adj2nest Running), schedule_scan must NOT have been called
        self.assertEqual(poll_history[1][1], 0)
        # During call 2 (both Completed), schedule_scan had still not been called at the time of poll
        self.assertEqual(poll_history[2][1], 0)
        # After initial jobs completed, schedule_scan was called exactly once
        mock_instance.schedule_scan.assert_called_once_with(upload_id=101, folder_id=1)

    @patch("run_ci_compliance.FossologyClient")
    def test_failure_of_adj2nest_prevents_scan_scheduling(self, mock_client_cls):
        """
        Verify that if ununpack succeeds but adj2nest fails, schedule_scan() is never called.
        """
        mock_instance = mock_client_cls.return_value
        mock_instance.upload_archive.return_value = 101
        mock_instance.get_job_status.return_value = [
            {"id": 1, "name": "ununpack", "status": "Completed"},
            {"id": 2, "name": "adj2nest", "status": "Failed"},
        ]

        code = run_pipeline(
            archive_path=self.archive_path,
            server_url=self.server_url,
            token=self.token,
            sleep_fn=lambda s: None,
        )

        self.assertEqual(code, 2)
        mock_instance.schedule_scan.assert_not_called()
        mock_instance.get_license_findings.assert_not_called()

    @patch("run_ci_compliance.FossologyClient")
    def test_failure_of_ununpack_prevents_scan_scheduling(self, mock_client_cls):
        """
        Verify that if ununpack fails while adj2nest is Queued, schedule_scan() is never called.
        """
        mock_instance = mock_client_cls.return_value
        mock_instance.upload_archive.return_value = 101
        mock_instance.get_job_status.return_value = [
            {"id": 1, "name": "ununpack", "status": "Failed"},
            {"id": 2, "name": "adj2nest", "status": "Queued"},
        ]

        code = run_pipeline(
            archive_path=self.archive_path,
            server_url=self.server_url,
            token=self.token,
            sleep_fn=lambda s: None,
        )

        self.assertEqual(code, 2)
        mock_instance.schedule_scan.assert_not_called()
        mock_instance.get_license_findings.assert_not_called()

    @patch("run_ci_compliance.FossologyClient")
    def test_run_pipeline_only_ununpack_initially_scan_scheduled_only_after_adj2nest_completes(
        self, mock_client_cls
    ):
        """
        Verify end-to-end pipeline race condition:
        Poll 1: Only ununpack has appeared and completed (adj2nest not yet queued).
                schedule_scan() must NOT be called.
        Poll 2: adj2nest appears, running.
                schedule_scan() must NOT be called.
        Poll 3: adj2nest completes.
                schedule_scan() is now called.
        Poll 4: scan jobs (nomos, monk) complete.
        """
        mock_instance = mock_client_cls.return_value
        mock_instance.upload_archive.return_value = 101
        mock_instance.schedule_scan.return_value = 202

        poll_history = []

        def mock_get_job_status(upload_id=None, job_id=None):
            call_idx = len(poll_history)
            if call_idx == 0:
                # Poll 1: only ununpack completed
                res = [{"name": "ununpack", "status": "Completed"}]
            elif call_idx == 1:
                # Poll 2: adj2nest appears in queue, running
                res = [{"name": "ununpack", "status": "Completed"}, {"name": "adj2nest", "status": "Running"}]
            elif call_idx == 2:
                # Poll 3: adj2nest completes
                res = [{"name": "ununpack", "status": "Completed"}, {"name": "adj2nest", "status": "Completed"}]
            else:
                # Poll 4+: license scan jobs complete
                res = [
                    {"name": "ununpack", "status": "Completed"},
                    {"name": "adj2nest", "status": "Completed"},
                    {"name": "nomos", "status": "Completed"},
                    {"id": 202, "name": "monk", "status": "Completed"},
                ]

            poll_history.append((res, mock_instance.schedule_scan.call_count))
            return res

        mock_instance.get_job_status.side_effect = mock_get_job_status
        mock_instance.get_license_findings.return_value = []

        code = run_pipeline(
            archive_path=self.archive_path,
            server_url=self.server_url,
            token=self.token,
            poll_interval=1,
            timeout=10,
            sleep_fn=lambda s: None,
        )

        self.assertEqual(code, 0)
        # Poll 1 (only ununpack): schedule_scan had not been called
        self.assertEqual(poll_history[0][1], 0)
        # Poll 2 (adj2nest running): schedule_scan had not been called
        self.assertEqual(poll_history[1][1], 0)
        # Poll 3 (adj2nest completes): schedule_scan had still not been called at time of polling
        self.assertEqual(poll_history[2][1], 0)
        # After initial processing finished, schedule_scan was called exactly once
        mock_instance.schedule_scan.assert_called_once_with(upload_id=101, folder_id=1)
        mock_instance.get_license_findings.assert_called_once_with(upload_id=101)

    @patch("run_ci_compliance.FossologyClient")
    def test_timeout_during_initial_jobs_prevents_scan_scheduling(self, mock_client_cls):
        """
        Verify that if initial jobs (ununpack, adj2nest) time out, schedule_scan() is never called.
        """
        mock_instance = mock_client_cls.return_value
        mock_instance.upload_archive.return_value = 101
        mock_instance.get_job_status.return_value = [
            {"id": 1, "name": "ununpack", "status": "Running"},
            {"id": 2, "name": "adj2nest", "status": "Queued"},
        ]

        code = run_pipeline(
            archive_path=self.archive_path,
            server_url=self.server_url,
            token=self.token,
            timeout=0,  # immediate timeout
            sleep_fn=lambda s: None,
        )

        self.assertEqual(code, 2)
        mock_instance.schedule_scan.assert_not_called()
        mock_instance.get_license_findings.assert_not_called()

    @patch("run_ci_compliance.FossologyClient")
    def test_run_pipeline_fail_prohibited_license(self, mock_client_cls):
        mock_instance = mock_client_cls.return_value
        mock_instance.upload_archive.return_value = 101
        mock_instance.schedule_scan.return_value = 202
        mock_instance.get_job_status.return_value = [
            {"name": "ununpack", "status": "COMPLETED"},
            {"name": "adj2nest", "status": "COMPLETED"},
            {"name": "nomos", "status": "COMPLETED"},
            {"id": 202, "name": "monk", "status": "COMPLETED"},
        ]
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
        mock_instance.get_job_status.return_value = [{"id": 202, "name": "ununpack", "status": "Failed"}]

        code = run_pipeline(
            archive_path=self.archive_path,
            server_url=self.server_url,
            token=self.token,
            sleep_fn=lambda s: None,
        )
        self.assertEqual(code, 2)

    # -------------------------------------------------------------
    # Real FOSSology API Structure Tests (Upload-Level & Job ID records)
    # -------------------------------------------------------------

    def test_poll_jobs_waits_for_required_agents_after_upload_level_record(self):
        """
        An upload-level record without agent names does not satisfy the required
        initial processing jobs; polling continues until both agent names appear.
        """
        mock_client = MagicMock()
        mock_client.get_job_status.side_effect = [
            [{"id": 4, "name": "source_archive.zip", "uploadId": "4", "status": "Completed"}],
            [
                {"id": 5, "name": "ununpack", "uploadId": "4", "status": "Completed"},
                {"id": 6, "name": "adj2nest", "uploadId": "4", "status": "Completed"},
            ],
        ]

        sleep_calls = []
        jobs = poll_jobs(
            client=mock_client,
            upload_id=4,
            stage_name="initial upload-processing",
            required_job_names=("ununpack", "adj2nest"),
            poll_interval=2,
            timeout=10,
            sleep_fn=lambda s: sleep_calls.append(s),
        )

        self.assertEqual(len(jobs), 2)
        self.assertEqual(mock_client.get_job_status.call_count, 2)
        self.assertEqual(sleep_calls, [2])

    def test_poll_jobs_real_fossology_scan_stage_tracks_job_id_ignoring_initial_upload(self):
        """
        Verify that during license scan polling on a real FOSSology server,
        an already-completed initial upload job does NOT trigger false early completion;
        poll_jobs tracks the scheduled job_id until it completes.
        """
        mock_client = MagicMock()
        mock_client.get_job_status.side_effect = [
            # 1st poll: scheduled scan job 12 is Processing; initial job 4 is Completed
            [
                {"id": 12, "name": "source_archive.zip", "uploadId": "4", "status": "Processing"},
                {"id": 4, "name": "source_archive.zip", "uploadId": "4", "status": "Completed"},
            ],
            # 2nd poll: scheduled scan job 12 is Completed
            [
                {"id": 12, "name": "source_archive.zip", "uploadId": "4", "status": "Completed"},
                {"id": 4, "name": "source_archive.zip", "uploadId": "4", "status": "Completed"},
            ],
        ]

        sleep_calls = []
        jobs = poll_jobs(
            client=mock_client,
            upload_id=4,
            job_id=12,
            stage_name="license scan",
            required_job_names=("nomos", "monk"),
            ignored_job_names=("ununpack", "adj2nest"),
            poll_interval=1,
            timeout=10,
            sleep_fn=lambda s: sleep_calls.append(s),
        )

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["id"], 12)
        self.assertEqual(jobs[0]["status"], "Completed")
        self.assertEqual(mock_client.get_job_status.call_count, 2)
        self.assertEqual(sleep_calls, [1])

    def test_poll_jobs_prefers_job_id_when_both_ids_are_provided(self):
        mock_client = MagicMock()
        mock_client.get_job_status.return_value = {
            "id": 12,
            "name": "source_archive.zip",
            "status": "Completed",
        }

        jobs = poll_jobs(
            client=mock_client,
            upload_id=4,
            job_id=12,
            stage_name="license scan",
            poll_interval=1,
            timeout=10,
            sleep_fn=lambda s: None,
        )

        self.assertEqual(jobs[0]["id"], 12)
        mock_client.get_job_status.assert_called_once_with(job_id=12)
        self.assertNotIn(call(upload_id=4), mock_client.get_job_status.call_args_list)

    def test_poll_jobs_job_id_ignores_other_agent_jobs(self):
        mock_client = MagicMock()
        mock_client.get_job_status.return_value = [
            {"id": 12, "name": "nomos", "status": "Completed"},
            {"id": 13, "name": "monk", "status": "Failed"},
        ]

        jobs = poll_jobs(
            client=mock_client,
            upload_id=4,
            job_id=12,
            stage_name="license scan",
            required_job_names=("nomos", "monk"),
            ignored_job_names=("ununpack", "adj2nest"),
            poll_interval=1,
            timeout=10,
            sleep_fn=lambda s: None,
        )

        self.assertEqual(jobs, [{"id": 12, "name": "nomos", "status": "Completed"}])
        mock_client.get_job_status.assert_called_once_with(job_id=12)

    @patch("run_ci_compliance.FossologyClient")
    def test_run_pipeline_real_fossology_two_stage_execution(self, mock_client_cls):
        """
        Verify end-to-end pipeline execution with real FOSSology API response payloads.
        """
        mock_instance = mock_client_cls.return_value
        mock_instance.upload_archive.return_value = 4
        mock_instance.schedule_scan.return_value = 12

        mock_instance.get_job_status.side_effect = [
            # Stage 1: An upload-level record alone does not prove both agents completed.
            [{"id": 4, "name": "source_archive.zip", "uploadId": "4", "status": "Processing"}],
            [{"id": 4, "name": "source_archive.zip", "uploadId": "4", "status": "Completed"}],
            [
                {"id": 5, "name": "ununpack", "uploadId": "4", "status": "Completed"},
                {"id": 6, "name": "adj2nest", "uploadId": "4", "status": "Completed"},
            ],
            # Stage 2: Scan scheduled (job 12), initial job 4 also present
            [
                {"id": 12, "name": "source_archive.zip", "uploadId": "4", "status": "Processing"},
                {"id": 4, "name": "source_archive.zip", "uploadId": "4", "status": "Completed"},
            ],
            [
                {"id": 12, "name": "source_archive.zip", "uploadId": "4", "status": "Completed"},
                {"id": 4, "name": "source_archive.zip", "uploadId": "4", "status": "Completed"},
            ],
        ]

        mock_instance.get_license_findings.return_value = [
            {"filePath": "src/main.py", "findings": {"scanner": ["MIT"], "conclusion": ["MIT"]}},
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
        self.assertEqual(mock_instance.get_job_status.call_count, 5)
        mock_instance.upload_archive.assert_called_once()
        mock_instance.schedule_scan.assert_called_once_with(upload_id=4, folder_id=1)
        mock_instance.get_license_findings.assert_called_once_with(upload_id=4)

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
