# FOSSology CI Compliance

CI pipeline integration for automated open-source license compliance using FOSSology and GitHub Actions.

## Overview

This project automates software license compliance checks by connecting FOSSology with a Python-based compliance checker and GitHub Actions.

The pipeline runs these stages:

1. Upload a source-code archive to a FOSSology server.
2. Wait for the upload's `ununpack` and `adj2nest` jobs to complete.
3. Schedule the Nomos and Monk license scanners and wait for the scheduled scan job.
4. Retrieve license findings using `GET /uploads/{id}/licenses?agent=nomos,monk`.
5. Evaluate detected licenses against a configurable compliance policy and print a summary.

The project also supports offline evaluation of existing FOSSology JSON and CSV reports.

## Features

- FOSSology REST API client for archive uploads, job scheduling, status checks, and license findings.
- Configurable license compliance policies.
- JSON and CSV report processing.
- Human conclusion precedence over automated scanner findings.
- Detection of prohibited, unapproved, and missing licenses.
- PASS/FAIL build status through exit codes.
- A pipeline summary with upload ID, archive name, scan status, findings, and detected allowed/disallowed licenses.
- GitHub Actions integration.
- Unit tests using Python's built-in `unittest` framework.

## Project Structure

```text
fossology-ci-compliance/
├── .github/
│   └── workflows/
│       └── compliance.yml
├── samples/
│   ├── sample_report_pass.json
│   ├── sample_report_fail.json
│   ├── sample_report_review.json
│   ├── sample_report_pass.csv
│   └── sample_report_fail.csv
├── tests/
│   ├── test_compliance_checker.py
│   ├── test_fossology_client.py
│   ├── test_run_ci_compliance.py
│   └── test_source_archive.py
├── compliance_checker.py
├── create_source_archive.py
├── compliance_policy.json
├── fossology_client.py
├── run_ci_compliance.py
└── README.md
```

## Requirements

- Python 3.8+
- Access to a FOSSology server for live scanning.
- A valid FOSSology API token for API integration.

The Python modules use the standard library and do not require additional package installation.

## Compliance Policy

The compliance policy is configured in `compliance_policy.json`.

Example:

```json
{
  "allowed_licenses": [
    "MIT",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "ISC",
    "Unlicense",
    "CC0-1.0"
  ],
  "denied_licenses": [
    "AGPL-3.0-only",
    "AGPL-3.0-or-later",
    "GPL-2.0-only",
    "GPL-2.0-or-later",
    "GPL-3.0-only",
    "GPL-3.0-or-later",
    "SSPL-1.0"
  ],
  "allow_unlicensed": false
}
```

### Evaluation Results

| Result | Meaning |
|---|---|
| PASS | All detected licenses are allowed. |
| DENIED | A prohibited license was detected. |
| NEEDS_REVIEW | An unapproved or missing license requires review. |

A denied or review-required finding causes the compliance check to fail.

## Running the Offline Compliance Checker

Evaluate a JSON report:

```bash
python3 compliance_checker.py --report samples/sample_report_pass.json
```

Evaluate a CSV report:

```bash
python3 compliance_checker.py --report samples/sample_report_pass.csv
```

Use a custom policy:

```bash
python3 compliance_checker.py \
  --report samples/sample_report_pass.json \
  --policy compliance_policy.json
```

### Exit Codes

| Exit Code | Meaning |
|---|---|
| 0 | Compliance check passed. |
| 1 | Compliance violations or review-required findings. |
| 2 | Execution or input error. |

## FOSSology API Integration

The `fossology_client.py` module provides functions for:

- Uploading source archives.
- Scheduling analysis and decider jobs.
- Checking job status.
- Retrieving license findings.

The `run_ci_compliance.py` script coordinates these operations.

Example:

```bash
python3 run_ci_compliance.py \
  --archive source_archive.zip \
  --policy compliance_policy.json
```

### Environment Variables

Configure the following environment variables before running the API integration:

```bash
export FOSSOLOGY_URL="https://your-fossology-server/repo"
export FOSSOLOGY_TOKEN="your-api-token"
```

`FOSSOLOGY_SERVER_URL` is also accepted as an alternate name for the server URL. The findings request supplies the required `agent` query parameter with `nomos,monk`, matching the scanners scheduled by this pipeline.

Never commit API tokens or other credentials to the repository.

## GitHub Actions Integration

The workflow is configured in:

`.github/workflows/compliance.yml`

It runs unit tests, creates a source archive, and invokes the FOSSology compliance pipeline.

The archive builder includes only the runtime Python modules. Test fixtures, reports, documentation, policy configuration, bytecode caches, and the archive itself are not scan inputs. The test suite still runs before archive creation. FOSSology findings retrieval excludes archive/container entries while scanning the files inside the archive.

Configure these GitHub repository secrets:

| Secret | Description |
|---|---|
| FOSSOLOGY_URL | FOSSology server URL |
| FOSSOLOGY_TOKEN | FOSSology API token |

The workflow runs on pushes and pull requests targeting the `main` branch.

## Running Unit Tests

Run the complete test suite (all FOSSology API calls are mocked):

```bash
python3 -m pytest -q
```

The tests cover:

- JSON and CSV report parsing.
- License extraction and policy evaluation.
- FOSSology API request handling.
- Upload and job scheduling behavior.
- CI orchestration and error handling.

API-related tests use mocks and do not upload archives or start scans.

## Current Implementation Status

The project includes mocked tests for upload, job polling, findings retrieval, compliance evaluation, and failure handling. These tests verify the request format, including the required findings `agent` parameter. They do not verify connectivity, permissions, or scan execution on a live FOSSology server. A live GitHub Actions run is required to verify those server-specific settings.

## Future Improvements

- Improve compliance reporting and error diagnostics.
- Add further integration tests against a controlled FOSSology instance.

## License

This repository does not currently contain a `LICENSE` file or declare a project license. No license has been inferred for these files.
