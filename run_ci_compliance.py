"""
FOSSology CI Compliance Pipeline Runner
Connects fossology_client and compliance_checker to run automated end-to-end
compliance verification in CI workflows.
"""

import argparse
import os
import sys
import time
from typing import Any, Dict, List, Optional, Union

from compliance_checker import (
    load_policy,
    print_summary,
    process_report,
)
from fossology_client import (
    FossologyAuthError,
    FossologyClient,
    FossologyClientError,
    FossologyHttpError,
    FossologyResponseError,
    FossologyTimeoutError,
)


def poll_jobs(
    client: FossologyClient,
    upload_id: Union[int, str],
    poll_interval: int = 10,
    timeout: int = 900,
    sleep_fn=time.sleep,
) -> List[Dict[str, Any]]:
    """
    Poll FOSSology job status every poll_interval seconds until all jobs complete,
    a job fails, or the timeout is exceeded.

    Args:
        client: Configured FossologyClient instance.
        upload_id: Target upload ID.
        poll_interval: Seconds between polls (default: 10).
        timeout: Maximum seconds to wait before timing out (default: 900 / 15 minutes).
        sleep_fn: Sleep function (injectable for testing).

    Returns:
        List of completed job objects.

    Raises:
        FossologyTimeoutError: If jobs do not complete within the timeout.
        RuntimeError: If any job reports FAILED status.
    """
    start_time = time.time()
    print(f"Waiting for scan jobs to finish (polling every {poll_interval}s, timeout {timeout}s)...")

    while True:
        elapsed = time.time() - start_time
        if elapsed >= timeout:
            raise FossologyTimeoutError(
                f"Scan timed out after {int(elapsed)} seconds. FOSSology jobs did not finish."
            )

        job_info = client.get_job_status(upload_id=upload_id)

        # Normalize job_info to a list
        if isinstance(job_info, list):
            jobs = job_info
        elif isinstance(job_info, dict):
            # In case the response wraps jobs under a key
            jobs = job_info.get("jobs", [job_info])
        else:
            jobs = []

        # Handle empty job lists (jobs may still be initializing in the scheduler queue)
        if not jobs:
            print("  No active jobs listed yet. Waiting for jobs to be scheduled...")
            sleep_fn(poll_interval)
            continue

        statuses = [job.get("status", "UNKNOWN") for job in jobs if isinstance(job, dict)]

        # Check for job failures
        failed_jobs = [job for job in jobs if isinstance(job, dict) and job.get("status") == "FAILED"]
        if failed_jobs:
            failed_names = ", ".join(
                str(j.get("name", j.get("id", "Unknown"))) for j in failed_jobs
            )
            raise RuntimeError(f"FOSSology scan job(s) failed: {failed_names}")

        # Check if all jobs are completed
        all_completed = all(status == "COMPLETED" for status in statuses)
        if all_completed and len(statuses) > 0:
            print(f"  All {len(statuses)} scan job(s) completed successfully.")
            return jobs

        # Show progress breakdown
        status_summary = ", ".join(f"{s}: {statuses.count(s)}" for s in set(statuses))
        print(f"  Scan in progress ({status_summary}). Elapsed: {int(elapsed)}s...")

        sleep_fn(poll_interval)


def run_pipeline(
    archive_path: str,
    policy_path: Optional[str] = None,
    folder_id: int = 1,
    poll_interval: int = 10,
    timeout: int = 900,
    server_url: Optional[str] = None,
    token: Optional[str] = None,
    sleep_fn=time.sleep,
) -> int:
    """
    Execute the full CI compliance pipeline:
      1. Upload source archive to FOSSology.
      2. Schedule license scan agents (Nomos, Monk, deciders).
      3. Poll until jobs finish or fail.
      4. Retrieve license findings.
      5. Evaluate against compliance policy.
      6. Output report summary and return exit code (0 for PASS, 1 for FAIL).

    Returns:
        0 for PASS
        1 for FAIL
        2 for API/Network/Timeout/File errors
    """
    if not os.path.exists(archive_path):
        print(f"ERROR: Archive file not found: {archive_path}", file=sys.stderr)
        return 2

    try:
        policy = load_policy(policy_path)
    except (FileNotFoundError, ValueError) as err:
        print(f"ERROR: Failed to load policy: {err}", file=sys.stderr)
        return 2

    try:
        client = FossologyClient(server_url=server_url, token=token)

        # Step 1: Upload archive
        print(f"[1/4] Uploading archive '{archive_path}' to folder {folder_id}...")
        upload_id = client.upload_archive(
            archive_path=archive_path,
            folder_id=folder_id,
            upload_description="Automated CI Compliance Scan",
        )
        print(f"      Upload successful. Upload ID: {upload_id}")

        # Step 2: Schedule scan
        print(f"[2/4] Scheduling scan agents (Nomos, Monk) for upload {upload_id}...")
        job_id = client.schedule_scan(upload_id=upload_id, folder_id=folder_id)
        print(f"      Scan scheduled. Job ID: {job_id}")

        # Step 3: Poll jobs until complete
        print(f"[3/4] Monitoring scan progress...")
        poll_jobs(
            client=client,
            upload_id=upload_id,
            poll_interval=poll_interval,
            timeout=timeout,
            sleep_fn=sleep_fn,
        )

        # Step 4: Retrieve findings and evaluate compliance
        print(f"[4/4] Retrieving license findings and evaluating compliance...")
        findings = client.get_license_findings(upload_id=upload_id)

        summary = process_report(findings, policy)
        print_summary(summary)

        return 0 if summary["status"] == "PASS" else 1

    except FossologyAuthError as err:
        print(f"ERROR: Authentication failure: {err}", file=sys.stderr)
        return 2
    except FossologyTimeoutError as err:
        print(f"ERROR: Timeout: {err}", file=sys.stderr)
        return 2
    except FossologyHttpError as err:
        print(f"ERROR: FOSSology HTTP error ({err.status_code}): {err}", file=sys.stderr)
        return 2
    except (FossologyResponseError, FossologyClientError, RuntimeError) as err:
        print(f"ERROR: Pipeline execution error: {err}", file=sys.stderr)
        return 2
    except Exception as err:
        print(f"ERROR: Unexpected error: {err}", file=sys.stderr)
        return 2


def main(argv: Optional[List[str]] = None) -> int:
    """
    CLI interface for running the end-to-end CI compliance check.
    """
    parser = argparse.ArgumentParser(
        description="FOSSology CI Compliance Pipeline: Upload, scan, and gate builds based on license compliance."
    )
    parser.add_argument(
        "--archive",
        "-a",
        required=True,
        help="Path to the source ZIP archive to upload and scan.",
    )
    parser.add_argument(
        "--policy",
        "-p",
        default=None,
        help="Path to the compliance policy JSON file.",
    )
    parser.add_argument(
        "--folder-id",
        "-f",
        type=int,
        default=1,
        help="FOSSology target folder ID (default: 1).",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=10,
        help="Seconds between job status polls (default: 10).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=900,
        help="Maximum seconds to wait for scan jobs to finish (default: 900 / 15 minutes).",
    )
    parser.add_argument(
        "--server-url",
        default=None,
        help="FOSSology server URL (defaults to FOSSOLOGY_URL env var).",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="FOSSology Bearer token (defaults to FOSSOLOGY_TOKEN env var).",
    )

    args = parser.parse_args(argv)

    return run_pipeline(
        archive_path=args.archive,
        policy_path=args.policy,
        folder_id=args.folder_id,
        poll_interval=args.poll_interval,
        timeout=args.timeout,
        server_url=args.server_url,
        token=args.token,
    )


if __name__ == "__main__":
    sys.exit(main())
