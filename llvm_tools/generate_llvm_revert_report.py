# Copyright 2023 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Reports on all reverts applied and not applied to sys-devel/llvm.

Note that this is primarily intended to produce output that can be easily
pasted into a spreadsheet (read: the ChromeOS Mage's test matrix), so output is
in CSV format.
"""

import argparse
import csv
import dataclasses
import json
import logging
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any, TextIO

from cros_utils import cros_paths


GERRIT = "gerrit"


@dataclasses.dataclass(frozen=True)
class Revert:
    """Represents a commit that reverts an LLVM change."""

    url: str
    subject: str
    status: str

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Revert":
        """Parses a Revert from a dict."""
        try:
            return Revert(
                url=data["url"],
                subject=data["subject"],
                status=data["status"],
            )
        except:
            logging.error("Could not parse Revert from %r", data)
            raise


def find_reverts_in_gerrit(branch: str, chromeos_root: Path) -> str:
    """Queries Gerrit for reverts on the given branch."""
    cmd = (
        GERRIT,
        "--format",
        "json",
        "search",
        "--branch",
        branch,
        "--topic",
        "revert-checker",
        "project:external/github.com/llvm/llvm-project",
    )
    logging.info("Running: %s", shlex.join(cmd))
    process = subprocess.run(
        cmd,
        cwd=chromeos_root,
        check=True,
        encoding="utf-8",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
    )
    return process.stdout


def find_reverts(branch: str, chromeos_root: Path) -> list[Revert]:
    """Queries Gerrit for reverts on the given branch."""
    reverts = find_reverts_in_gerrit(branch, chromeos_root)
    return [Revert.from_dict(d) for d in json.loads(reverts)]


def write_reverts_as_csv(write_to: TextIO, reverts: list[Revert]) -> None:
    writer = csv.writer(write_to, quoting=csv.QUOTE_ALL)
    # Write the header.
    writer.writerow(("Status", "URI", "Subject", "Notes"))
    writer.writerows((x.status, x.url, x.subject) for x in reverts)


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--branch",
        help="Branch to examine. For example: chromeos/llvm-r574158-1",
        required=True,
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )

    opts = parser.parse_args(argv)
    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.DEBUG if opts.debug else logging.INFO,
    )

    chromeos_root = cros_paths.script_chromiumos_checkout_or_exit()

    reverts = sorted(
        find_reverts(opts.branch, chromeos_root),
        key=lambda r: r.status,
        reverse=True,
    )

    print("\nCSV summary of reverts:")
    write_reverts_as_csv(sys.stdout, reverts)
