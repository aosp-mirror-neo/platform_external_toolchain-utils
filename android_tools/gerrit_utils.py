# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Utilities for interacting with Gerrit."""

import json
import logging
import subprocess
from typing import Any


INTERNAL_GERRIT_HOST = "https://googleplex-android-review.git.corp.google.com"


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
