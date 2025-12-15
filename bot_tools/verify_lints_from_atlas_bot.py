# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Verifies that a single atlas-linter-cq bot invocation runs lints properly."""

import argparse
import collections
import dataclasses
import logging
import re
import sys
from typing import Iterable

from bot_tools import bot_lints
from llvm_tools import cros_cls


# Series of CLs to apply for testing.
CLS_TO_APPLY = tuple(
    # N.B., require patch-sets here since automation runs this script; without a
    # patch-set, we might run a bot on a CL that hasn't been approved by someone
    # who can CQ+1.
    cros_cls.ChangeListURL.parse_with_patch_set(x)
    for x in (
        # Adds source in platform2/ that has known lints in it (reflected in
        # DEFAULT_FINDING_EXPECTATIONS below).
        "crrev.com/c/6547978/2",
        # Adds chromiumos-overlay logic to make the source buildable & lintable.
        "crrev.com/c/6548503/6",
    )
)


@dataclasses.dataclass(frozen=True)
class FindingExpectations:
    """Expectations about a Finding emitted by linters.

    The bot_lints.Finding type contains a fair amount of data that we don't
    really care about in this script. If that data changes, it's a maintenance
    burden to patch up.

    This captures the important bits.
    """

    category: str
    file_path: str
    message_re: re.Pattern

    def matches_finding(self, finding: bot_lints.Finding) -> bool:
        """Returns whether this expectation matches the given finding."""
        return (
            self.category == finding.category
            and self.file_path == finding.file_path
            and bool(self.message_re.search(finding.message))
        )


# TODO(b/422200984): are the differing paths here a problem?
DEFAULT_FINDING_EXPECTATIONS = (
    # The clippy lint is about a pointless borrow.
    FindingExpectations(
        category="chromeos_cargo_clippy",
        file_path="cros-toolchain/do-not-commit/lint-rs/src/main.rs",
        message_re=re.compile(r"clippy::needless_borrow"),
    ),
    # The clang-tidy lint is about a side-effect in an assert() statement being
    # questionable.
    FindingExpectations(
        category="chromeos_clang_tidy",
        file_path="cros-toolchain/do-not-commit/lint-cpp/lint.cpp",
        message_re=re.compile(r"side effect in assert"),
    ),
    # The staticcheck lint is a complaint about a trivially unused function.
    FindingExpectations(
        category="chromeos_static_check",
        file_path="src/platform2/cros-toolchain/do-not-commit/lint-go/main.go",
        message_re=re.compile(r"func unusedFunc is unused"),
    ),
)


def spawn_bot_and_collect_lints(
    cls_to_apply: Iterable[cros_cls.ChangeListURL],
) -> list[bot_lints.Finding]:
    build_id = cros_cls.spawn_bot(
        "chromeos/cq/atlas-linters-cq", cls=cls_to_apply
    )
    # At the time of writing, these take about 1 hour to complete successfully.
    # Break after 4 to have _some_ timeout, but not one that's remotely close
    # to their current execution time.
    final_status = cros_cls.wait_for_bot_to_finish(build_id, timeout_hours=4)
    if final_status.is_failure:
        raise ValueError(f"Bot failed with status {final_status}")
    return bot_lints.fetch_bot_info(build_id).findings


def log_errors_with_lints(
    lints: list[bot_lints.Finding],
    finding_expectations: Iterable[FindingExpectations] | None = None,
) -> bool:
    """Logs mismatches between the given lints and expectations.

    Returns:
        True if one or more errors are logged; False otherwise.
    """
    if finding_expectations is None:
        finding_expectations = DEFAULT_FINDING_EXPECTATIONS

    expectations_by_category = collections.defaultdict(list)
    for expectation in finding_expectations:
        expectations_by_category[expectation.category].append(expectation)

    lints_by_category = collections.defaultdict(list)
    for lint in lints:
        lints_by_category[lint.category].append(lint)

    logged_errors = False

    def log_error(*args, **kwargs):
        nonlocal logged_errors
        logged_errors = True
        logging.error(*args, **kwargs)

    for category, expectations in expectations_by_category.items():
        category_lints = lints_by_category.get(category)
        if category_lints is None:
            log_error("No lints found for category %s", category)
            continue

        # Mutability note: each `lint` that an expectation matches is removed
        # from `lints_by_category` so that checking whether any _extra_ lints
        # were produced is easier afterward.
        for expectation in expectations:
            match_index = next(
                (
                    i
                    for i, l in enumerate(category_lints)
                    if expectation.matches_finding(l)
                ),
                None,
            )
            if match_index is None:
                log_error(
                    "No lint found matching finding expectation %s", expectation
                )
            else:
                logging.debug(
                    "Expectation %s matched lint %s",
                    expectation,
                    category_lints[match_index],
                )
                del category_lints[match_index]

        if not category_lints:
            del lints_by_category[category]

    if lints_by_category:
        log_error("Unexpected lints found: %s", lints_by_category)

    return logged_errors


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--debug", action="store_true", help="Enable debug logging"
    )
    parser.add_argument(
        "--build-id",
        type=int,
        help="""
        Build ID to check. If not specified, this will spawn a bot, wait for
        it to complete, and check its output.
        """,
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> None:
    opts = parse_args(argv)
    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.DEBUG if opts.debug else logging.INFO,
    )

    build_id: int | None = opts.build_id
    if build_id:
        logging.info("Fetching lints from %s", cros_cls.builder_url(build_id))
        got_lints = bot_lints.fetch_bot_info(build_id).findings
    else:
        got_lints = spawn_bot_and_collect_lints(cls_to_apply=CLS_TO_APPLY)

    logging.info("Bot emitted lints: %s", got_lints)
    had_errors = log_errors_with_lints(got_lints)
    if had_errors:
        sys.exit("Lints did not match expectations; see above logs")
