# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Launches a CQ orchestrator job, waits for it, and verifies child builders.

This script checks if all direct child builders of a given CQ orchestrator run
have a step named 'update sdk'.
"""

import argparse
import logging
import sys

from llvm_tools import cros_cls


# The step name we look for.
TARGET_STEP_NAME = "update sdk"

# Builders to ignore in our checks, for one reason or another.
IGNORE_BUILDERS = (
    # This runs on CLs with Chromite changes (b/420954566#comment8). Whether or
    # not it updates the SDK is irrelevant.
    "chromite-cq",
)


def _inspect_and_verify_cq_orchestrator(
    build_id: int, min_expected_child_builders: int = 20
) -> bool:
    """Fetches and verifies child builders of a given cq-orchestrator build.

    Args:
        build_id: the Build ID to use
        min_expected_child_builders: The minimum number of child builders that
          have to exist, lest this error out.

    Returns:
        True if verification passed; False otherwise.
    """
    logging.info("Fetching CQ orchestrator output for build ID: %s", build_id)
    cq_orchestrator_output = cros_cls.CQOrchestratorOutput.fetch(build_id)

    child_builders = cq_orchestrator_output.child_builders
    # We normally have 80+ builders on these CQ runs; if there're fewer than 20,
    # something's seriously wrong.
    if len(child_builders) < min_expected_child_builders:
        logging.error(
            "Only %d child builders were found on CQ run; something's wrong",
            len(child_builders),
        )
        # Return early here rather than checking children, so the error isn't
        # obscured by more output.
        return False

    logging.info("Found %d child builders.", len(child_builders))

    builders_missing_step = []
    builders_with_step = 0
    logging.info('Checking child builders for "%s" step...', TARGET_STEP_NAME)
    for builder_name, child_build_id in child_builders.items():
        logging.debug("Checking child %d...", child_build_id)
        steps = cros_cls.fetch_builder_steps(child_build_id)
        had_step = any(x.get("name", "") == TARGET_STEP_NAME for x in steps)
        if had_step:
            builders_with_step += 1
            continue

        if builder_name in IGNORE_BUILDERS:
            logging.info(
                "Builder %s lacked step, but is marked as ignored", builder_name
            )
        else:
            builders_missing_step.append((builder_name, child_build_id))

    if not builders_missing_step:
        logging.info(
            "All %d relevant child builders had the '%s' step.",
            builders_with_step,
            TARGET_STEP_NAME,
        )
        return True

    builders_missing_step.sort()
    logging.error(
        "Child builders are unexpectedly missing '%s'", TARGET_STEP_NAME
    )
    logging.error("This may mean that our toolchain coverage has degraded.")
    logging.error("Listing of problematic builders:")
    for name, problematic_build_id in builders_missing_step:
        logging.error(
            "- %s at %s", name, cros_cls.builder_url(problematic_build_id)
        )
    logging.error(
        "A total of %d builders were missing the step",
        len(builders_missing_step),
    )
    return False


def _run_and_verify_cq(
    cl_urls: list[cros_cls.ChangeListURL], timeout_hours: int
) -> None:
    """Spawns a CQ orchestrator, waits, and then verifies its children."""
    logging.info(
        "Launching CQ orchestrator for CLs: %s",
        ", ".join(str(x) for x in cl_urls),
    )
    build_id = cros_cls.spawn_bot("chromeos/cq/cq-orchestrator", cls=cl_urls)
    final_status = cros_cls.wait_for_bot_to_finish(build_id, timeout_hours)
    logging.info("Build %s completed with status: %s", build_id, final_status)

    if final_status.is_failure:
        # The orchestrator could fail for one of many reasons, mostly failed
        # builds or hwtests (notably, both are after 'update sdk')
        logging.warning(
            "CQ orchestrator job %s failed; "
            "attempting to check child builders anyway...",
            cros_cls.builder_url(build_id),
        )

    _inspect_and_verify_cq_orchestrator(build_id)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Common debug flag, applied to the main parser.
    parser.add_argument(
        "--debug", action="store_true", help="Enable debug logging."
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run",
        help="Spawn a new cq-orchestrator job and inspect its child builders.",
    )
    run_parser.add_argument(
        "--cl",
        required=True,
        action="append",
        # Patch-set is necessary, since this is run by automation.
        type=cros_cls.ChangeListURL.parse_with_patch_set,
        help="""
        A CL (incl patch-set) to run the CQ orchestrator with (e.g.,
        crrev.com/c/12345/1). May be specified multiple times. At least one
        CL must be provided.
        """,
    )
    run_parser.add_argument(
        "--timeout-hours",
        type=int,
        # Default timeout for waiting for the CQ orchestrator job to complete.
        # Toolchain CQ runs can take quite a while.
        default=12,
        help="""
        Timeout in hours for waiting for the CQ orchestrator job. Default:
        %(default)s hours.
        """,
    )

    inspect_parser = subparsers.add_parser(
        "inspect",
        help="Inspect an existing cq-orchestrator job for child builder steps.",
    )
    inspect_parser.add_argument(
        "--cq-orchestrator",
        required=True,
        type=cros_cls.BuildID,
        help="Build ID of the completed cq-orchestrator to inspect.",
    )

    return parser.parse_args(argv)


def main(argv: list[str]) -> None:
    opts = _parse_args(argv)
    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.DEBUG if opts.debug else logging.INFO,
    )

    if opts.command == "run":
        _run_and_verify_cq(opts.cl, opts.timeout_hours)
        return

    assert opts.command == "inspect", f"Unknown command: {opts.command}"
    ok = _inspect_and_verify_cq_orchestrator(opts.cq_orchestrator)
    if not ok:
        # Errors should've already been logged, just exit.
        sys.exit(1)
