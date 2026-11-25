# SPDX-License-Identifier: MIT
"""
FOSSology API Client
A beginner-friendly module to interact with the hosted FOSSology REST API,
specifically for uploading source archives, scheduling scan agents, tracking jobs,
and retrieving compliance license findings.
"""

import json
import os
import socket
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlencode
from typing import Any, Dict, Optional, Tuple, Union


# Default scan specification enabling Nomos, Monk, and supporting analysis/decider agents.
DEFAULT_SCAN_SPEC: Dict[str, Any] = {
    "analysis": {
        "bucket": True,
        "copyright_email_author": True,
        "ecc": True,
        "keyword": True,
        "mime": True,
        "monk": True,
        "nomos": True,
        "package": True,
    },
    "decider": {
        "nomos_monk": True,
        "bulk_reused": True,
        "new_scanner": True,
    },
}


class FossologyClientError(Exception):
    """Base exception for all FOSSology client errors."""
    pass


class FossologyAuthError(FossologyClientError):
    """Raised when authentication fails (missing token or HTTP 401/403)."""
    pass


class FossologyHttpError(FossologyClientError):
    """Raised when an HTTP error occurs (e.g., 400, 404, 500)."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


class FossologyTimeoutError(FossologyClientError):
    """Raised when a request to FOSSology times out."""
    pass


class FossologyResponseError(FossologyClientError):
    """Raised when the server response is invalid JSON or lacks required fields."""
    pass


def get_fossology_config(
    server_url: Optional[str] = None,
    token: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Retrieve FOSSology server URL and API token.
    Prioritizes passed arguments, falling back to environment variables.

    Environment variables:
        FOSSOLOGY_URL or FOSSOLOGY_SERVER_URL: Base URL of the FOSSology server.
        FOSSOLOGY_TOKEN: API Bearer token for authentication.

    Raises:
        FossologyClientError: If the server URL is not provided or configured.
        FossologyAuthError: If the API token is not provided or configured.
    """
    url = server_url or os.environ.get("FOSSOLOGY_URL") or os.environ.get("FOSSOLOGY_SERVER_URL")
    if not url or not url.strip():
        raise FossologyClientError(
            "FOSSology server URL not configured. Set FOSSOLOGY_URL environment variable "
            "or pass server_url directly."
        )

    auth_token = token or os.environ.get("FOSSOLOGY_TOKEN")
    if not auth_token or not auth_token.strip():
        raise FossologyAuthError(
            "FOSSology token not configured. Set FOSSOLOGY_TOKEN environment variable "
            "or pass token directly."
        )

    return url.strip().rstrip("/"), auth_token.strip()


def build_api_url(base_url: str, path: str) -> str:
    """
    Construct a full API endpoint URL from the base server URL and API sub-path.
    Normalizes '/repo/api/v1' prefix safely across base URL variants.
    """
    normalized = base_url.rstrip("/")
    clean_path = path.lstrip("/")

    if normalized.endswith("/repo/api/v1"):
        api_root = normalized
    elif normalized.endswith("/repo"):
        api_root = f"{normalized}/api/v1"
    else:
        api_root = f"{normalized}/repo/api/v1"

    return f"{api_root}/{clean_path}"


def build_upload_endpoint(base_url: str) -> str:
    """
    Construct the full upload endpoint URL from the base server URL.
    Ensures '/repo/api/v1/uploads' is targeted without duplicate paths.
    """
    return build_api_url(base_url, "uploads")


def create_multipart_payload(
    archive_path: str,
    boundary: str,
) -> Tuple[bytes, str]:
    """
    Construct an RFC-compliant multipart/form-data payload with field 'fileInput'.

    Returns:
        (payload_bytes, content_type_header)
    """
    if not os.path.exists(archive_path):
        raise FileNotFoundError(f"Archive file not found: {archive_path}")

    filename = os.path.basename(archive_path)

    with open(archive_path, "rb") as f:
        file_bytes = f.read()

    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="fileInput"; filename="{filename}"\r\n'
        f"Content-Type: application/zip\r\n\r\n"
    ).encode("utf-8")

    footer = f"\r\n--{boundary}--\r\n".encode("utf-8")

    payload = header + file_bytes + footer
    content_type = f"multipart/form-data; boundary={boundary}"

    return payload, content_type


def _send_request(req: urllib.request.Request, timeout: int = 60) -> Any:
    """
    Execute an HTTP request with uniform error handling and JSON response parsing.
    """
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            response_bytes = response.read()
            response_text = response_bytes.decode("utf-8")
    except urllib.error.HTTPError as err:
        error_body = ""
        try:
            error_body = err.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        finally:
            try:
                err.close()
            except Exception:
                pass

        if err.code in (401, 403):
            raise FossologyAuthError(
                f"Authentication failed (HTTP {err.code}): {error_body or err.reason}"
            ) from err

        raise FossologyHttpError(
            status_code=err.code,
            message=f"Request failed with HTTP {err.code}: {error_body or err.reason}",
        ) from err
    except (urllib.error.URLError, TimeoutError, socket.timeout) as err:
        reason = getattr(err, "reason", None)
        if isinstance(reason, socket.timeout) or isinstance(err, (TimeoutError, socket.timeout)):
            raise FossologyTimeoutError(
                f"Request timed out after {timeout} seconds: {err}"
            ) from err
        raise FossologyClientError(f"Connection error to FOSSology server: {err}") from err
    except Exception as err:
        raise FossologyClientError(f"Unexpected error: {err}") from err

    # Parse JSON response
    try:
        data = json.loads(response_text)
    except (json.JSONDecodeError, ValueError) as err:
        snippet = response_text[:200] if response_text else "<empty>"
        raise FossologyResponseError(
            f"Invalid JSON received from FOSSology server: {snippet}"
        ) from err

    # Check for application-level error code in response body
    if isinstance(data, dict) and "code" in data:
        code_val = data.get("code")
        if isinstance(code_val, int) and code_val >= 400:
            msg = data.get("message", "Unknown error")
            if code_val in (401, 403):
                raise FossologyAuthError(f"Authentication failed (code {code_val}): {msg}")
            raise FossologyHttpError(
                status_code=code_val,
                message=f"FOSSology API error (code {code_val}): {msg}",
            )

    return data


def upload_archive(
    archive_path: str,
    folder_id: int = 1,
    upload_description: str = "",
    upload_type: str = "file",
    public: str = "public",
    server_url: Optional[str] = None,
    token: Optional[str] = None,
    timeout: int = 60,
) -> Union[int, str]:
    """
    Upload a ZIP archive to FOSSology via POST /repo/api/v1/uploads.

    Args:
        archive_path: Local path to the ZIP archive file.
        folder_id: FOSSology target folder ID (default: 1).
        upload_description: Optional description for the upload.
        upload_type: Type of upload (default: 'file').
        public: Visibility permission ('public' or 'private', default: 'public').
        server_url: Optional server URL (defaults to FOSSOLOGY_URL env var).
        token: Optional Bearer token (defaults to FOSSOLOGY_TOKEN env var).
        timeout: Request timeout in seconds (default: 60).

    Returns:
        Upload ID (int if numeric, else str) returned by FOSSology.
    """
    base_url, auth_token = get_fossology_config(server_url=server_url, token=token)
    endpoint = build_upload_endpoint(base_url)

    boundary = f"----FossologyBoundary{uuid.uuid4().hex}"
    payload, content_type = create_multipart_payload(archive_path, boundary)

    headers = {
        "Authorization": f"Bearer {auth_token}",
        "folderId": str(folder_id),
        "uploadDescription": str(upload_description),
        "uploadType": str(upload_type),
        "public": str(public),
        "Content-Type": content_type,
        "Content-Length": str(len(payload)),
    }

    req = urllib.request.Request(
        url=endpoint,
        data=payload,
        headers=headers,
        method="POST",
    )

    data = _send_request(req, timeout=timeout)

    # Extract upload ID (FOSSology convention: 'message' contains the upload ID)
    raw_id = None
    if isinstance(data, dict):
        raw_id = data.get("message") or data.get("id")

    if raw_id is None:
        raise FossologyResponseError(
            f"Upload ID not found in FOSSology response: {data}"
        )

    try:
        return int(raw_id)
    except (ValueError, TypeError):
        return str(raw_id)


def schedule_scan(
    upload_id: Union[int, str],
    folder_id: int = 1,
    scan_spec: Optional[Dict[str, Any]] = None,
    server_url: Optional[str] = None,
    token: Optional[str] = None,
    timeout: int = 60,
) -> Union[int, str]:
    """
    Schedule analysis and decider agents for an upload via POST /repo/api/v1/jobs.

    Args:
        upload_id: Target upload ID to analyze.
        folder_id: Target folder ID (default: 1).
        scan_spec: Custom scan specification dictionary, or None for DEFAULT_SCAN_SPEC.
        server_url: Optional server URL (defaults to FOSSOLOGY_URL env var).
        token: Optional Bearer token (defaults to FOSSOLOGY_TOKEN env var).
        timeout: Request timeout in seconds (default: 60).

    Returns:
        Job ID (int if numeric, else str) returned by FOSSology.
    """
    base_url, auth_token = get_fossology_config(server_url=server_url, token=token)
    endpoint = build_api_url(base_url, "jobs")

    spec = scan_spec if scan_spec is not None else DEFAULT_SCAN_SPEC
    payload = json.dumps(spec).encode("utf-8")

    headers = {
        "Authorization": f"Bearer {auth_token}",
        "folderId": str(folder_id),
        "uploadId": str(upload_id),
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Content-Length": str(len(payload)),
    }

    req = urllib.request.Request(
        url=endpoint,
        data=payload,
        headers=headers,
        method="POST",
    )

    data = _send_request(req, timeout=timeout)

    raw_id = None
    if isinstance(data, dict):
        raw_id = data.get("message") or data.get("id")

    if raw_id is None:
        raise FossologyResponseError(
            f"Job ID not found in FOSSology response: {data}"
        )

    try:
        return int(raw_id)
    except (ValueError, TypeError):
        return str(raw_id)


def get_job_status(
    upload_id: Optional[Union[int, str]] = None,
    server_url: Optional[str] = None,
    token: Optional[str] = None,
    timeout: int = 60,
    job_id: Optional[Union[int, str]] = None,
) -> Any:
    """
    Retrieve upload job history via GET /repo/api/v1/jobs/history?upload={upload_id}
    or for a specific job via GET /repo/api/v1/jobs/{job_id}.

    Args:
        upload_id: Target upload ID (optional if job_id is provided).
        job_id: Target job ID (optional if upload_id is provided).
        server_url: Optional server URL (defaults to FOSSOLOGY_URL env var).
        token: Optional Bearer token (defaults to FOSSOLOGY_TOKEN env var).
        timeout: Request timeout in seconds (default: 60).

    Returns:
        Parsed JSON job status information (list or dict).
    """
    if upload_id is None and job_id is None:
        raise ValueError("Either upload_id or job_id must be provided to get_job_status")

    base_url, auth_token = get_fossology_config(server_url=server_url, token=token)

    if job_id is not None:
        endpoint = build_api_url(base_url, f"jobs/{job_id}")
    else:
        endpoint = build_api_url(base_url, f"jobs/history?upload={upload_id}")

    headers = {
        "Authorization": f"Bearer {auth_token}",
        "Accept": "application/json",
    }

    req = urllib.request.Request(
        url=endpoint,
        headers=headers,
        method="GET",
    )

    return _send_request(req, timeout=timeout)


def get_license_findings(
    upload_id: Union[int, str],
    server_url: Optional[str] = None,
    token: Optional[str] = None,
    timeout: int = 60,
    agent: str = "nomos,monk",
    containers: bool = False,
) -> Any:
    """
    Retrieve license scan findings via GET /repo/api/v1/uploads/{upload_id}/licenses?agent=...

    Args:
        upload_id: Target upload ID.
        server_url: Optional server URL (defaults to FOSSOLOGY_URL env var).
        token: Optional Bearer token (defaults to FOSSOLOGY_TOKEN env var).
        timeout: Request timeout in seconds (default: 60).
        agent: Comma-separated license agent names to retrieve (default: 'nomos,monk').
        containers: Whether to include archive/container entries (default: False).

    Returns:
        Parsed findings list from the JSON response.
    """
    base_url, auth_token = get_fossology_config(server_url=server_url, token=token)
    query = urlencode({"agent": agent, "containers": str(containers).lower()})
    endpoint = build_api_url(base_url, f"uploads/{upload_id}/licenses?{query}")

    headers = {
        "Authorization": f"Bearer {auth_token}",
        "Accept": "application/json",
    }

    req = urllib.request.Request(
        url=endpoint,
        headers=headers,
        method="GET",
    )

    return _send_request(req, timeout=timeout)


class FossologyClient:
    """
    Object-oriented client wrapper for the FOSSology REST API.
    """

    def __init__(
        self,
        server_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout: int = 60,
    ):
        self.server_url, self.token = get_fossology_config(server_url=server_url, token=token)
        self.timeout = timeout

    def upload_archive(
        self,
        archive_path: str,
        folder_id: int = 1,
        upload_description: str = "",
        upload_type: str = "file",
        public: str = "public",
        timeout: Optional[int] = None,
    ) -> Union[int, str]:
        """
        Upload a source archive to FOSSology via POST /repo/api/v1/uploads.
        """
        return upload_archive(
            archive_path=archive_path,
            folder_id=folder_id,
            upload_description=upload_description,
            upload_type=upload_type,
            public=public,
            server_url=self.server_url,
            token=self.token,
            timeout=timeout or self.timeout,
        )

    def schedule_scan(
        self,
        upload_id: Union[int, str],
        folder_id: int = 1,
        scan_spec: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> Union[int, str]:
        """
        Schedule analysis (nomos, monk) and decider agents for an upload via POST /repo/api/v1/jobs.
        """
        return schedule_scan(
            upload_id=upload_id,
            folder_id=folder_id,
            scan_spec=scan_spec,
            server_url=self.server_url,
            token=self.token,
            timeout=timeout or self.timeout,
        )

    def get_job_status(
        self,
        upload_id: Optional[Union[int, str]] = None,
        timeout: Optional[int] = None,
        job_id: Optional[Union[int, str]] = None,
    ) -> Any:
        """
        Retrieve upload job history via GET /repo/api/v1/jobs/history?upload={upload_id}
        or for a specific job via GET /repo/api/v1/jobs/{job_id}.
        """
        return get_job_status(
            upload_id=upload_id,
            job_id=job_id,
            server_url=self.server_url,
            token=self.token,
            timeout=timeout or self.timeout,
        )

    def get_license_findings(
        self,
        upload_id: Union[int, str],
        timeout: Optional[int] = None,
        agent: str = "nomos,monk",
        containers: bool = False,
    ) -> Any:
        """
        Retrieve license scan findings for an upload via GET /repo/api/v1/uploads/{upload_id}/licenses?agent=...
        """
        return get_license_findings(
            upload_id=upload_id,
            server_url=self.server_url,
            token=self.token,
            timeout=timeout or self.timeout,
            agent=agent,
            containers=containers,
        )
