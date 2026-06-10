# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Downloads failing build logs related to a CQ run.

Usage examples:
    ./py/bin/bot_tools/download_failing_build_logs \\
        --cl crrev.com/c/12345/1
    ./py/bin/bot_tools/download_failing_build_logs \\
        --cq-orchestrator-id 876543210
    ./py/bin/bot_tools/download_failing_build_logs \\
        --build-id 876543210 -d /tmp/logs

Logs are saved hierarchically under the target directory as:
    <target_dir>/<top_level_build_id>/<builder_name>/<step_name>.log

Directories are created as needed, so will generally only exist when there are
failing build logs.

The top_level_build_id varies based on the flag you pass:

- For a manually-specified build ID, the top_level_build_id is that ID.
- For child builders spawned by a CQ orchestrator, the <top_level_build_id> is
  the orchestrator's build ID.
- For a CQ run, one <top_level_build_id> subdir is created per builder spawned
  **directly** by the CQ+1. Generally, this means that there _will_ be one
  top-level cq-orchestrator, and there _may_ be others (e.g., bots run by
  zephyr's CI, since that doesn't interact with the cq-orchestrator, despite
  being spawned by CQ+1).
"""

import argparse
import concurrent.futures
import dataclasses
import itertools
import logging
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
from typing import Any, Iterable

from cros_utils import cros_paths
from cros_utils import gerrit_utils
from llvm_tools import cros_cls


@dataclasses.dataclass(frozen=True)
class BuilderWithFailingSteps:
    """Failing steps for a builder."""

    name: str
    build_id: int
    failing_steps: list[dict[str, Any]]
    top_level_build_id: int


@dataclasses.dataclass
class StepFileAssignments:
    """Manages unique filename assignments for steps to prevent collisions."""

    base_dir: Path
    assigned_paths: set[Path] = dataclasses.field(
        default_factory=set, init=False
    )

    def assign_log_file(
        self, top_level_build_id: int, builder_name: str, step_name: str
    ) -> Path:
        """Assigns a unique log filename for the step."""
        sanitized_step = sanitize_step_name(step_name)
        sanitized_builder = sanitize_step_name(builder_name)
        log_dir = self.base_dir / str(top_level_build_id) / sanitized_builder
        want_log_file = log_dir / f"{sanitized_step}.log"

        # Collision prevention. This is never expected, but good to
        # guard against anyway.
        if want_log_file in self.assigned_paths:
            for counter in itertools.count(1):
                want_log_file = log_dir / f"{sanitized_step}_{counter}.log"
                if want_log_file not in self.assigned_paths:
                    break

        logging.info(
            "Will save logs for step %r in builder %s to %s",
            step_name,
            builder_name,
            want_log_file,
        )
        self.assigned_paths.add(want_log_file)
        return want_log_file


def sanitize_step_name(name: str) -> str:
    """Replaces most non-alphanumeric characters with underscores.

    Step names come with separators like `|` in them, and often include other
    discouraged characters (spaces, asterisks, ...). Sanitizing helps avoid
    having to deal with that.
    """
    # Note on intent: the substitution here is mostly about removing characters
    # that would be surprising in a file path. e.g., `-` is unsurprising, and
    # preserving it allows us to exactly reproduce CQ builder names, so it's
    # preserved.
    return "".join(c if c.isalnum() or c == "-" else "_" for c in name)


def fetch_and_save_step_log(task: tuple[int, str, Path]) -> bool:
    """Downloads log for the step and writes to the target file path."""
    build_id, step_name, log_file = task
    cmd = ("bb", "log", str(build_id), step_name)
    logging.debug("Running command: %s", shlex.join(cmd))
    log_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(log_file, "w", encoding="utf-8") as f:
            subprocess.run(
                cmd,
                check=True,
                stdin=subprocess.DEVNULL,
                stdout=f,
                stderr=subprocess.PIPE,
                encoding="utf-8",
            )
    except subprocess.CalledProcessError as e:
        logging.error(
            "Failed to download log for builder %s, step %r: %r",
            build_id,
            step_name,
            e.stderr,
        )
        if log_file.exists():
            log_file.unlink()
        return False

    return True


def fetch_one_failing_builder_steps(
    builder: tuple[str, int, int],
) -> BuilderWithFailingSteps:
    """Fetches a single builder's failing steps."""
    name, build_id, top_level_build_id = builder
    try:
        steps = cros_cls.fetch_builder_steps(build_id)
    except Exception as e:
        e.add_note(f"Failed to fetch steps for builder {name}")
        raise

    failing_steps = []
    for step in steps:
        step_name = step.get("name", "")
        step_status_str = step.get("status", "")
        if not step_name or not step_status_str:
            raise ValueError(
                f"Malformed build {build_id}: step lacks name or status. "
                f"Name: {step_name!r}, Status: {step_status_str!r}"
            )

        status = cros_cls.BuilderStatus.parse(step_status_str)
        if status.is_failure:
            if status == cros_cls.BuilderStatus.CANCELED:
                logging.warning(
                    "Step %r in builder %s was canceled; skipping log download",
                    step_name,
                    name,
                )
            else:
                failing_steps.append(step)

    return BuilderWithFailingSteps(
        name, build_id, failing_steps, top_level_build_id
    )


def fetch_all_failing_builder_steps(
    executor: concurrent.futures.ThreadPoolExecutor,
    failing_builders: Iterable[tuple[str, int, int]],
) -> list[BuilderWithFailingSteps]:
    """Fetches steps for failing builders in parallel."""
    return list(executor.map(fetch_one_failing_builder_steps, failing_builders))


def fetch_failing_steps_from_orchestrator(
    executor: concurrent.futures.ThreadPoolExecutor,
    child_builders: dict[str, int],
    top_level_build_id: int,
) -> list[BuilderWithFailingSteps]:
    """Fetches failing steps for child builders of a CQ orchestrator."""
    board_outputs = cros_cls.CQBoardBuilderOutput.fetch_many(
        child_builders.values()
    )
    failing_builders = sorted(
        (name, build_id, top_level_build_id)
        for (name, build_id), output in zip(
            child_builders.items(), board_outputs
        )
        if output.status.is_failure
    )

    if not failing_builders:
        return []

    logging.info("Found %d failing child builders.", len(failing_builders))
    return fetch_all_failing_builder_steps(executor, failing_builders)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--cl",
        type=gerrit_utils.ChangeListURL.parse,
        help="""
        ChangeList URL to retrieve the newest cq-orchestrator run. Generally in
        the form crrev.com/c/1234/5.
        """,
    )
    group.add_argument(
        "--cq-orchestrator-id",
        type=cros_cls.BuildID,
        help="Specific Build ID of the cq-orchestrator run.",
    )
    group.add_argument(
        "--build-id",
        type=cros_cls.BuildID,
        help="Specific Build ID of a single child builder.",
    )
    parser.add_argument(
        "--directory",
        "-d",
        type=Path,
        help=f"""
        Output directory to store logs. Defaults to
        {tempfile.gettempdir()}/failing_build_logs/${{builder_or_orchestrator_id}}.
        """,
    )
    parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Remove and recreate output directory if it exists.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose debug logging.",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=8,  # Arbitrary number meant to help avoid rate-limiting.
        help="Maximum number of workers for parallel execution.",
    )
    return parser.parse_args(argv)


def find_failing_builders_and_steps(
    executor: concurrent.futures.ThreadPoolExecutor,
    orchestrator_id: cros_cls.BuildID | None,
    build_id: cros_cls.BuildID | None,
    search_siblings: bool,
) -> list[BuilderWithFailingSteps]:
    """Finds all failing builders and their failing steps."""
    if build_id is not None:
        logging.info("Fetching info for single builder: %s", build_id)
        b_name, _ = cros_cls.fetch_cq_orchestrator_or_board_builder(build_id)
        return fetch_all_failing_builder_steps(
            executor, [(b_name, build_id, build_id)]
        )

    if orchestrator_id is None:
        raise ValueError("Either orchestrator_id or build_id must be passed")

    logging.info(
        "Fetching CQ attempt info for orchestrator ID: %s",
        orchestrator_id,
    )

    cq_output = cros_cls.CQOrchestratorOutput.fetch(orchestrator_id)
    if not cq_output.child_builders:
        raise ValueError("No child builders found on the CQ orchestrator run.")

    attempt_key = cq_output.cq_attempt_key if search_siblings else None

    if not attempt_key:
        if search_siblings:
            logging.warning(
                "Could not find cq_attempt_key for %s; falling back to "
                "single orchestrator",
                orchestrator_id,
            )
        else:
            logging.info(
                "Sibling search disabled; focusing on single orchestrator: %s",
                orchestrator_id,
            )
        logging.info(
            "Found %d child builders. Fetching statuses...",
            len(cq_output.child_builders),
        )
        return fetch_failing_steps_from_orchestrator(
            executor,
            cq_output.child_builders,
            top_level_build_id=orchestrator_id,
        )

    logging.info("Found cq_attempt_key: %s", attempt_key)
    siblings = cros_cls.fetch_sibling_builds(attempt_key)
    logging.info("Found %d sibling builds in this attempt", len(siblings))

    builder_steps = fetch_failing_steps_from_orchestrator(
        executor,
        cq_output.child_builders,
        top_level_build_id=orchestrator_id,
    )

    child_build_ids = set(cq_output.child_builders.values())
    other_builds = (s for s in siblings if s.builder_name != "cq-orchestrator")

    failing_siblings = [
        (s.builder_name, s.build_id, s.build_id)
        for s in other_builds
        if s.status.is_failure and s.build_id not in child_build_ids
    ]
    if failing_siblings:
        logging.info(
            "Found %d failing sibling builds: %s",
            len(failing_siblings),
            [(name, bid) for name, bid, _ in failing_siblings],
        )
        builder_steps.extend(
            fetch_all_failing_builder_steps(executor, failing_siblings)
        )

    return builder_steps


def main(argv: list[str]) -> int:
    opts = parse_args(argv)
    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.DEBUG if opts.debug else logging.INFO,
    )

    cros_root = cros_paths.script_chromiumos_checkout_or_exit()

    orchestrator_id = None
    search_siblings = False
    if opts.cl:
        logging.info("Resolving CL URL: %s", opts.cl)
        # fetch_cq_orchestrator_ids requires a patch-set; assume the user meant
        # the most recent one if none is provided.
        if opts.cl.patch_set is None:
            inspect_result = cros_cls.gerrit_inspect(opts.cl, cros_root)
            current_ps = inspect_result.current_patch_set
            logging.info("Selected most recent patchset: %d", current_ps)
            opts.cl = dataclasses.replace(opts.cl, patch_set=current_ps)
        ids = cros_cls.fetch_cq_orchestrator_ids(opts.cl)
        if not ids:
            logging.error(
                "No completed cq-orchestrator runs found for CL %s", opts.cl
            )
            return 1
        orchestrator_id = ids[-1]
        logging.info("Using newest cq-orchestrator ID: %s", orchestrator_id)
        search_siblings = True
    elif opts.cq_orchestrator_id:
        orchestrator_id = opts.cq_orchestrator_id

    base_dir = opts.directory
    if not base_dir:
        base_dir = Path(tempfile.gettempdir()) / "failing_build_logs"

    logging.info("Target base directory: %s", base_dir)

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=opts.max_workers
    ) as executor:
        builder_steps = find_failing_builders_and_steps(
            executor,
            orchestrator_id=orchestrator_id,
            build_id=opts.build_id,
            search_siblings=search_siblings,
        )

        if not builder_steps:
            logging.info("No failing builders found.")
            return 0

        top_level_ids = {b.top_level_build_id for b in builder_steps}
        target_dirs = [base_dir / str(id) for id in top_level_ids]
        if not opts.force:
            existing_dirs = [d for d in target_dirs if d.exists()]
            if existing_dirs:
                logging.error(
                    "Target directories already exist: %s. Use -f/--force to "
                    "overwrite.",
                    [str(d) for d in existing_dirs],
                )
                return 1
        else:
            for d in target_dirs:
                logging.info("Removing existing directory at %s", d)
                try:
                    shutil.rmtree(d)
                except FileNotFoundError:
                    pass

        for d in target_dirs:
            d.mkdir(parents=True, exist_ok=True)

        tasks = []
        file_assignments = StepFileAssignments(base_dir)
        for builder in builder_steps:
            for step in builder.failing_steps:
                step_name = step["name"]
                log_file = file_assignments.assign_log_file(
                    builder.top_level_build_id, builder.name, step_name
                )
                tasks.append((builder.build_id, step_name, log_file))

        if not tasks:
            logging.info("No failing steps found in the failing builders.")
            return 0

        logging.info(
            "Downloading logs for %d failing steps across builders...",
            len(tasks),
        )

        results = executor.map(fetch_and_save_step_log, tasks)
        success_count = sum(results)

    logging.info(
        "Successfully downloaded %d/%d logs to %s.",
        success_count,
        len(tasks),
        base_dir,
    )
    if success_count < len(tasks):
        logging.error(
            "Some logs failed to download; please see above error logs."
        )
        return 1

    return 0
