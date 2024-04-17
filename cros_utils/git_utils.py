# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Shared utilities for working with git."""

import contextlib
import logging
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
from typing import Generator, Iterable, List


# Email address used to tag the detective as a reviewer.
REVIEWER_DETECTIVE = "c-compiler-chrome@google.com"


def _parse_cls_from_upload_output(upload_output: str) -> List[int]:
    """Returns the CL number in the given upload output."""
    id_regex = re.compile(
        r"^remote:\s+https://"
        r"(?:chromium|chrome-internal)"
        r"-review\S+/\+/(\d+)\s",
        re.MULTILINE,
    )

    results = id_regex.findall(upload_output)
    if not results:
        raise ValueError(
            f"Wanted at least one match for {id_regex} in {upload_output!r}; "
            "found 0"
        )
    return [int(x) for x in results]


def upload_to_gerrit(
    git_repo: Path,
    remote: str,
    branch: str,
    reviewers: Iterable[str] = (),
    cc: Iterable[str] = (),
    ref: str = "HEAD",
) -> List[int]:
    """Uploads `ref` to gerrit, optionally adding reviewers/CCs."""
    # https://gerrit-review.googlesource.com/Documentation/user-upload.html#reviewers
    # for more info on the `%` params.
    option_list = [f"r={x}" for x in reviewers]
    option_list += (f"cc={x}" for x in cc)
    if option_list:
        trailing_options = "%" + ",".join(option_list)
    else:
        trailing_options = ""

    run_result = subprocess.run(
        [
            "git",
            "push",
            remote,
            # https://gerrit-review.googlesource.com/Documentation/user-upload.html#reviewers
            # for more info on the `%` params.
            f"{ref}:refs/for/{branch}{trailing_options}",
        ],
        cwd=git_repo,
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
    )

    logging.info(
        "`git push`ing %s to %s/%s had this output:\n%s",
        ref,
        remote,
        branch,
        run_result.stdout,
    )
    run_result.check_returncode()
    return _parse_cls_from_upload_output(run_result.stdout)


def try_set_autosubmit_labels(cwd: Path, cl_id: int) -> None:
    """Sets autosubmit on a CL. Logs - not raises - on failure.

    This sets a series of convenience labels on the given cl_number, so landing
    it (e.g., for the detective) is as easy as possible.

    Args:
        cwd: the directory that the `gerrit` tool should be run in. Anywhere in
            a ChromeOS tree will do. The `gerrit` command fails if it isn't run
            from within a ChromeOS tree.
        cl_id: The CL number to apply labels to.
    """
    gerrit_cl_id = str(cl_id)
    gerrit_commands = (
        ["gerrit", "label-as", gerrit_cl_id, "1"],
        ["gerrit", "label-cq", gerrit_cl_id, "1"],
        ["gerrit", "label-v", gerrit_cl_id, "1"],
    )
    for cmd in gerrit_commands:
        # Run the gerrit commands inside of toolchain_utils, since `gerrit`
        # needs to be run inside of a ChromeOS tree to work. While
        # `toolchain-utils` can be checked out on its own, that's not how this
        # script is expeted to be used.
        return_code = subprocess.run(
            cmd,
            cwd=cwd,
            check=False,
            stdin=subprocess.DEVNULL,
        ).returncode
        if return_code:
            logging.warning(
                "Failed to run gerrit command %s. Ignoring.",
                shlex.join(cmd),
            )


@contextlib.contextmanager
def create_worktree(git_directory: Path) -> Generator[Path, None, None]:
    """Creates a temp worktree of `git_directory`, yielding the result."""
    with tempfile.TemporaryDirectory(prefix="update_kernel_afdo_") as t:
        tempdir = Path(t)
        logging.info(
            "Establishing worktree of %s in %s", git_directory, tempdir
        )
        subprocess.run(
            [
                "git",
                "worktree",
                "add",
                "--detach",
                "--force",
                tempdir,
            ],
            cwd=git_directory,
            check=True,
            stdin=subprocess.DEVNULL,
        )

        try:
            yield tempdir
        finally:
            # Explicitly `git worktree remove` here, so the parent worktree's
            # metadata is cleaned up promptly.
            subprocess.run(
                [
                    "git",
                    "worktree",
                    "remove",
                    "--force",
                    tempdir,
                ],
                cwd=git_directory,
                check=False,
                stdin=subprocess.DEVNULL,
            )
