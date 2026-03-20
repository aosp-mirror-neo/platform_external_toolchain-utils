# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Automated clean-up of package.provided files for Rust.

This script is intended to be run from a cronjob. It determines the
currently installed Rust version and removes obsolete entries for
dev-lang/rust and dev-lang/rust-host from package.provided files.
"""

import argparse
import logging
from pathlib import Path
import re
import subprocess
import textwrap

from cros_utils import cros_paths
from cros_utils import git_utils
from rust_tools import rust_uprev


# The bug to tag in all commit messages.
TRACKING_BUG = "b:434697334"

# e.g. dev-lang/rust-1.78.0 or dev-lang/rust-host-1.78.0, with a
# potential trailing comment.
_RUST_PKG_RE = re.compile(r"^(dev-lang/rust(?:-host)?-)(\d+\.\d+\.\d+)(?:$|\s)")


def fetch_chroot_rust_version() -> rust_uprev.RustVersion:
    """Gets the current rust version from within the chroot."""
    # We need to enter the chroot to run rustc.
    output = subprocess.run(
        ("cros_sdk", "--", "rustc", "--version"),
        check=True,
        encoding="utf-8",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
    ).stdout
    # Output is like:
    # rustc 1.84.1-nightly (e71f9a9a9 2025-01-27)
    #
    # Note that the chroot may output housekeeping bits like 'replacing
    # chroot', so search all lines independently.
    match = re.search(r"^rustc (\d+\.\d+\.\d+)-nightly", output, re.MULTILINE)
    if not match:
        raise ValueError(f"Could not parse rustc version from: {output}")
    version = rust_uprev.RustVersion.parse(match.group(1))
    logging.info("Detected Rust version: %s", version)
    return version


def update_package_provided_file(
    file_path: Path, current_version: rust_uprev.RustVersion, dry_run: bool
) -> bool:
    """Updates a single package.provided file."""
    logging.info("Processing %s", file_path)
    original_content = file_path.read_text(encoding="utf-8")

    lines = original_content.splitlines()
    new_lines = []
    changed = False

    for line in lines:
        match = _RUST_PKG_RE.match(line)
        if not match:
            new_lines.append(line)
            continue

        pkg_version = rust_uprev.RustVersion.parse(match.group(2))
        if pkg_version >= current_version:
            new_lines.append(line)
        else:
            logging.info("Removing obsolete line: %s", line)
            changed = True

    if not changed:
        return False

    if dry_run:
        logging.info("[dry-run] Would have updated %s", file_path)
        return True

    new_content = "\n".join(new_lines).rstrip() + "\n"
    file_path.write_text(new_content, encoding="utf-8")
    logging.info("Updated %s", file_path)
    return True


def main(argv: list[str]) -> None:
    """Main entry point."""
    cros_checkout = cros_paths.script_chromiumos_checkout_or_exit()

    logging.basicConfig(
        format=(
            ">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
            "%(message)s"
        ),
        level=logging.INFO,
    )

    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--chromiumos-overlay",
        type=Path,
        default=cros_checkout / cros_paths.CHROMIUMOS_OVERLAY,
        help="Path to the chromiumos-overlay.",
    )
    parser.add_argument(
        "action",
        choices=("dry-run", "commit", "upload"),
        help="""
        What to do. `dry-run` makes no changes, `commit` commits changes
        locally, and `upload` commits changes and uploads the result to Gerrit.
        """,
    )
    opts = parser.parse_args(argv)

    match opts.action:
        case "dry-run":
            dry_run = True
            upload = False
        case "commit":
            dry_run = False
            upload = False
        case "upload":
            dry_run = False
            upload = True
        case _:
            assert False, f"Unhandled case: {opts.action}"

    rust_version = fetch_chroot_rust_version()
    overlay_path = opts.chromiumos_overlay
    package_provided_path = (
        overlay_path / "profiles/targets/chromeos/package.provided"
    )

    any_file_changed = update_package_provided_file(
        package_provided_path, rust_version, dry_run=dry_run
    )

    if not any_file_changed:
        logging.info("No changes needed.")
        return

    if dry_run:
        logging.info("Dry-run enabled, quit; would commit changes otherwise.")
        return

    commit_message = textwrap.dedent(
        f"""\
        package.provided: remove old Rust versions

        Now that Rust {rust_version} has stuck, prior versions can be GC'ed.

        BUG={TRACKING_BUG}
        TEST=CQ
        """
    )

    logging.info("Committing changes.")
    git_utils.commit_all_changes(
        opts.chromiumos_overlay,
        message=commit_message,
    )

    if not upload:
        logging.info("Commit made; not uploading changes.")
        return

    logging.info("Uploading changes...")
    cl_ids = git_utils.upload_to_gerrit(
        git_repo=opts.chromiumos_overlay,
        remote=git_utils.CROS_EXTERNAL_REMOTE,
        branch=git_utils.CROS_MAIN_BRANCH,
    )
    for cl_id in cl_ids:
        git_utils.set_autoreview_topic_and_labels(
            cwd=opts.chromiumos_overlay,
            cl_id=cl_id,
        )
    logging.info("Changes uploaded successfully.")
