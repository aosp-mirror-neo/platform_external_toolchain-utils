# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Uses tooling to clean up warning exemptions for a set of git repos.

This tooling expects that you've recently run
`parse_and_apply_warning_exemptions.py` on the tree in question. Specifically:

1. Tools like `bpfmt` are available.
2. Repos that `parse_and_apply_warning_exemptions` branched and modified are
   still in that branched-and-modified state.
"""

import argparse
import concurrent.futures
import dataclasses
import itertools
import logging
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from typing import Iterator

from android_tools import android_paths
from android_tools import parse_and_apply_warning_exemptions as parse_and_apply
from cros_utils import git_utils


_HUNK_HEADER_RE = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)")

# More info on `git diff`'s output can be found here:
# https://git-scm.com/docs/diff-format#generate_patch_text_with_p


@dataclasses.dataclass(frozen=True, eq=True)
class DiffFileHeader:
    """A file header in a diff.

    This is expected to be a produced by `git diff` on a single file, on a repo
    which is not currently undergoing any special git operation (e.g., merging).
    """

    # No special parsing is done, but we _do_ need to treat these file headers
    # specially. Example list contents are:
    # [
    #   "diff --git a/... b/...",
    #   "index ...",
    #   "--- a/foo/bar",
    #   "+++ b/baz/qux",
    # ]
    lines: list[str]

    @staticmethod
    def parse(
        lines: list[str], start_idx: int = 0
    ) -> tuple["DiffFileHeader", int] | None:
        """Tries to parse a file header at `start_idx`.

        Returns:
            A tuple of (file_header, first_line_idx_after_header), or None if
            parsing failed.
        """
        file_header_prefix = "diff --git "
        if not lines[start_idx].startswith(file_header_prefix):
            return None

        # So the number of potential follow-ups here is pretty high.
        # Rather than matching those exactly, just search for a
        # hunk header (which indicates diffs exist in the file), or
        # another file header (which indicates that the file may have just been
        # moved, or something).

        i = start_idx + 1
        while i < len(lines):
            if lines[i].startswith(file_header_prefix) or _HUNK_HEADER_RE.match(
                lines[i]
            ):
                break
            i += 1

        return DiffFileHeader(lines=lines[start_idx:i]), i


@dataclasses.dataclass(frozen=True, eq=True)
class DiffHunk:
    """A single hunk in a diff.

    This is expected to be a hunk diff produced by `git diff` on a single file,
    on a repo which is not currently undergoing any special git operation (e.g.,
    merging).
    """

    header: str
    lines: list[str]
    old_start: int
    old_len: int
    new_start: int
    new_len: int
    rest: str

    @staticmethod
    def parse(
        lines: list[str], start_idx: int = 0
    ) -> tuple["DiffHunk", int] | None:
        """Tries to parse a hunk at `start_line_idx`.

        Returns:
            A tuple of (hunk, first_line_idx_after_hunk), or None if parsing
            failed.
        """
        line = lines[start_idx]
        match = _HUNK_HEADER_RE.match(line)
        if not match:
            return None

        hunk_header = line
        (
            old_start_str,
            old_len_str,
            new_start_str,
            new_len_str,
            rest,
        ) = match.groups()

        old_start = int(old_start_str)
        new_start = int(new_start_str)
        # Note that if these are omitted, their value is implicitly 1.
        old_len = int(old_len_str) if old_len_str else 1
        new_len = int(new_len_str) if new_len_str else 1

        hunk_start_idx = start_idx + 1
        old_lines_read = 0
        new_lines_read = 0

        if not (old_len or new_len):
            raise ValueError("0-length hunk is nonsensical")

        i = None
        for i in range(hunk_start_idx, len(lines)):
            # We verify that the hunk header was well-formed after this loop, so
            # `>=` here is fine.
            if old_lines_read >= old_len and new_lines_read >= new_len:
                break

            hunk_line = lines[i]
            if hunk_line.startswith("+"):
                new_lines_read += 1
            elif hunk_line.startswith("-"):
                old_lines_read += 1
            elif hunk_line.startswith(" ") or not hunk_line:
                old_lines_read += 1
                new_lines_read += 1
            elif not hunk_line.startswith("\\ No newline at end of file"):
                raise ValueError(
                    f"Invalid line in diff hunk parsing: {hunk_line}"
                )

        if not (old_lines_read == old_len and new_lines_read == new_len):
            raise ValueError(
                f"Invalid diff hunk; "
                f"read {old_lines_read} old lines, want {old_len}, and "
                f"read {new_lines_read} new lines, want {new_len}."
            )

        # Checked for a zero-length hunk above, so the loop must have iterated.
        assert i, "Zero-length hunk that wasn't caught earlier?"
        return (
            DiffHunk(
                header=hunk_header,
                lines=lines[hunk_start_idx:i],
                old_start=old_start,
                old_len=old_len,
                new_start=new_start,
                new_len=new_len,
                rest=rest,
            ),
            i,
        )


def remove_blank_lines_from_hunk(hunk: DiffHunk) -> list[str] | None:
    """Per-hunk version of remove_blank_lines_from_diff.

    Returns:
        A new hunk with whitespace removed. If the hunk only consisted of
        `isspace()` additions, returns None.
    """
    new_hunk_lines = []
    removed_count = 0
    for hunk_line in hunk.lines:
        if hunk_line.startswith("+") and not hunk_line[1:].rstrip():
            removed_count += 1
            continue

        new_hunk_lines.append(hunk_line)

    if not any(x.startswith("+") or x.startswith("-") for x in new_hunk_lines):
        return None

    # _Technically_ we should be updating 'new' line numbers in headers if a
    # prior hunk is modified, but git is happy to fuzz.
    if not removed_count:
        return [hunk.header] + hunk.lines

    new_len_updated = hunk.new_len - removed_count

    old_range = f"-{hunk.old_start},{hunk.old_len}"
    new_range = f"+{hunk.new_start},{new_len_updated}"
    new_hunk_header = f"@@ {old_range} {new_range} @@{hunk.rest}"
    new_hunk_lines.insert(0, new_hunk_header)
    return new_hunk_lines


def iterate_diff_pieces(
    git_diff: str,
) -> Iterator[DiffFileHeader | DiffHunk | str]:
    """Yields the 'pieces' of the given diff.

    "Pieces" is, loosely, "either a meaningful, structured part of a diff, or a
    str that represents the current line."

    For example, given a diff containing:

    '''
    Foo bar
    --- a/foo
    +++ b/foo
    @@ -1 +1 @@
       line
    trailing line

    '''

    This will yield the following elements:
    ["Foo bar", "--- a/foo", "+++ b/foo", DiffHunk(...), "trailing line", ""]
    """
    # `splitlines()` will ignore a trailing newline, `split()` will preserve it
    # as an empty string.
    lines = git_diff.split("\n")

    line_idx = 0
    while line_idx < len(lines):
        if parsed_header := DiffFileHeader.parse(lines, line_idx):
            header, line_idx = parsed_header
            yield header
        elif parsed_hunk := DiffHunk.parse(lines, line_idx):
            hunk, line_idx = parsed_hunk
            yield hunk
        else:
            yield lines[line_idx]
            line_idx += 1


def remove_blank_lines_from_diff(git_diff: str) -> str:
    """This function removes blank lines added by `git_diff`.

    Sometimes, Soong tooling will lead to weird constructs that can't be
    autoformatted away. For example, a diff might contain the following hunk
    (ignoring the header):

    ```
     cc_defaults {
       name: "foo",
    +  cflags: ["-Wbar"],
    +
     }
    ```

    This modifies the given diff to remove the empty added line. It does nothing
    about removed or unchanged lines.
    """
    output_lines = []

    # Defer adding diff file headers, since git isn't fond of file headers with
    # no corresponding diff.
    pending_file_header = None
    pending_header_had_hunk = False

    for piece in iterate_diff_pieces(git_diff):
        if isinstance(piece, DiffFileHeader):
            # If there were no hunks associated with the pending header
            # originally, emit it now.
            if pending_file_header and not pending_header_had_hunk:
                output_lines.extend(pending_file_header.lines)

            pending_file_header = piece
            pending_header_had_hunk = False
            continue

        if isinstance(piece, DiffHunk):
            pending_header_had_hunk = True
            if new_hunk := remove_blank_lines_from_hunk(piece):
                if pending_file_header:
                    output_lines += pending_file_header.lines
                    pending_file_header = None

                output_lines.extend(new_hunk)
            continue

        output_lines.append(piece)

    if pending_file_header and not pending_header_had_hunk:
        output_lines.extend(pending_file_header.lines)

    return "\n".join(output_lines)


@dataclasses.dataclass(frozen=True)
class RunConfig:
    """Shared attributes between clean-up runs."""

    android_tree: Path
    gemini_prompt: str
    bpfmt: Path


def read_gemini_prompt() -> str:
    prompt_md = (
        android_paths.script_toolchain_utils_root()
        / "android_tools"
        / "clean_warning_exemptions_prompt.md"
    )
    return prompt_md.read_text(encoding="utf-8")


_DASH_W_NO_STRING_RE = re.compile(r'"(-Wno-[^"]+)"')


def diff_trivially_has_no_dedupe_potential(file_diff: str) -> bool:
    """Returns True if `file_diff` has no dedupe potential.

    This is meant to be a quick heuristic that lets us skip Gemini in cases
    where it's obviously not needed. The simple act of starting Gemini and
    sending a query for it to respond "it's obvious that no changes are needed,"
    takes seconds of wall-time. Taking a few milliseconds instead speeds things
    up dramatically in the common case that a file has just one change.
    """
    dash_w_strings = set()
    for piece in iterate_diff_pieces(file_diff):
        if not isinstance(piece, DiffHunk):
            continue

        for line in piece.lines:
            if not line.startswith("+"):
                continue

            for m in _DASH_W_NO_STRING_RE.findall(line):
                # If we've already found this `-Wno-` string elsewhere in the
                # diff, there's room for it to be deduped.
                if m in dash_w_strings:
                    return False
                dash_w_strings.add(m)

    return True


class RetryableRunOnFileError(Exception):
    """Raised when `run_on_file` fails in a way that may be retryable."""


def run_bpfmt(
    config: RunConfig,
    git_repo_path: Path,
    files: list[Path],
) -> None:
    try:
        cmd: list[str | Path] = [config.bpfmt, "-w"]
        cmd += files
        subprocess.run(
            cmd,
            cwd=git_repo_path,
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except subprocess.CalledProcessError as e:
        # bpfmt should be deterministic, but Gemini will (very) rarely insert
        # syntax errors. `bpfmt` failing is indicative of a syntax error.
        logging.error(
            "Formatting failed on %s; stdout/stderr:\n%s",
            files,
            e.stdout,
        )
        raise


def run_gemini_on_file(
    config: RunConfig,
    git_repo: Path,
    file_in_repo: Path,
) -> None:
    """Runs transformations on a specific file in a git repo.

    Args:
        config: RunConfig for the cleanups.
        git_repo: Git repo inside of the Android tree to modify.
        file_in_repo: File to modify.
    """
    git_file = git_repo / file_in_repo
    git_repo_path = config.android_tree / git_repo

    file_diff = git_utils.diff(
        git_dir=git_repo_path,
        ref_start="HEAD~",
        ref_end="HEAD",
        only_files=(file_in_repo,),
    )

    logging.info("Running Gemini on %s...", git_file)
    # Use gemini-cli rather than Gemini's API, since gemini-cli has the
    # built-in ability to edit files/etc.
    try:
        gemini_run_result = subprocess.run(
            (
                "gemini",
                # TODO(b/462692566): Gemini 3 is new, and has a lower rate-limit
                # (in requests per day) than 2.5. Prefer 2.5 until that
                # improves.
                "--model=gemini-2.5-pro",
                # This isn't an interactive session; approve all edits, but
                # stop short of running anything else.
                "--approval-mode=auto_edit",
                "\n\n".join((config.gemini_prompt, "```", file_diff, "```")),
            ),
            cwd=git_repo_path,
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.CalledProcessError as e:
        logging.error(
            "Gemini failed on %s; stdout/stderr:\n%s",
            git_file,
            e.stdout,
        )
        raise RetryableRunOnFileError from e

    logging.debug(
        "Gemini's output on %s was:\n%s",
        git_file,
        gemini_run_result.stdout,
    )

    # Format all modified files. This is nice for cleanliness, but also helps
    # catch if Gemini introduced syntax errors.
    unstaged_files = [
        Path(x)
        for x in git_utils.list_unstaged_files_changed(git_repo_path)
        if x.endswith("Android.bp")
    ]
    if not unstaged_files:
        logging.info(
            "Gemini made no changes to %s; skipping formatting", git_file
        )
        return

    logging.info("Formatting %s in %s...", unstaged_files, git_repo)
    try:
        run_bpfmt(config, git_repo_path, unstaged_files)
    except subprocess.CalledProcessError as e:
        raise RetryableRunOnFileError from e


def run_gemini_on_file_with_retries(
    config: RunConfig,
    git_repo: Path,
    file_in_repo: Path,
    max_retries: int = 5,
    retry_backoff_secs: int = 1,
) -> None:
    """Runs Gemini on the given file, retrying as needed.

    Note that this will stage _some_ files in the given repository.
    """
    # Arbitrarily selected retry limit.
    max_retries = 5
    retry_backoff_secs = 1

    git_repo_path = config.android_tree / git_repo
    git_file = git_repo_path / file_in_repo

    # Stage the state of the repo before we start. `run_on_file` likely
    # modifies `file_in_repo`, and if it fails, we want to reset to the state we
    # were in before we started.
    #
    # In rare cases, Gemini may modify _multiple_ files in `run_on_file` (e.g.,
    # if the `defaults` is in a distant-but-still-in-repo Android.bp), so the
    # full repo is analyzed rather than just `file_in_repo`.
    git_utils.stage_all_unstaged_changes(git_repo_path, quiet=True)

    for i in itertools.count():
        try:
            run_gemini_on_file(config, git_repo, file_in_repo)
        except RetryableRunOnFileError as e:
            if i == max_retries:
                # It's a bug if these don't have a `__cause__`, but type
                # definitions say `__cause__` can be None, so placate mypy.
                #
                # Simultaneously, disable `pylint`. For some reason, it asserts
                # that `e.__cause__` has a constant value, which makes the
                # conditional pointless.
                #
                # pylint: disable=using-constant-test
                if e.__cause__:
                    raise e.__cause__
                raise e
            logging.exception(
                "Caught exception running on file %s, retrying in %ds",
                git_file,
                retry_backoff_secs,
            )
        else:
            return

        time.sleep(retry_backoff_secs)
        retry_backoff_secs *= 2

        # Restore repo state to what was staged above.
        git_utils.checkout(git_repo_path, ref=None, paths=(".",))

    assert False, "Unreachable"


def amend_head_if_necessary(run_config: RunConfig, git_repo: Path) -> bool:
    """Amends HEAD if changes are present; returns True if amended."""
    git_repo_path = run_config.android_tree / git_repo
    if not git_utils.has_discardable_changes(git_repo_path):
        logging.debug("No changes made to %s; not amending", git_repo_path)
        return False

    logging.debug("Changes made to %s; amending", git_repo_path)
    git_utils.amend_head_with_all_changes(git_repo_path, quiet=True)
    return True


def run_on_repo(config: RunConfig, git_repo: Path) -> bool:
    """Runs the cleanup process on a single repository.

    Args:
        config: RunConfig for this clean_warning_exemptions_prompt invocation.
        git_repo: Git repository relative to Android's root.

    Returns:
        True if the repository was amended, False otherwise.
    """
    git_repo_path = config.android_tree / git_repo
    changed_files = git_utils.list_files_changed_by_commit(
        git_dir=git_repo_path, ref="HEAD"
    )

    initial_diff = git_utils.diff(
        git_dir=git_repo_path,
        ref_start="HEAD~",
    )
    if diff_trivially_has_no_dedupe_potential(initial_diff):
        logging.info(
            "Skipping Gemini on files in %s; no dedupe potential exists.",
            git_repo,
        )
    else:
        # NOTE: Fixing up individual files in a repo is _almost_ something we
        # can parallelize, but it is not easy to do so.
        #
        # 1. Gemini performs _much_ better at this task if it's asked to
        #    evaluate a single file at a time, so `gemini-cli` is only asked to
        #    edit one file per invocation.
        # 2. `git` operations in `run_on_file_with_retries` will sometimes break
        #    if run concurrently with other `git` operations.
        # 3. `gemini-cli` may edit a file **elsewhere in a git repo** if it
        #    determines that doing so allows for dedupe (b/455912162#comment12).
        for file in changed_files:
            if Path(file).name != "Android.bp":
                logging.warning(
                    "Weird: found non-Android.bp file update to %s. Ignoring.",
                    git_repo / file,
                )
                continue

            run_gemini_on_file_with_retries(config, git_repo, Path(file))

    # ...Now we've made an arbitrary set of changes to an arbitrary set of
    # Android.bp files. Some formatted, some not. Fix blank lines up as
    # necessary.
    full_diff = git_utils.diff(
        git_dir=git_repo_path,
        ref_start="HEAD~",
    )

    cleaned_diff = remove_blank_lines_from_diff(full_diff)
    if full_diff != cleaned_diff:
        logging.info("Cleaning up blank lines in %s...", git_repo)
        git_utils.checkout(git_repo_path, "HEAD~", paths=(".",))
        try:
            git_utils.apply_patch_contents(git_repo_path, cleaned_diff)
        except subprocess.CalledProcessError:
            logging.error("Failed applying patch:\n%s", cleaned_diff)
            raise

    # Finally, run a formatting pass. All files should be syntactically valid,
    # since:
    # - Gemini introducing syntax errors raises an exception.
    # - The only other room to add a syntax error is blank line removal, and
    #   that _shouldn't_ introduce issues.
    all_bp_files_with_changes = [
        Path(x)
        for x in git_utils.list_uncommitted_files_changed(git_repo_path)
        if x.endswith("Android.bp")
    ]

    if all_bp_files_with_changes:
        all_bp_files_with_changes.sort()
        logging.info(
            "Running final formatting pass on:%s",
            "".join(f"\n  {git_repo / x}" for x in all_bp_files_with_changes),
        )
        run_bpfmt(config, git_repo_path, all_bp_files_with_changes)

    return amend_head_if_necessary(config, git_repo)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--debug", action="store_true", help="Enable debug logging"
    )
    parser.add_argument(
        "--android-tree",
        type=Path,
        help="""
        Android tree to modify. If not specified, autodetection from this
        script's directory will be attempted.
        """,
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=8,
        help="""
        Max jobs to run at once. Generally speaking, these jobs will spend most
        of their wall time an invocation of `gemini-cli`. To avoid
        rate-limiting, this is kept pretty low.
        """,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--only-repo",
        type=Path,
        help="""
        Only run on the repository given here. This repository should be
        relative to the Android root, e.g., `bionic/`.
        """,
    )
    group.add_argument(
        "--summary-file",
        type=Path,
        help="""
        The --update-summary-file generated by the warning suppression script.
        """,
    )
    upload_group = parser.add_mutually_exclusive_group(required=True)
    upload_group.add_argument(
        "--upload",
        action="store_true",
        help="Run `repo upload` on changed repos.",
    )
    upload_group.add_argument(
        "--no-upload",
        action="store_true",
        help="Do not upload changes to Gerrit.",
    )
    opts = parser.parse_args(argv)

    if opts.android_tree:
        android_paths.assert_is_valid_android_tree_root(
            parser, opts.android_tree
        )

    return opts


def upload_changes(
    android_tree: Path,
    thread_pool: concurrent.futures.ThreadPoolExecutor,
    repos_to_upload: list[Path],
) -> list[Path]:
    """Uploads all changes.

    Returns:
        A list of git repos where uploading failed.
    """
    # Bound this heavily, since gerrit may rate-limit a significant number of
    # requests.
    upload_semaphore = threading.BoundedSemaphore(2)

    def upload_one_repo(repo_subpath: Path) -> int:
        repo = android_tree / repo_subpath
        with upload_semaphore:
            cls_uploaded = git_utils.upload_to_gerrit(
                git_repo=repo,
                remote=git_utils.ANDROID_INTERNAL_REMOTE,
                branch=git_utils.ANDROID_MAIN_BRANCH,
                # Assume this has already been uploaded with the correct topic
                # and `wip` state.
            )

        if not cls_uploaded:
            raise ValueError(f"Unexpected: no CLs uploaded in {repo}")

        if len(cls_uploaded) != 1:
            logging.warning(
                "Uploaded %d CLs in %s somehow - ignoring all but the last",
                len(cls_uploaded),
                repo,
            )
        return cls_uploaded[-1]

    logging.info("Uploading %d CLs...", len(repos_to_upload))

    # Don't use `map`, since we filter on exceptions later.
    upload_futures = [
        thread_pool.submit(upload_one_repo, x) for x in repos_to_upload
    ]

    exceptions = []
    for repo, upload_result in zip(repos_to_upload, upload_futures):
        if e := upload_result.exception():
            exceptions.append((repo, e))

    # List exceptions after all threads are done executing for clarity.
    for repo, e in exceptions:
        logging.error(
            "Exception caught uploading changes to %s", repo, exc_info=e
        )

    return [x for x, _ in exceptions]


def main(argv: list[str]) -> None:
    opts = parse_args(argv)
    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.DEBUG if opts.debug else logging.INFO,
    )

    android_tree: Path = (
        opts.android_tree or android_paths.script_android_checkout_or_exit()
    )
    summary_file_path: Path = opts.summary_file
    only_repo: Path | None = opts.only_repo

    if only_repo:
        only_repo_git_dir = android_tree / only_repo / ".git"
        if not only_repo_git_dir.exists():
            sys.exit(
                f"Path at {only_repo_git_dir} doesn't exist; check that "
                "`--only-repo`'s value is correct."
            )

        repos_to_run_on = [only_repo]
    else:
        summary_file = parse_and_apply.ExemptionSummary.from_file(
            summary_file_path
        )
        repos_to_run_on = [Path(x) for x in summary_file.exemptions]

    bpfmt_path = parse_and_apply.bpfmt_path(android_tree)
    if not bpfmt_path.exists():
        sys.exit(
            f"No bpfmt found at {bpfmt_path} - are you using the tree you "
            "ran parse_and_apply_warning_exemptions on? Reminder that you can "
            "always build it via `m blueprint_tools`."
        )

    run_config = RunConfig(
        android_tree=android_tree,
        gemini_prompt=read_gemini_prompt(),
        bpfmt=bpfmt_path,
    )

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=opts.jobs
    ) as thread_pool:
        future_to_repo = {
            thread_pool.submit(run_on_repo, run_config, repo): repo
            for repo in repos_to_run_on
        }

        amended_repos = []
        exceptions = []

        for future in concurrent.futures.as_completed(future_to_repo):
            repo = future_to_repo[future]
            try:
                amended = future.result()
                if amended:
                    amended_repos.append(repo)
            except Exception as e:
                exceptions.append((repo, e))

        failed_uploads = []
        if opts.upload and amended_repos:
            failed_uploads = upload_changes(
                android_tree,
                thread_pool,
                amended_repos,
            )
        elif not amended_repos:
            logging.info("No repos amended, so nothing to upload.")

        # Log exceptions/errors in a batch afterward, so they don't get lost in
        # any logging output above.
        for repo, exc in exceptions:
            if isinstance(exc, subprocess.CalledProcessError):
                logging.error(
                    "Exception caught making changes to %s; stdstreams:\n%s",
                    repo,
                    exc.stdout,
                    exc_info=exc,
                )
            else:
                logging.error(
                    "Exception caught making changes to %s",
                    repo,
                    exc_info=exc,
                )

        if failed_uploads:
            logging.error(
                "Uploading failed for repo(s):%s",
                "".join(f"\n- {x}" for x in failed_uploads),
            )

        had_failures = exceptions or failed_uploads

        logging.info("Applied updates to %d repos", len(amended_repos))
        sys.exit(1 if had_failures else 0)
