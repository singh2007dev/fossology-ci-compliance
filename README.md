# FOSSology CI Compliance

CI pipeline integration for automated open-source license compliance using FOSSology and GitHub Actions.

## Overview

This project automates software license compliance checks by connecting FOSSology with a Python-based compliance checker and GitHub Actions.

The pipeline is designed to:

1. Upload a source-code archive to a FOSSology server.
2. Schedule license analysis using FOSSology scanner agents.
3. Retrieve license findings from FOSSology.
4. Evaluate detected licenses against a configurable compliance policy.
5. Generate a compliance summary and return an appropriate CI exit code.

The project also supports offline evaluation of existing FOSSology JSON and CSV reports.

## Features

- FOSSology REST API client for archive uploads, job scheduling, status checks, and license findings.
- Configurable license compliance policies.
- JSON and CSV report processing.
- Human conclusion precedence over automated scanner findings.
- Detection of prohibited, unapproved, and missing licenses.
- PASS/FAIL build status through exit codes.
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
│   └── test_run_ci_compliance.py
├── compliance_checker.py
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

Never commit API tokens or other credentials to the repository.

## GitHub Actions Integration

The workflow is configured in:

`.github/workflows/compliance.yml`

It runs unit tests, creates a source archive, and invokes the FOSSology compliance pipeline.

Configure these GitHub repository secrets:

| Secret | Description |
|---|---|
| FOSSOLOGY_URL | FOSSology server URL |
| FOSSOLOGY_TOKEN | FOSSology API token |

The workflow runs on pushes and pull requests targeting the `main` branch.

## Running Unit Tests

Run the complete test suite:

```bash
python3 -m unittest discover -s tests -v
```

The project currently has 74 unit tests covering:

- JSON and CSV report parsing.
- License extraction and policy evaluation.
- FOSSology API request handling.
- Upload and job scheduling behavior.
- CI orchestration and error handling.

API-related tests use mocks; they do not represent 74 live server tests.

## Current Implementation Status

The following functionality has been verified:

- Offline compliance checking.
- Unit tests passing.
- Archive upload to the hosted FOSSology server.
- Manual Nomos scan execution.
- Retrieval and display of license findings.

**Known limitation:** The automated API scheduling request from GitHub Actions currently returns HTTP 404, reporting that the upload is not associated with the specified folder or that the account lacks write permission. Manual Nomos scheduling through the FOSSology web interface has succeeded.

Therefore, the complete automated end-to-end GitHub Actions scan is not yet verified.

## Future Improvements

- Resolve automated API job scheduling and permission handling.
- Verify complete automated scanning through GitHub Actions.
- Improve compliance reporting and error diagnostics.
- Add further integration tests against a controlled FOSSology instance.

## License

See the repository's license file for usage and distribution terms.