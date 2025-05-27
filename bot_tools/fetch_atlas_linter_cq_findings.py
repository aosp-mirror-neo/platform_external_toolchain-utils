# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Fetches all atlas-linter-cq bot findings, putting them into CSVs."""

import argparse
import collections
import csv
import dataclasses
import datetime
import json
import logging
from pathlib import Path
import subprocess
from typing import Dict, List


UPLOAD_LINTER_FINDINGS_STEP_NAME = "upload linter findings"


def date_to_proto(date: datetime.date) -> str:
    """Converts the given date to a protojson-amenable timestamp."""
    # Proto accepts timestamps in RFC3339 format, per
    # https://protobuf.dev/reference/php/api-docs/Google/Protobuf/Timestamp.html
    dt = datetime.datetime.combine(date, datetime.time.min, tzinfo=datetime.UTC)
    raw_iso = dt.isoformat(timespec="seconds")
    # This ends with `+00:00`, but proto wants `Z`
    return raw_iso.split("+")[0] + "Z"


def enumerate_bots(
    start_at: datetime.date, stop_at: datetime.date
) -> List[int]:
    """Returns all successful bots created in the given timeframe.

    Args:
        start_at: include bots created on or after this date.
        stop_at: exclude bots created on or after this date.
    """
    predicate = json.dumps(
        {
            "builder": {
                "project": "chromeos",
                "bucket": "cq",
                "builder": "atlas-linters-cq",
            },
            "createTime": {
                "startTime": date_to_proto(start_at),
                "endTime": date_to_proto(stop_at),
            },
            "status": "SUCCESS",
        }
    )

    all_builds = subprocess.run(
        (
            "bb",
            "ls",
            "-id",
            "-predicate",
            predicate,
        ),
        check=True,
        encoding="utf-8",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
    ).stdout
    return [int(x) for x in all_builds.splitlines()]


@dataclasses.dataclass(frozen=True, eq=True)
class Finding:
    """A single finding by the linter bots.

    Contains some of the information from the Finding struct in
    https://github.com/luci/luci-go/blob/main/common/proto/findings/findings.proto

    Selection was done based mostly on "is it likely that we'll care to query
    based this information, or use it in end-to-end tests?"
    """

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
    findings: List[Finding]


def fetch_bot_findings(build_id: int) -> List[Finding]:
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


def group_findings_by_date(
    info: List[LinterBotInfo],
) -> Dict[datetime.date, List[Finding]]:
    """Groups the given linter bots' findings by the date the bot was run on."""
    grouped = collections.defaultdict(list)
    for bot_info in info:
        grouped[bot_info.create_time.date()].extend(bot_info.findings)
    return grouped


def asciify(s: str) -> str:
    """Replaces non-ascii sequences in `s` with an escape sequence.

    This isn't built to be fully, unambiguously round-trippable; it's just meant
    to roughly preserve information from commit messages when they're 99.9%
    ASCII.

    >>> asciify("\uffff")
    '\\uFFFF'
    """
    return s.encode("ascii", "backslashreplace").decode("ascii")


def write_findings(to_file: Path, findings: List[Finding]):
    """Writes `findings` to `to_file` as a CSV."""
    with to_file.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[x.name for x in dataclasses.fields(Finding)],
            dialect=csv.unix_dialect,
        )
        writer.writeheader()
        # Write the data rows
        for finding in findings:
            write_dict = dataclasses.asdict(finding)
            # non-ascii should be rare, but just to be sure that it's
            # interpreted properly, replace it with an ascii representation
            write_dict["message"] = asciify(write_dict["message"])
            writer.writerow(write_dict)


def write_grouped_findings(
    out_dir: Path, findings: Dict[datetime.date, List[Finding]]
):
    """Writes `findings` to dated subdirectories of `out_dir`.

    `findings` are individually written as CSVs. The output file paths are
    generally of the form `${out_dir}/YYYY/MM/DD/data.csv`.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    for date, findings_today in findings.items():
        if not findings_today:
            continue
        write_file = (
            out_dir
            / f"{date.year}"
            / f"{str(date.month).zfill(2)}"
            / f"{str(date.day).zfill(2)}"
            / "data.csv"
        )
        write_file.parent.mkdir(parents=True, exist_ok=False)
        write_findings(write_file, findings_today)


def parse_date(x: str) -> datetime.date:
    """Parses a date for argparse.

    Mostly exists so if the user passes an unparsable one, they see the type is
    `parse_date`, not a lambda of some sort.
    """
    return datetime.datetime.strptime(x, "%Y-%m-%d").date()


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--stop-at",
        required=True,
        type=parse_date,
        help="Grab bot creations before this date, in the format YYYY-MM-DD.",
    )
    parser.add_argument(
        "--start-at",
        required=True,
        type=parse_date,
        help="""
        Grab bot creations on or after this date, in the format YYYY-MM-DD.
        """,
    )
    parser.add_argument(
        "--output-dir", required=True, type=Path, help="Dir to write output to."
    )
    parser.add_argument(
        "--debug", action="store_true", help="Enable debug logging."
    )
    return parser.parse_args(argv)


def main(argv: List[str]) -> None:
    opts = parse_args(argv)
    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.DEBUG if opts.debug else logging.INFO,
    )

    bots_to_inspect = enumerate_bots(opts.start_at, opts.stop_at)
    if not bots_to_inspect:
        logging.info("No new bots found; quit")
        return

    logging.info("Found %d bots to inspect", len(bots_to_inspect))
    bot_infos = [fetch_bot_info(x) for x in bots_to_inspect]
    grouped_findings = group_findings_by_date(bot_infos)
    logging.info(
        "Found %d findings across %d days",
        sum(len(x) for x in grouped_findings.values()),
        len(grouped_findings),
    )
    write_grouped_findings(opts.output_dir, grouped_findings)
