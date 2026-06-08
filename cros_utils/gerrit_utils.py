# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Utilities for interacting with Gerrit."""

import dataclasses
import enum
import json
import logging
import subprocess
from typing import Any


ANDROID_INTERNAL_GERRIT_HOST = (
    "https://googleplex-android-review.git.corp.google.com"
)


class CLStatus(enum.Enum):
    """The status of a CL."""

    NEW = "NEW"
    MERGED = "MERGED"
    ABANDONED = "ABANDONED"

    def is_open(self) -> bool:
        """Returns whether the CL is open."""
        return self == self.NEW

    @classmethod
    def parse(cls, status: str) -> "CLStatus":
        """Parses a CL status from a string."""
        for member in cls:
            if member.value == status:
                return member
        raise ValueError(f"Unknown CL status: {status!r}")


@dataclasses.dataclass(frozen=True)
class RelatedChangeInfo:
    """Details of a related change from Gerrit."""

    project: str
    cl_number: int
    status: CLStatus


def fetch_gob_curl_body_with_retries(url: str) -> str:
    """Runs gob-curl, returning its output as a `str`.

    Retries to work around Gerrit flakes, if any.
    """
    max_tries = 5
    i = 1
    while True:
        result = subprocess.run(
            (
                "gob-curl",
                # Follow redirects.
                "--location",
                # Exit with nonzero code if the response code indicates failure.
                "--fail",
                url,
            ),
            check=False,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if not result.returncode:
            return result.stdout

        logging.warning(
            "Failed attempt %d/%d running gob-curl on %s; "
            "stdout:\n%s\n\nstderr:\n%s",
            i,
            max_tries,
            url,
            result.stdout,
            result.stderr,
        )
        # Reraise if we're at the limit, but make sure to log stdout/stderr
        # above so they're not dropped.
        if i == max_tries:
            result.check_returncode()
        i += 1


def parse_gerrit_response(response_body: str) -> Any:
    """Parses a JSON response from Gerrit, stripping the prefix."""
    json_response_pre = ")]}'"
    if not response_body.startswith(json_response_pre):
        raise ValueError(
            f"Unexpected non-JSON Gerrit response: {response_body!r}"
        )
    return json.loads(response_body[len(json_response_pre) :])


def fetch_related_changes(
    gerrit_host: str, change_id: int
) -> list[RelatedChangeInfo]:
    """Fetches related changes for a given change."""
    logging.info("Fetching related changes for %d", change_id)
    url = f"{gerrit_host}/changes/{change_id}/revisions/current/related"
    response_body = fetch_gob_curl_body_with_retries(url)
    related = parse_gerrit_response(response_body)

    changes = related.get("changes")
    if not changes:
        return []

    results = []
    for change in changes:
        project = change.get("project")
        cl_number = change.get("_change_number")
        status_str = change.get("status")
        if not all((project, cl_number, status_str)):
            logging.warning("Related change %s is missing fields", change)
            continue

        status = CLStatus.parse(status_str)
        results.append(
            RelatedChangeInfo(
                project=project, cl_number=cl_number, status=status
            )
        )
    return results
