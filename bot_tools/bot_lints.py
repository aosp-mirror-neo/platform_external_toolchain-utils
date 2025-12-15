# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Common code for collecting linter bot records."""

import dataclasses
import datetime
import json
import logging
import subprocess


UPLOAD_LINTER_FINDINGS_STEP_NAME = "upload linter findings"


@dataclasses.dataclass(frozen=True, eq=True)
class Finding:
    """A single finding by the linter bots."""

    category: str
    file_path: str
    gerrit_host: str
    gerrit_change_number: int
    gerrit_patchset: int
    message: str
    severity_level: str


@dataclasses.dataclass(frozen=True, eq=True)
class LinterBotInfo:
    """Info about a single linter bot invocation."""

    create_time: datetime.datetime
    findings: list[Finding]


def fetch_bot_findings(build_id: int) -> list[Finding]:
    """Fetches the findings associated with the given build ID.

    It's up to the caller to verify that the build ID _has_ findings; if not,
    the `bb` command this invokes will fail, causing this function to `raise`.
    """
    findings = json.loads(
        subprocess.run(
            (
                "bb",
                "log",
                str(build_id),
                UPLOAD_LINTER_FINDINGS_STEP_NAME,
                "findings.json",
            ),
            check=True,
            encoding="utf-8",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
        ).stdout
    )["findings"]

    results = []
    for finding in findings:
        location = finding["location"]
        gerrit_change_ref = location["gerrit_change_ref"]
        results.append(
            Finding(
                category=finding["category"],
                file_path=location["file_path"],
                gerrit_host=gerrit_change_ref["host"],
                gerrit_change_number=int(gerrit_change_ref["change"]),
                gerrit_patchset=int(gerrit_change_ref["patchset"]),
                message=finding["message"],
                severity_level=finding["severity_level"],
            )
        )
    return results


def fetch_bot_info(build_id: int) -> LinterBotInfo:
    """Fetches the LinterBotInfo for a single linter bot invocation."""
    logging.info("Fetching info for %d", build_id)
    build_results = json.loads(
        subprocess.run(
            (
                "bb",
                "get",
                "-steps",
                "-json",
                str(build_id),
            ),
            check=True,
            encoding="utf-8",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
        ).stdout
    )

    create_time = datetime.datetime.fromisoformat(build_results["createTime"])
    has_findings = any(
        x["name"] == UPLOAD_LINTER_FINDINGS_STEP_NAME
        for x in build_results["steps"]
    )
    findings = fetch_bot_findings(build_id) if has_findings else []
    logging.debug("Bot %d had %d findings", build_id, len(findings))
    return LinterBotInfo(
        create_time=create_time,
        findings=findings,
    )
