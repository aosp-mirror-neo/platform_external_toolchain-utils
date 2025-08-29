# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Wraps a command to run it at most once per week successfully.

This script is intended to be run daily (e.g., via a cron job).
It checks a state file to determine if the wrapped command has already
succeeded within the current week. If so, it exits successfully. Otherwise, it
runs the command.

If the command fails more times than the `--max-flakes` flag specifies, it
exits unsuccessfully. Otherwise, it assumes the failure is a flake, logs a
warning, and exits successfully. As mentioned above, subsequent invocations will
re-execute the command.

For the purposes of this script, week boundaries are defined by the ISO
calendar, and 'now' is determined by the current UNIX timestamp. Moreover, weeks
begin every Monday at 00:00UTC.
"""

import argparse
import dataclasses
import datetime
import json
import logging
from pathlib import Path
import shlex
import subprocess
import time
from typing import Any, Dict, Optional


@dataclasses.dataclass(frozen=True)
class WeeklyJobState:
    """State for the weekly job runner."""

    # Timestamp per `time.time()`
    last_success_timestamp: Optional[float] = None
    consecutive_failures: int = 0

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> "WeeklyJobState":
        return cls(
            last_success_timestamp=data.get("last_success_timestamp"),
            consecutive_failures=data.get("consecutive_failures", 0),
        )

    def to_json(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


def load_state(state_file_path: Path) -> WeeklyJobState:
    """Loads the state from the JSON file."""
    try:
        with open(state_file_path, "r", encoding="utf-8") as f:
            return WeeklyJobState.from_json(json.load(f))
    except FileNotFoundError:
        logging.info(
            "State file %s not found, using default state.", state_file_path
        )
        return WeeklyJobState()


def save_state(state_file_path: Path, state: WeeklyJobState) -> None:
    """Saves the state to the JSON file."""
    with state_file_path.open("w", encoding="utf-8") as f:
        json.dump(state.to_json(), f)


def get_iso_week_info(timestamp: float) -> tuple[int, int]:
    """Converts a Unix timestamp to (ISO year, ISO week number)."""
    # `isocalendar` note: weeks are uniquely identified by (iso_year, iso_week),
    # and all weeks are 7 days. The year that an ISO week belongs to is the
    # Gregorian year of the Thursday of that week.
    #
    # Hence, there's no need to be concerned about cases like "1-Jan falls on
    # Wednesday, so we see 31-Dec-24 as (iso_year=2024, iso_week=52) and
    # 1-Jan-25 as (iso_year=2025, iso_week=1)." 30-Dec-25 through 5-Jan-25 is
    # (iso_year=2025, iso_week=1).
    date_obj = datetime.date.fromtimestamp(timestamp)
    iso_year, iso_week, _ = date_obj.isocalendar()
    return iso_year, iso_week


def run_wrapped_command(command_to_run: list[str]) -> int:
    """Runs the wrapped command and returns its exit code."""
    logging.info("Running command: %s", shlex.join(command_to_run))
    # Do not capture stdout/stderr unless necessary for debugging,
    # let the wrapped command print directly.
    process = subprocess.run(
        command_to_run,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    return process.returncode


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parses command line arguments."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--state-file",
        required=True,
        type=Path,
        help="Path to the JSON file for storing run state.",
    )
    parser.add_argument(
        "--max-flakes",
        required=True,
        type=int,
        help="Max number of consecutive failures before reporting an error.",
    )
    parser.add_argument(
        "--debug", action="store_true", help="Enable debug logging."
    )
    parser.add_argument(
        "command_to_run",
        nargs=argparse.REMAINDER,
        help="The command to run, optionally preceded by '--'.",
    )

    opts = parser.parse_args(argv)

    # Some commandline utils use `--` to separate remainder args from flags.
    # e.g., `./run_weekly_job.py "${flags[@]}" -- ./foo`
    # In this case, Python will have `command_to_run == ["--", "./foo"]`;
    # massage to `["./foo"]`
    if opts.command_to_run and opts.command_to_run[0] == "--":
        opts.command_to_run = opts.command_to_run[1:]

    if not opts.command_to_run:
        parser.error("No wrapped command provided.")
    return opts


def main(argv: list[str]) -> int:
    """Main entry point for the script."""
    opts = parse_args(argv)
    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.DEBUG if opts.debug else logging.INFO,
    )

    command_to_run: list[str] = opts.command_to_run
    state_file: Path = opts.state_file
    state = load_state(state_file)

    now = time.time()
    if state.last_success_timestamp:
        current_week = get_iso_week_info(now)
        last_success_week = get_iso_week_info(state.last_success_timestamp)
        if current_week == last_success_week:
            logging.info("Had success earlier this week; not running command")
            return 0

        if state.consecutive_failures:
            logging.info(
                "Had %d consecutive failures before; trying again",
                state.consecutive_failures,
            )
        else:
            logging.info("Last success was one or more weeks ago; trying again")

    command_success = run_wrapped_command(command_to_run) == 0

    if command_success:
        logging.info("Command succeeded.")
        new_state = WeeklyJobState(
            last_success_timestamp=now,
            consecutive_failures=0,
        )
        exit_code = 0
    else:
        new_state = WeeklyJobState(
            last_success_timestamp=state.last_success_timestamp,
            consecutive_failures=state.consecutive_failures + 1,
        )
        if new_state.consecutive_failures >= opts.max_flakes:
            logging.warning(
                "Command failed, total failures = %d. Bubbling error up.",
                new_state.consecutive_failures,
            )
            exit_code = 1
        else:
            logging.warning(
                "Command failed, total failures = %d, which is below the "
                "failure threshold. Exiting cleanly.",
                new_state.consecutive_failures,
            )
            exit_code = 0

    save_state(state_file, new_state)
    return exit_code
