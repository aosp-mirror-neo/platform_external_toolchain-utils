# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Given the `bb` id for a cq-orchestrator, fetches all test logs on gs://.

Note that "all" can be _a lot_; some runs have dozens of GB of logs.
"""

import argparse
import json
import logging
from pathlib import Path
import re
import shlex
import subprocess
from typing import Any, Dict, Iterable, List


def get_bb_output(subcmd: List[str]):
    return subprocess.run(
        ["bb"] + subcmd,
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        encoding="utf-8",
    ).stdout


def get_bb_json_output(subcmd: str, args: Iterable[str]):
    cmd = [subcmd, "-json"]
    cmd += args
    output = get_bb_output(cmd)
    return json.loads(output)


def build_id_to_link(build_id: int) -> str:
    return f"https://ci.chromium.org/b/{build_id}"


def find_cros_test_platform_child_of_cq_orchestrator(
    cq_orchestrator_id: int,
) -> int:
    """Looks for the cros_test_platform invocation run by the cq-orch."""
    # At the time of writing, this shows up like in a link inside of a 'check
    # test results' log.

    output = get_bb_json_output("get", ("-steps", str(cq_orchestrator_id)))
    summary_markdown_re = re.compile(
        re.escape("https://cr-buildbucket.appspot.com/build/") + r"(\d+)"
    )
    # If this JSON isn't perfectly formed, the below may `raise`. Given the
    # simplicity of this script, that's fine.
    for step in output["steps"]:
        if step.get("name") != "check test results":
            continue

        summary = step.get("summaryMarkdown", "")
        match = summary_markdown_re.search(summary)
        if not match:
            raise ValueError(
                "cq-orchestrator's summary had no cros_test_platform link"
            )
        return int(match.group(1))

    raise ValueError(
        f"No `check test results` step found in "
        f"{build_id_to_link(cq_orchestrator_id)}"
    )


def find_gs_links_in_test_log(log: Dict[str, Any]) -> List[str]:
    empty_dict = {}
    results = []
    for result_sets in log.values():
        for result_set in result_sets:
            gs_url = (
                result_set.get("Results", empty_dict)
                .get("log_data", empty_dict)
                .get("gs_url")
            )
            if gs_url:
                results.append(gs_url)
    if not results:
        raise ValueError(
            "No gs_urls found in test results; detection is probably broken"
        )
    return results


def find_all_gs_log_test_links(cq_orchestrator_id: int) -> List[str]:
    cros_test_platform_id = find_cros_test_platform_child_of_cq_orchestrator(
        cq_orchestrator_id
    )
    log_output = get_bb_output(
        [
            "log",
            str(cros_test_platform_id),
            "ctpv2 sub-build (async)|Summarize",
            "all test results",
        ]
    )
    parsed_log_output = json.loads(log_output)
    return find_gs_links_in_test_log(parsed_log_output)


def download_gs_logs_to(
    output_dir: Path, gs_logs: List[str], dry_run: bool = False
):
    gsutil_command = ["gsutil", "-m", "cp", "-r"] + gs_logs + [str(output_dir)]
    logging.info("Running `%s`...", shlex.join(gsutil_command))
    if dry_run:
        logging.info("--dry-run passed; running skipped")
    else:
        subprocess.run(gsutil_command, check=True)


def main(argv: List[str]) -> None:
    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.INFO,
    )

    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-c",
        "--cq-orchestrator-id",
        type=int,
        required=True,
        help="cq-orchestrator builder ID",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        required=True,
        help="directory to write results into",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="""
        Don't actually do the download; just print the command
        that would have been run.
        """,
    )
    opts = parser.parse_args(argv)

    cq_orchestrator_id: int = opts.cq_orchestrator_id
    dry_run: bool = opts.dry_run
    output_dir: Path = opts.output_dir

    if not dry_run and output_dir.exists():
        parser.error("--output-dir exists; refusing to overwrite")

    logging.info("Finding all relevant test log locations...")
    all_log_locations = find_all_gs_log_test_links(cq_orchestrator_id)

    logging.info(
        "Found %d test logs on gs://; downloading...", len(all_log_locations)
    )
    if not dry_run:
        output_dir.mkdir(parents=True)
    download_gs_logs_to(output_dir, all_log_locations, dry_run)
