"""
Unit tests for the FOSSology API Client.
Using Python's built-in unittest framework and mock objects.
"""

import io
import os
import tempfile
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from fossology_client import (
    DEFAULT_SCAN_SPEC,
    FossologyAuthError,
    FossologyClient,
    FossologyClientError,
    FossologyHttpError,
    FossologyResponseError,
    FossologyTimeoutError,
    build_api_url,
    build_upload_endpoint,
    create_multipart_payload,
    get_fossology_config,
    get_job_status,
    get_license_findings,
    schedule_scan,
    upload_archive,
)


class TestFossologyClient(unittest.TestCase):

    def setUp(self):
        # Create a temporary dummy zip archive for testing
        self.temp_file = tempfile.NamedTemporaryFile("wb", delete=False, suffix=".zip")
        self.temp_file.write(b"PK\x03\x04dummy_archive_content")
        self.temp_file.close()
        self.archive_path = self.temp_file.name

        self.test_url = "http://fossology.example.com"
        self.test_token = "secret_bearer_token_12345"

    def tearDown(self):
        if os.path.exists(self.archive_path):
            os.remove(self.archive_path)

    # -------------------------------------------------------------
    # Configuration and Endpoint Construction Tests
    # -------------------------------------------------------------

    def test_get_fossology_config_from_arguments(self):
        url, token = get_fossology_config(server_url="http://test.org/", token="tok123")
        self.assertEqual(url, "http://test.org")
        self.assertEqual(token, "tok123")

    @patch.dict(os.environ, {"FOSSOLOGY_URL": "https://hosted.fossology.org", "FOSSOLOGY_TOKEN": "env_tok"})
    def test_get_fossology_config_from_env(self):
        url, token = get_fossology_config()
        self.assertEqual(url, "https://hosted.fossology.org")
        self.assertEqual(token, "env_tok")

    @patch.dict(os.environ, {"FOSSOLOGY_SERVER_URL": "https://alt.fossology.org", "FOSSOLOGY_TOKEN": "alt_tok"}, clear=True)
    def test_get_fossology_config_from_alt_env(self):
        url, token = get_fossology_config()
        self.assertEqual(url, "https://alt.fossology.org")
        self.assertEqual(token, "alt_tok")

    @patch.dict(os.environ, {}, clear=True)
    def test_get_fossology_config_missing_url(self):
        with self.assertRaises(FossologyClientError) as ctx:
            get_fossology_config(token="tok")
        self.assertIn("FOSSOLOGY_URL", str(ctx.exception))

    @patch.dict(os.environ, {"FOSSOLOGY_URL": "http://test.org"}, clear=True)
    def test_get_fossology_config_missing_token(self):
        with self.assertRaises(FossologyAuthError) as ctx:
            get_fossology_config()
        self.assertIn("FOSSOLOGY_TOKEN", str(ctx.exception))

    def test_build_upload_endpoint_variations(self):
        self.assertEqual(
            build_upload_endpoint("http://example.com"),
            "http://example.com/repo/api/v1/uploads",
        )
        self.assertEqual(
            build_upload_endpoint("http://example.com/"),
            "http://example.com/repo/api/v1/uploads",
        )
        self.assertEqual(
            build_upload_endpoint("http://example.com/repo"),
            "http://example.com/repo/api/v1/uploads",
        )
        self.assertEqual(
            build_upload_endpoint("http://example.com/repo/api/v1"),
            "http://example.com/repo/api/v1/uploads",
        )

    # -------------------------------------------------------------
    # Multipart Payload Construction Tests
    # -------------------------------------------------------------

    def test_create_multipart_payload(self):
        payload, content_type = create_multipart_payload(self.archive_path, "test_boundary")
        self.assertIn("multipart/form-data; boundary=test_boundary", content_type)
        payload_str = payload.decode("latin-1")
        self.assertIn('name="fileInput"', payload_str)
        self.assertIn(os.path.basename(self.archive_path), payload_str)
        self.assertIn("dummy_archive_content", payload_str)

    def test_create_multipart_payload_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            create_multipart_payload("non_existent_file.zip", "boundary123")

    # -------------------------------------------------------------
    # Successful Upload Tests (Mocked)
    # -------------------------------------------------------------

    @patch("urllib.request.urlopen")
    def test_upload_archive_success(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"code": 201, "message": "42", "type": "INFO"}'
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        upload_id = upload_archive(
            archive_path=self.archive_path,
            folder_id=3,
            upload_description="CI build upload",
            upload_type="file",
            public="public",
            server_url=self.test_url,
            token=self.test_token,
            timeout=30,
        )

        self.assertEqual(upload_id, 42)

        # Inspect the request that was made
        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        self.assertEqual(req.get_full_url(), "http://fossology.example.com/repo/api/v1/uploads")
        self.assertEqual(req.get_method(), "POST")

        # Headers check
        headers = req.headers
        self.assertEqual(headers["Authorization"], "Bearer secret_bearer_token_12345")
        self.assertEqual(headers["Folderid"], "3")
        self.assertEqual(headers["Uploaddescription"], "CI build upload")
        self.assertEqual(headers["Uploadtype"], "file")
        self.assertEqual(headers["Public"], "public")
        self.assertIn("multipart/form-data; boundary=", headers["Content-type"])

        # Payload check
        self.assertIn(b'name="fileInput"', req.data)
        self.assertIn(b"dummy_archive_content", req.data)

    @patch("urllib.request.urlopen")
    def test_fossology_client_class_success(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"code": 201, "message": "99", "type": "INFO"}'
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        client = FossologyClient(server_url=self.test_url, token=self.test_token)
        upload_id = client.upload_archive(archive_path=self.archive_path)

        self.assertEqual(upload_id, 99)

    # -------------------------------------------------------------
    # Failure Cases & Error Handling Tests (Mocked)
    # -------------------------------------------------------------

    def test_upload_missing_archive_file(self):
        with self.assertRaises(FileNotFoundError):
            upload_archive(
                archive_path="non_existent_archive_xyz.zip",
                server_url=self.test_url,
                token=self.test_token,
            )

    @patch("urllib.request.urlopen")
    def test_upload_auth_failure_401(self, mock_urlopen):
        err = urllib.error.HTTPError(
            url="http://fossology.example.com",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=io.BytesIO(b'{"message": "Invalid token"}'),
        )
        mock_urlopen.side_effect = err

        with self.assertRaises(FossologyAuthError) as ctx:
            upload_archive(
                archive_path=self.archive_path,
                server_url=self.test_url,
                token=self.test_token,
            )
        self.assertIn("401", str(ctx.exception))

    @patch("urllib.request.urlopen")
    def test_upload_auth_failure_403(self, mock_urlopen):
        err = urllib.error.HTTPError(
            url="http://fossology.example.com",
            code=403,
            msg="Forbidden",
            hdrs={},
            fp=io.BytesIO(b'{"message": "Permission denied"}'),
        )
        mock_urlopen.side_effect = err

        with self.assertRaises(FossologyAuthError) as ctx:
            upload_archive(
                archive_path=self.archive_path,
                server_url=self.test_url,
                token=self.test_token,
            )
        self.assertIn("403", str(ctx.exception))

    @patch("urllib.request.urlopen")
    def test_upload_http_error_500(self, mock_urlopen):
        err = urllib.error.HTTPError(
            url="http://fossology.example.com",
            code=500,
            msg="Internal Server Error",
            hdrs={},
            fp=io.BytesIO(b'Server exploded'),
        )
        mock_urlopen.side_effect = err

        with self.assertRaises(FossologyHttpError) as ctx:
            upload_archive(
                archive_path=self.archive_path,
                server_url=self.test_url,
                token=self.test_token,
            )
        self.assertEqual(ctx.exception.status_code, 500)
        self.assertIn("500", str(ctx.exception))

    @patch("urllib.request.urlopen")
    def test_upload_timeout(self, mock_urlopen):
        mock_urlopen.side_effect = TimeoutError("Connection timed out")

        with self.assertRaises(FossologyTimeoutError) as ctx:
            upload_archive(
                archive_path=self.archive_path,
                server_url=self.test_url,
                token=self.test_token,
                timeout=5,
            )
        self.assertIn("timed out", str(ctx.exception))

    @patch("urllib.request.urlopen")
    def test_upload_invalid_json_response(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = b"<html>502 Bad Gateway</html>"
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        with self.assertRaises(FossologyResponseError) as ctx:
            upload_archive(
                archive_path=self.archive_path,
                server_url=self.test_url,
                token=self.test_token,
            )
        self.assertIn("Invalid JSON", str(ctx.exception))

    @patch("urllib.request.urlopen")
    def test_upload_missing_upload_id_in_response(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"status": "ok"}'
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        with self.assertRaises(FossologyResponseError) as ctx:
            upload_archive(
                archive_path=self.archive_path,
                server_url=self.test_url,
                token=self.test_token,
            )
        self.assertIn("Upload ID not found", str(ctx.exception))

    @patch("urllib.request.urlopen")
    def test_upload_application_level_error_code(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"code": 403, "message": "Target folder does not exist"}'
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        with self.assertRaises(FossologyAuthError) as ctx:
            upload_archive(
                archive_path=self.archive_path,
                server_url=self.test_url,
                token=self.test_token,
            )
        self.assertIn("Target folder does not exist", str(ctx.exception))

    # -------------------------------------------------------------
    # schedule_scan Tests (Mocked)
    # -------------------------------------------------------------

    @patch("urllib.request.urlopen")
    def test_schedule_scan_success(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"code": 201, "message": 88, "type": "INFO"}'
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        job_id = schedule_scan(
            upload_id=42,
            folder_id=1,
            server_url=self.test_url,
            token=self.test_token,
            timeout=30,
        )

        self.assertEqual(job_id, 88)

        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        self.assertEqual(req.get_full_url(), "http://fossology.example.com/repo/api/v1/jobs")
        self.assertEqual(req.get_method(), "POST")

        headers = req.headers
        self.assertEqual(headers["Authorization"], "Bearer secret_bearer_token_12345")
        self.assertEqual(headers["Folderid"], "1")
        self.assertEqual(headers["Uploadid"], "42")
        self.assertEqual(headers["Content-type"], "application/json")
        self.assertEqual(headers["Accept"], "application/json")

        # Verify scan specification includes nomos, monk and deciders
        import json
        body = json.loads(req.data.decode("utf-8"))
        self.assertTrue(body["analysis"]["nomos"])
        self.assertTrue(body["analysis"]["monk"])
        self.assertTrue(body["decider"]["nomos_monk"])

    @patch("urllib.request.urlopen")
    def test_schedule_scan_client_method(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"code": 201, "message": "91", "type": "INFO"}'
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        client = FossologyClient(server_url=self.test_url, token=self.test_token)
        job_id = client.schedule_scan(upload_id=42, folder_id=2)

        self.assertEqual(job_id, 91)

    @patch("urllib.request.urlopen")
    def test_schedule_scan_missing_job_id(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"status": "queued"}'
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        with self.assertRaises(FossologyResponseError) as ctx:
            schedule_scan(upload_id=42, server_url=self.test_url, token=self.test_token)
        self.assertIn("Job ID not found", str(ctx.exception))

    @patch("urllib.request.urlopen")
    def test_schedule_scan_http_error(self, mock_urlopen):
        err = urllib.error.HTTPError(
            url="http://fossology.example.com",
            code=500,
            msg="Internal Error",
            hdrs={},
            fp=io.BytesIO(b'{"message": "Scheduler down"}'),
        )
        mock_urlopen.side_effect = err

        with self.assertRaises(FossologyHttpError) as ctx:
            schedule_scan(upload_id=42, server_url=self.test_url, token=self.test_token)
        self.assertEqual(ctx.exception.status_code, 500)

    # -------------------------------------------------------------
    # get_job_status Tests (Mocked)
    # -------------------------------------------------------------

    @patch("urllib.request.urlopen")
    def test_get_job_status_success(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = (
            b'[{"jobId": 13, "jobName": "source_archive.zip", "uploadId": 42, '
            b'"jobQueue": [{"jobQueueId": 19, "jobQueueType": "ununpack", "status": "COMPLETED"}]}]'
        )
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        status_info = get_job_status(
            upload_id=42,
            server_url=self.test_url,
            token=self.test_token,
        )

        self.assertIsInstance(status_info, list)
        self.assertEqual(len(status_info), 1)
        self.assertEqual(status_info[0]["jobQueue"][0]["status"], "COMPLETED")

        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        self.assertEqual(req.get_full_url(), "http://fossology.example.com/repo/api/v1/jobs/history?upload=42")
        self.assertEqual(req.get_method(), "GET")
        self.assertEqual(req.headers["Authorization"], "Bearer secret_bearer_token_12345")
        self.assertEqual(req.headers["Accept"], "application/json")

    @patch("urllib.request.urlopen")
    def test_get_job_status_positional_arguments_remain_compatible(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = b'[]'
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        get_job_status(42, self.test_url, self.test_token, 17)

        req = mock_urlopen.call_args.args[0]
        self.assertEqual(req.get_full_url(), "http://fossology.example.com/repo/api/v1/jobs/history?upload=42")
        self.assertEqual(mock_urlopen.call_args.kwargs["timeout"], 17)

    @patch("urllib.request.urlopen")
    def test_get_job_status_by_job_id(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = (
            b'{"id": 88, "name": "source_archive.zip", "status": "COMPLETED", "uploadId": "42"}'
        )
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        status_info = get_job_status(
            job_id=88,
            server_url=self.test_url,
            token=self.test_token,
        )

        self.assertIsInstance(status_info, dict)
        self.assertEqual(status_info["id"], 88)
        self.assertEqual(status_info["status"], "COMPLETED")

        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        self.assertEqual(req.get_full_url(), "http://fossology.example.com/repo/api/v1/jobs/88")
        self.assertEqual(req.get_method(), "GET")

    def test_get_job_status_missing_params_raises(self):
        with self.assertRaises(ValueError):
            get_job_status(server_url=self.test_url, token=self.test_token)

    @patch("urllib.request.urlopen")
    def test_get_job_status_client_method(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = (
            b'[{"jobId": 13, "jobName": "source_archive.zip", "uploadId": 42, '
            b'"jobQueue": [{"jobQueueId": 19, "jobQueueType": "ununpack", "status": "PROCESSING"}]}]'
        )
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        client = FossologyClient(server_url=self.test_url, token=self.test_token)
        jobs = client.get_job_status(upload_id=42)

        self.assertEqual(jobs[0]["jobQueue"][0]["status"], "PROCESSING")

    @patch("urllib.request.urlopen")
    def test_get_job_status_client_positional_timeout_remains_compatible(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = b'[]'
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        client = FossologyClient(server_url=self.test_url, token=self.test_token)
        client.get_job_status(42, 17)

        req = mock_urlopen.call_args.args[0]
        self.assertEqual(req.get_full_url(), "http://fossology.example.com/repo/api/v1/jobs/history?upload=42")
        self.assertEqual(mock_urlopen.call_args.kwargs["timeout"], 17)

    @patch("urllib.request.urlopen")
    def test_get_job_status_timeout(self, mock_urlopen):
        mock_urlopen.side_effect = TimeoutError("Timed out waiting for job status")

        with self.assertRaises(FossologyTimeoutError):
            get_job_status(upload_id=42, server_url=self.test_url, token=self.test_token)

    # -------------------------------------------------------------
    # get_license_findings Tests (Mocked)
    # -------------------------------------------------------------

    @patch("urllib.request.urlopen")
    def test_get_license_findings_success(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = (
            b'[{"filePath": "src/app.py", "findings": {"scanner": ["MIT"], "conclusion": ["MIT"]}}]'
        )
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        findings = get_license_findings(
            upload_id=42,
            server_url=self.test_url,
            token=self.test_token,
        )

        self.assertIsInstance(findings, list)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["filePath"], "src/app.py")
        self.assertEqual(findings[0]["findings"]["scanner"], ["MIT"])

        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        self.assertEqual(
            req.get_full_url(),
            "http://fossology.example.com/repo/api/v1/uploads/42/licenses",
        )
        self.assertEqual(req.get_method(), "GET")
        self.assertEqual(req.headers["Authorization"], "Bearer secret_bearer_token_12345")
        self.assertEqual(req.headers["Accept"], "application/json")

    @patch("urllib.request.urlopen")
    def test_get_license_findings_client_method(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = (
            b'[{"filePath": "lib.c", "findings": {"scanner": ["Apache-2.0"], "conclusion": null}}]'
        )
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        client = FossologyClient(server_url=self.test_url, token=self.test_token)
        findings = client.get_license_findings(upload_id=42)

        self.assertEqual(findings[0]["filePath"], "lib.c")

    @patch("urllib.request.urlopen")
    def test_get_license_findings_auth_failure(self, mock_urlopen):
        err = urllib.error.HTTPError(
            url="http://fossology.example.com",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=io.BytesIO(b'{"message": "Token expired"}'),
        )
        mock_urlopen.side_effect = err

        client = FossologyClient(server_url=self.test_url, token=self.test_token)
        with self.assertRaises(FossologyAuthError) as ctx:
            client.get_license_findings(upload_id=42)
        self.assertIn("401", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
