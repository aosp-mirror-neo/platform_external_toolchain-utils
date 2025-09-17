# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Shared utilities for working with git."""

import contextlib
import dataclasses
import enum
import logging
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Generator, Iterable, Sequence


# Email address used to tag the detective/mage as a reviewer.
REVIEWER_DETECTIVE = "c-compiler-chrome@google.com"
REVIEWER_MAGE = "chromeos-toolchain-mage@google.com"

# Default git naming conventions throughout ChromeOS.
CROS_EXTERNAL_REMOTE = "cros"
CROS_INTERNAL_REMOTE = "cros-internal"
CROS_MAIN_BRANCH = "main"

# Gerrit labels
GERRIT_LABEL_AUTOSUBMIT = "label-as"
GERRIT_LABEL_CQ = "label-cq"
GERRIT_LABEL_VERIFIED = "label-v"


class Channel(enum.Enum):
    """An enum that represents ChromeOS channels."""

    # Ordered from closest-to-ToT to farthest-from-ToT
    CANARY = "canary"
    BETA = "beta"
    STABLE = "stable"

    @classmethod
    def parse(cls, val: str) -> "Channel":
        for x in cls:
            if val == x.value:
                return x
        raise ValueError(
            f"No such channel: {val!r}; try one of {[x.value for x in cls]}"
        )


@dataclasses.dataclass(frozen=True, eq=True, order=True)
class ChannelBranch:
    """Represents a ChromeOS branch."""

    # Name of the remote that has the branch.
    remote: str
    # The ChromeOS release number associated with the branch (e.g., 127 for
    # M127).
    release_number: int
    # The name of the branch.
    branch_name: str


def autodetect_cros_channels(git_repo: Path) -> dict[Channel, ChannelBranch]:
    """Autodetects the current ChromeOS channels from a git repo.

    Returns:
        A map of channels to their associated git branches. There will be one
        entry per Channel enum value.
    """
    stdout = subprocess.run(
        [
            "git",
            "branch",
            "-r",
        ],
        cwd=git_repo,
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        encoding="utf-8",
    ).stdout.strip()

    # Match "${remote}/release-R${branch_number}-${build}.B"
    branch_re = re.compile(r"([^/]+)/(release-R(\d+)-\d+\.B)")
    branches = []
    for line in stdout.splitlines():
        line = line.strip()
        if m := branch_re.fullmatch(line):
            remote, branch_name, branch_number = m.groups()
            branches.append(
                ChannelBranch(remote, int(branch_number), branch_name)
            )

    branches.sort(key=lambda x: x.release_number)
    if len(branches) < 2:
        raise ValueError(
            f"Expected at least two branches, but only found {len(branches)}"
        )

    stable = branches[-2]
    beta = branches[-1]
    canary = ChannelBranch(
        remote=beta.remote,
        release_number=beta.release_number + 1,
        branch_name="main",
    )
    return {
        Channel.CANARY: canary,
        Channel.BETA: beta,
        Channel.STABLE: stable,
    }


def _parse_cls_from_upload_output(upload_output: str) -> list[int]:
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


def is_full_git_sha(s: str) -> bool:
    """Returns if `s` looks like a git SHA."""
    return len(s) == 40 and all(x.isdigit() or "a" <= x <= "f" for x in s)


def create_branch(git_repo: Path, branch_name: str) -> None:
    """Creates a branch in the given repo.

    Args:
        git_repo: The path to the repo.
        branch_name: The name of the branch to create.
    """
    subprocess.run(
        ["repo", "start", branch_name, "--head"],
        check=True,
        cwd=git_repo,
    )


def generate_upload_to_gerrit_cmd(
    remote: str,
    branch: str,
    reviewers: Iterable[str] = (),
    cc: Iterable[str] = (),
    ref: str = "HEAD",
    topic: str | None = None,
    wip: bool = False,
) -> list[str]:
    """Create a git push CLI command to upload to Gerrit.

    This is similar to `upload_to_gerrit`, but doesn't actually
    run the command. The returned command here is the same
    as what `upload_to_gerrit` would have run.

    Args:
        remote: The remote to upload to.
        branch: The branch to upload to.
        reviewers: Reviewers to add to the CLs.
        cc: CCs to add to the CLs.
        ref: The ref (generally a SHA) to upload. Note that any parents of this
            that Gerrit does not recognize will be uploaded.
        topic: Gerrit topic to add the change to.
        wip: Whether to upload the CL as WIP

    Returns:
        A list representing the command line args to push to the gerrit
        upstream.
    """
    # https://gerrit-review.googlesource.com/Documentation/user-upload.html#reviewers
    # for more info on the `%` params.
    option_list = [f"r={x}" for x in reviewers]
    option_list += (f"cc={x}" for x in cc)
    if wip:
        option_list.append("wip")
    if topic is not None:
        option_list.append(f"topic={topic}")
    if option_list:
        trailing_options = "%" + ",".join(option_list)
    else:
        trailing_options = ""

    return [
        "git",
        "push",
        remote,
        f"{ref}:refs/for/{branch}{trailing_options}",
    ]


def upload_to_gerrit(
    git_repo: Path,
    remote: str,
    branch: str,
    reviewers: Iterable[str] = (),
    cc: Iterable[str] = (),
    ref: str = "HEAD",
    topic: str | None = None,
    wip: bool = False,
) -> list[int]:
    """Uploads `ref` to gerrit, optionally adding reviewers/CCs.

    Args:
        git_repo: The git repo to upload.
        remote: The remote to upload to.
        branch: The branch to upload to.
        reviewers: Reviewers to add to the CLs.
        cc: CCs to add to the CLs.
        ref: The ref (generally a SHA) to upload. Note that any parents of this
            that Gerrit does not recognize will be uploaded.
        topic: Gerrit topic to add the change to.
        wip: Whether to upload the CL as WIP

    Returns:
        A list of CL numbers uploaded.
    """
    cmd = generate_upload_to_gerrit_cmd(
        remote,
        branch,
        reviewers,
        cc,
        ref,
        topic,
        wip,
    )
    run_result = subprocess.run(
        cmd,
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


def set_gerrit_label(cwd: Path, cl_id: int, label_name: str, label_value: str):
    """Sets the given gerrit label to the given value for `cl_id`.

    Args:
        cwd: the directory that the `gerrit` tool should be run in. Anywhere in
            a ChromeOS tree will do. The `gerrit` command fails if it isn't run
            from within a ChromeOS tree.
        cl_id: The CL number to apply the label to.
        label_name: Name of the gerrit label to apply, e.g., "label-as"
        label_value: Value of the label, e.g., "1"

    Raises:
        subprocess.CalledProcessError if the label wasn't set.
    """
    subprocess.run(
        ("gerrit", label_name, str(cl_id), label_value),
        cwd=cwd,
        check=True,
        stdin=subprocess.DEVNULL,
    )


def set_autoreview_topic(cwd: Path, cl_id: int) -> None:
    """Sets the autoreview topic on the given CL.

    The autoreview topic integrates with other infra we have to ping
    Chrotomation's CL reviews en masse every day. This allows these CLs to land
    in a timely manner (or more timely than tagging random reviewers, at least).
    """
    set_gerrit_label(cwd, cl_id, "topic", "crostc-auto-cl")


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
    labels = (
        (GERRIT_LABEL_AUTOSUBMIT, "1"),
        (GERRIT_LABEL_CQ, "1"),
        (GERRIT_LABEL_VERIFIED, "1"),
    )
    for label_name, label_value in labels:
        try:
            set_gerrit_label(cwd, cl_id, label_name, label_value)
        except subprocess.CalledProcessError:
            logging.warning(
                "Failed setting label %s on CL %d; ignoring", label_name, cl_id
            )


def set_autoreview_topic_and_labels(cwd: Path, cl_id: int) -> None:
    """Combines set_autoreview_topic and try_set_autosubmit_labels.

    These often go hand-in-hand.
    """
    set_autoreview_topic(cwd, cl_id)
    try_set_autosubmit_labels(cwd, cl_id)


@contextlib.contextmanager
def create_worktree(
    git_directory: Path,
    in_dir: Path | None = None,
    commitish: str | None = None,
) -> Generator[Path, None, None]:
    """Creates a temp worktree of `git_directory`, yielding the result.

    Args:
        git_directory: The directory to create a worktree of.
        in_dir: The directory to make the worktree in. If None, uses the same
            default as tempfile.TemporaryDirectory.
        commitish: A commit-like reference to checkout the worktree at.
            If not, set, uses HEAD.

    Yields:
        A worktree to work in. This is cleaned up once the contextmanager is
        exited.
    """
    with tempfile.TemporaryDirectory(
        prefix="git_utils_worktree_", dir=in_dir
    ) as t:
        tempdir = Path(t)
        logging.info(
            "Establishing worktree of %s in %s", git_directory, tempdir
        )
        cmd: list[str | os.PathLike] = [
            "git",
            "worktree",
            "add",
            "--detach",
            "--force",
            tempdir,
        ]
        if commitish:
            cmd.append(commitish)
        subprocess.run(
            cmd,
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


def resolve_ref(git_dir: Path, ref: str) -> str:
    """Resolves the given ref or SHA shorthand to a full SHA.

    Raises:
        subprocess.CalledProcessError if resolution fails
    """
    return subprocess.run(
        ["git", "rev-parse", ref],
        check=True,
        cwd=git_dir,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        encoding="utf-8",
    ).stdout.strip()


def commit_all_changes(git_dir: Path, message: str) -> str:
    """Commits all changes in `git_dir`, with the given commit message.

    This also commits any untracked files in `git_dir`.

    Args:
        git_dir: Anywhere in the git directory in which changes should be
            committed.
        message: Message of the commit message.

    Returns:
        The SHA of the committed change.
    """
    # Explicitly add using `git add -A`, since that stages all unstaged changes
    # & adds any files that aren't tracked. `git commit -a` skips adding
    # untracked files.
    subprocess.run(
        ["git", "add", "-A"],
        check=True,
        cwd=git_dir,
        stdin=subprocess.DEVNULL,
    )
    subprocess.run(
        ["git", "commit", "-m", message],
        check=True,
        cwd=git_dir,
        stdin=subprocess.DEVNULL,
    )
    return resolve_ref(git_dir, "HEAD")


def fetch(
    git_dir: Path, remote: str | None = None, branch: str | None = None
) -> None:
    """Runs `git fetch`.

    Args:
        git_dir: Directory to execute in.
        remote: If specified, only the given remote will be fetched.
        branch: If specified, only the given branch will be fetched. If branch
            is specified, remote must be, also.
    """
    if branch and not remote:
        raise ValueError("If `branch` is specified, `remote` must also be.")

    cmd = ["git", "fetch"]
    if remote:
        cmd.append(remote)
        if branch:
            cmd.append(branch)
    subprocess.run(
        cmd,
        check=True,
        cwd=git_dir,
        stdin=subprocess.DEVNULL,
    )


def checkout(git_dir: Path, ref: str) -> None:
    """Runs `git checkout ${ref}."""
    subprocess.run(
        ("git", "checkout", ref),
        check=True,
        cwd=git_dir,
        stdin=subprocess.DEVNULL,
    )


def fetch_and_checkout(git_dir: Path, remote: str, branch: str) -> None:
    """Fetches contents of `git_dir`, and checks out `remote/branch`."""
    logging.info(
        "Fetching %s and checking out to %s/%s...", git_dir, remote, branch
    )
    fetch(git_dir, remote, branch)
    checkout(git_dir, ref=f"{remote}/{branch}")


def has_discardable_changes(git_dir: Path) -> bool:
    """Returns whether discard_changes_and_checkout will discard changes."""
    stdout = subprocess.run(
        ["git", "status", "--porcelain"],
        check=True,
        cwd=git_dir,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
    ).stdout
    return bool(stdout.strip())


def discard_changes_and_checkout(git_dir: Path, ref: str):
    """Discards local changes, and checks `ref` out."""
    subprocess.run(
        ["git", "clean", "-fd"],
        check=True,
        cwd=git_dir,
        stdin=subprocess.DEVNULL,
    )
    # `git reset --hard HEAD` independently of the checkout, since we may be on
    # a branch. The goal isn't to update the potential branch to point to
    # `ref`.
    subprocess.run(
        ["git", "reset", "--hard", "HEAD"],
        check=True,
        cwd=git_dir,
        stdin=subprocess.DEVNULL,
    )
    checkout(git_dir, ref)


def maybe_show_file_at_commit(
    git_dir: Path, ref: str, path_from_git_root: str
) -> str | None:
    """Returns the given file's contents at `ref`.

    Args:
        git_dir: Directory to execute in.
        ref: SHA or ref to get the file's contents from
        path_from_git_root: The path from the git dir's root to get contents
            for.

    Returns:
        File contents, or None if the file does not exist at the given ref.
    """
    result = subprocess.run(
        ["git", "show", f"{ref}:{path_from_git_root}"],
        check=False,
        cwd=git_dir,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
    )
    if not result.returncode:
        return result.stdout

    # If this file does not exist, git will exit with code 128 and we'll get a
    # stderr message like either:
    # - `fatal: path 'foo' does not exist in 'bar'`, or
    # - `fatal: path 'foo' exists on disk, but not in 'bar'`
    is_dne = result.returncode == 128 and (
        "' does not exist in '" in result.stderr
        or "' exists on disk, but not in '" in result.stderr
    )
    if not is_dne:
        # Put `check_returncode` in a branch before the return, since mypy
        # can't determine that it always `raise`s.
        result.check_returncode()
    return None


def maybe_list_dir_contents_at_commit(
    git_dir: Path, ref: str, path_from_git_root: str
) -> list[str] | None:
    """Returns files contained in the given directory at the given commit.

    Args:
        git_dir: Directory to execute in.
        ref: SHA or ref to get the directory's contents from
        path_from_git_root: The path from the git dir's root to get directory
            contents for.

    Returns:
        None if the directory did not exist; otherwise, a nonempty list of
        files/directories contained, relative to the directory they're
        contained in. Directory names end with a trailing `/`.

    Raises:
        ValueError if the given path exists, but isn't a directory.
    """
    raw_contents = maybe_show_file_at_commit(git_dir, ref, path_from_git_root)
    if not raw_contents:
        return None

    not_a_dir = lambda: ValueError(
        f"{path_from_git_root} at {ref} in {git_dir} isn't a directory"
    )
    # If this is a directory, stdout will always start with `tree
    # ${description}\n\n` before listing entries, one line per entry.
    raw_contents_lines = raw_contents.splitlines()
    if len(raw_contents_lines) < 3:
        raise not_a_dir()

    header_line, empty_line, *results = raw_contents_lines
    if not header_line.startswith("tree "):
        raise not_a_dir()

    if empty_line.lstrip():
        raise not_a_dir()

    return results


def commits_between(git_dir: Path, from_ref: str, to_ref: str) -> Iterable[str]:
    """Return a list of git SHAs between `from_ref` and `to_ref`.

    Args:
        git_dir: git root directory to get the commits of.
        from_ref: Starting git ref, exclusive.
        to_ref: Ending git ref, inclusive.

    Returns:
        Iterator of git SHAs between the two refs, oldest to newest.
    """
    return reversed(
        subprocess.run(
            ["git", "log", "--format=%H", f"{from_ref}..{to_ref}"],
            check=True,
            cwd=git_dir,
            stdout=subprocess.PIPE,
            encoding="utf-8",
        )
        .stdout.strip()
        .splitlines()
    )


def format_patch(git_dir: Path, ref: str) -> str:
    """Format a patch for a single git ref.

    Args:
        git_dir: Root directory for a given local git repository.
        ref: Git ref to make a patch for.

    Returns:
        The patch file contents.
    """
    logging.debug("Formatting patch for %s^..%s", ref, ref)
    proc = subprocess.run(
        ["git", "format-patch", "--stdout", f"{ref}^..{ref}"],
        cwd=git_dir,
        encoding="utf-8",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        check=True,
    )
    contents = proc.stdout.strip()
    if not contents:
        raise ValueError(f"No git diff between {ref}^..{ref}")
    logging.debug("Patch diff is %d lines long", contents.count("\n"))
    return contents


def get_message_subject(git_dir: Path, ref: str) -> str:
    """Return the commit message's subject line."""
    return subprocess.run(
        ("git", "show", "--format=%s", "-s", ref),
        cwd=git_dir,
        encoding="utf-8",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        check=True,
    ).stdout.strip()


def get_commit_timestamp(git_dir: Path, ref: str) -> int:
    """Return the commit message's commit-time as a UNIX timeestamp."""
    stdout = subprocess.run(
        ("git", "show", "--format=%ct", "-s", ref),
        cwd=git_dir,
        encoding="utf-8",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        check=True,
    ).stdout.strip()
    return int(stdout)


def get_commit_message_metadata(git_dir: Path, ref: str) -> dict[str, str]:
    """Return footer information for a given commit."""
    commit_msg = (
        subprocess.run(
            ["git", "show", "--format=%b", "-s", ref],
            cwd=git_dir,
            encoding="utf-8",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            check=True,
        )
        .stdout.strip()
        .splitlines()
    )
    return parse_message_metadata(commit_msg)


def parse_message_metadata(message_lines: Iterable[str]) -> dict[str, str]:
    """Return a dictionary of commit message lines' directives."""
    regex = re.compile(r"([-\w.]+):(.+)")
    result = {}
    for line in message_lines:
        # Must not lstrip the line, as leading whitespace here is important.
        line = line.rstrip()
        if match := regex.match(line):
            key, value = match.groups()
            result[key] = value.strip()
    return result


def merge_base(git_dir: Path, refs: Sequence[str]) -> str | None:
    """Return the git merge-base --octopus between branches.

    Args:
        git_dir: Root directory for a given local git repository.
        refs: Sequence of commit refs to find the merge base of.

    Returns:
        An Optional string which is the git SHA of the merge base.
        If no merge-base exists or there was an error, return None.
    """
    cmd = ["git", "merge-base", "--octopus"]
    cmd += refs
    proc = subprocess.run(
        cmd,
        check=False,
        cwd=git_dir,
        encoding="utf-8",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
    )
    if not proc.returncode:
        return proc.stdout.strip()
    return None


def branch_list(git_dir: Path, glob: str | None = None) -> list[str]:
    """List branches, optionally matching a given glob."""
    addendum = [glob] if glob else []
    return (
        subprocess.run(
            ["git", "branch", "--format=%(refname)", "-a", "-l"] + addendum,
            check=True,
            cwd=git_dir,
            encoding="utf-8",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
        )
        .stdout.strip()
        .splitlines()
    )


def commit_author_email(git_dir: Path, ref: str) -> str:
    """Return the author email of a given git ref."""
    return subprocess.run(
        ["git", "show", "--format=%aE", ref],
        cwd=git_dir,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        encoding="utf-8",
        check=True,
    ).stdout.strip()


def log(
    git_dir: Path,
    head: str,
    stop_at: str | None = None,
    log_format: str | None = None,
) -> str:
    """Runs `git log` between `head` and `stop_at`.

    Args:
        git_dir: Directory to run `git log` in.
        head: Commit to start at. This is always included in the log.
        stop_at: Optional commit to stop at. This is _not_ included in the log.
        log_format: String to pass to `git log`'s `--format` flag.

    Returns:
        The output out `git log`.
    """
    cmd = ["git", "log"]
    if log_format:
        cmd.append(f"--format={log_format}")

    cmd.append(f"{stop_at}..{head}" if stop_at else head)
    return subprocess.run(
        cmd,
        check=True,
        cwd=git_dir,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        encoding="utf-8",
    ).stdout


def query_gerrit(chromeos_root: Path, query: str) -> list[int]:
    """Returns CLs that match the given `query`."""
    results = subprocess.run(
        ("gerrit", "--raw", "search", query),
        check=True,
        cwd=chromeos_root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        encoding="utf-8",
    ).stdout
    return [int(x) for x in results.split()]
