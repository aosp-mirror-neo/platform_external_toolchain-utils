# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Utilities for interacting with gs://."""

import dataclasses
import datetime
import logging
import re
import shlex
import subprocess
from typing import List, Optional


# Determine which gsutil to use.
# 'gsutil.py' is provided by depot_tools, whereas 'gsutil'
# is provided by either https://cloud.google.com/sdk/docs/install, or
# the 'google-cloud-cli' package. Since we need depot_tools to even
# use 'repo', 'gsutil.py' is guaranteed to exist.
GSUTIL = "gsutil.py"


@dataclasses.dataclass(frozen=True)
class GsEntry:
    """An entry of `gsutil ls -l` output."""

    # When this was last modified (or created). `None` if the entry is a
    # directory.
    last_modified: Optional[datetime.datetime]
    # The full gs:// path to the artifact.
    gs_path: str


def _datetime_from_gs_time(timestamp_str: str) -> datetime.datetime:
    """Parses a datetime from gs."""
    return datetime.datetime.strptime(
        timestamp_str, "%Y-%m-%dT%H:%M:%SZ"
    ).replace(tzinfo=datetime.timezone.utc)


def _parse_ls_output(stdout: str) -> List[GsEntry]:
    """Parses output of `gsutil ls`."""
    stdout_lines = stdout.splitlines()
    # Ignore the last line, since that's always "TOTAL:"
    stdout_lines.pop()

    line_re = re.compile(
        # Entries can take one of two forms:
        r"(?:"
        # 1. The entry has a size, mod date, and name
        r"\d+\s+(\S+T\S+)\s+(gs://.+)"
        r"|"
        # 2. The entry has none of those, and is just a gs URL.
        r"(gs://.+)"
        r")"
    )
    results = []

    for line in stdout_lines:
        # If the line starts with gs://, it's a header for a directory's
        # contents. Skip it.
        if line.startswith("gs://"):
            continue

        line = line.strip()
        if not line:
            continue
        m = line_re.fullmatch(line)
        if m is None:
            raise ValueError(f"Unexpected line from gs: {line!r}")
        timestamp_str, gs_url, alt_gs_url = m.groups()
        if timestamp_str:
            last_modified = _datetime_from_gs_time(timestamp_str)
            gs_path = gs_url
        else:
            last_modified = None
            gs_path = alt_gs_url
        results.append(GsEntry(last_modified=last_modified, gs_path=gs_path))
    return results


def ls(gs_url: str) -> List[GsEntry]:
    """Runs `gsutil ls` on the given `path`.

    Globs are forwarded to gs://

    Returns:
        A list of GsEntrys matching `path`. If the list is entry, no paths
        matched the URL.
    """
    cmd = [
        GSUTIL,
        "ls",
        "-l",
        gs_url,
    ]
    result = subprocess.run(
        cmd,
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
    )

    if result.returncode:
        # If nothing could be found, gsutil will exit after printing this.
        if "One or more URLs matched no objects." in result.stderr:
            return []
        logging.error("%s failed; stderr:\n%s", shlex.join(cmd), result.stderr)
        result.check_returncode()
        assert False, "unreachable"
    return _parse_ls_output(result.stdout)
