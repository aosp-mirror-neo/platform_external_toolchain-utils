# Copyright 2019 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Returns the latest LLVM version's hash."""

import argparse
import contextlib
import dataclasses
import fcntl
import functools
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Iterator, Optional, Tuple, Union

from cros_utils import cros_paths
from cros_utils import git_utils
from llvm_tools import chroot
from llvm_tools import cros_llvm_repo
from llvm_tools import git_llvm_rev
from llvm_tools import llvm_next
from llvm_tools import manifest_utils
from llvm_tools import subprocess_helpers


_LLVM_GIT_URL = (
    "https://chromium.googlesource.com/external/github.com/llvm/llvm-project"
)

KNOWN_HASH_SOURCES = (
    "google3",
    "google3-unstable",
    "llvm",
    "llvm-next",
    "tot",
)


def GetVersionFrom(src_dir: Union[Path, str], git_hash: str) -> int:
    """Obtain an SVN-style version number based on the LLVM git hash passed in.

    Args:
        src_dir: LLVM's source directory.
        git_hash: The git hash.

    Returns:
        An SVN-style version number associated with the git hash.
    """

    version = git_llvm_rev.translate_sha_to_rev(
        git_llvm_rev.LLVMConfig(remote="origin", dir=src_dir), git_hash
    )
    # Note: branches aren't supported
    assert version.branch == git_llvm_rev.MAIN_BRANCH, version.branch
    return version.number


def GetGitHashFrom(src_dir: Union[Path, str], version: int) -> str:
    """Finds the commit hash(es) of the LLVM version in the git log history.

    Args:
        src_dir: The LLVM source tree.
        version: The version number.

    Returns:
        A git hash string corresponding to the version number.

    Raises:
        subprocess.CalledProcessError: Failed to find a git hash.
    """

    return git_llvm_rev.translate_rev_to_sha(
        git_llvm_rev.LLVMConfig(remote="origin", dir=src_dir),
        git_llvm_rev.Rev(branch=git_llvm_rev.MAIN_BRANCH, number=version),
    )


def ParseLLVMMajorVersion(cmakelist: str) -> Optional[str]:
    """Reads CMakeList.txt file contents for LLVMMajor Version.

    Args:
        cmakelist: contents of CMakeList.txt

    Returns:
        The major version number as a string, or None if it couldn't be found.
    """
    match = re.search(
        r"\n\s+set\(LLVM_VERSION_MAJOR (?P<major>\d+)\)", cmakelist
    )
    if not match:
        return None
    return match.group("major")


@functools.lru_cache(maxsize=1)
def GetLLVMMajorVersion(git_hash: Optional[str] = None) -> str:
    """Reads llvm/CMakeList.txt file contents for LLVMMajor Version.

    Args:
        git_hash: git hash of llvm version as string or None for top of trunk

    Returns:
        The major version number as a string

    Raises:
        ValueError: The major version cannot be parsed from cmakelist or
          there was a failure to checkout git_hash version
        FileExistsError: The src directory doe not contain CMakeList.txt
    """
    # b/325895866#comment36: the LLVM version number was moved from
    # `llvm/CMakeLists.txt` to `cmake/Modules/LLVMVersion.cmake` in upstream
    # commit 81e20472a0c5a4a8edc5ec38dc345d580681af81 (r530225). Until we no
    # longer care about looking before that, we need to support searching both
    # files.
    cmakelists_paths = (
        "llvm/CMakeLists.txt",
        "cmake/Modules/LLVMVersion.cmake",
    )

    repo = GetCachedUpToDateReadOnlyLLVMRepo()
    ref = git_hash if git_hash else "HEAD"
    for path in cmakelists_paths:
        contents = git_utils.maybe_show_file_at_commit(repo.path, ref, path)
        if contents is None:
            # Ignore the file if it doesn't exist yet.
            continue
        if version := ParseLLVMMajorVersion(contents):
            return version

    raise ValueError(
        f"Major version could not be parsed from any of {cmakelists_paths}"
    )


def _LockAndCloneLLVMProject(clone_target: Path, tmpdir_path: Path):
    """Creates and locks `tmpdir_path`, and clones LLVM into it.

    Multithreading and multiprocessing safe.

    Args:
        clone_target: Where to place llvm-project.
        tmpdir_path: A temporary directory to sync llvm-project in. Must share
            the same parent directory as clone_target.
    """
    # This code is subtle, and relies on Linux guarantees outlined here:
    # https://www.kernel.org/doc/Documentation/filesystems/directory-locking
    #
    # Specifically, this heavily leverages the idea that there's a total
    # ordering of dirent additions/removals for each directory in a file
    # system.
    assert (
        tmpdir_path.parent == clone_target.parent
    ), f"{tmpdir_path} and {clone_target} must share a parent."

    # `exist_ok=True` covers races with other processes.
    tmpdir_path.mkdir(exist_ok=True)
    try:
        tmpdir_fd = os.open(tmpdir_path, os.O_RDONLY | os.O_DIRECTORY)
    except FileNotFoundError:
        # If this isn't found, another process removed this dir. This code
        # _only_ removes this dir on a successful sync, so it must be that the
        # sync was successful.
        assert (
            clone_target.exists()
        ), f"{clone_target} should exist if {tmpdir_path} doesn't."
        return

    try:
        # Note that the lock is implicitly unlocked by the
        # `os.close(tmpdir_fd)` in the `finally` block.
        fcntl.flock(tmpdir_fd, fcntl.LOCK_EX)

        # If a racing sync succeeded, exit early. Note that the existence of
        # `clone_target` implies that our lock of `tmpdir_fd` may be
        # non-exclusive (see the comment above `os.rename` below for more), as
        # racing processes might've removed & recreated `tmpdir_path` since
        # this one opened it.
        if clone_target.exists():
            # Catch FileNotFoundError due to non-exclusivity. In any case, it
            # must be empty, since syncs are never started if `clone_target`
            # exists.
            try:
                tmpdir_path.rmdir()
            except FileNotFoundError:
                pass
            return

        # Clean up from any potentially-incomplete racing syncs.
        for child in tmpdir_path.iterdir():
            shutil.rmtree(child)

        subprocess.run(
            ["git", "clone", _LLVM_GIT_URL, "."],
            check=True,
            cwd=tmpdir_path,
        )

        # This `rename` makes our lock on `tmpdir_path` non-global, which is
        # less dangerous than it may seem, since it simultaneously brings
        # `clone_target` into existence.
        #
        # Leveraging Linux's file locking guarantees, this means that
        # `os.open`s of `tmpdir_path`s that are created after this is renamed
        # _necessarily_ have a view of `tmpdir_path.parent` that includes
        # `clone_target`.
        os.rename(tmpdir_path, clone_target)
    finally:
        os.close(tmpdir_fd)


def _GetToolchainUtilsCopyOfLLVMProject() -> Path:
    """Inits and returns ${toolchain_utils}/llvm_tools/llvm-project-copy.

    Returns:
        The absolute path to the 'llvm-project-copy' directory in 'llvm_tools'
    """
    # NOTE: At the moment, the initial sync of this is not thread-safe. It'd be
    # nice to have a flock of some sort of toolchain-utils-local stamp for
    # that.
    llvm_project_copy = Path(__file__).resolve().parent / "llvm-project-copy"
    if llvm_project_copy.is_dir():
        return llvm_project_copy

    print(
        f"llvm-project checkout requested; checking out {llvm_project_copy}.\n"
        "This may take a while, but only has to be done once.",
        file=sys.stderr,
    )
    tmp_llvm_project_copy = llvm_project_copy.parent / ".llvm-project-copy"
    _LockAndCloneLLVMProject(
        clone_target=llvm_project_copy, tmpdir_path=tmp_llvm_project_copy
    )
    assert llvm_project_copy.is_dir(), llvm_project_copy
    return llvm_project_copy


@dataclasses.dataclass(frozen=True)
class ReadOnlyLLVMRepo:
    """Describes an LLVM repository, and provides some useful ops on it.

    Strictly speaking, `read-only` is a bit of a misnomer: the git data of this
    repo may be updated by users of this class. The expectation is that the
    working tree won't be modified, though.
    """

    # Path to the repository.
    path: Path
    # The name of the remote to query.
    remote: str
    # The ref that points to the upstream's main branch.
    upstream_main: str

    def GetRevisionFromHash(self, git_hash: str) -> int:
        """Converts a SHA to an svn-like revision."""
        version = git_llvm_rev.translate_sha_to_rev(
            git_llvm_rev.LLVMConfig(remote=self.remote, dir=self.path), git_hash
        )
        # Note: branches aren't supported. Always match against
        # `git_llvm_rev.MAIN_BRANCH` instead of `upstream_main`, since
        # `git_llvm_rev` doesn't acknowledge `upstream_main`.
        assert version.branch == git_llvm_rev.MAIN_BRANCH, (
            "Revisions only make sense on main, but given git hash was "
            f"on {version.branch}"
        )
        return version.number

    def GetHashFromRevision(self, revision: int) -> str:
        """Converts a svn-like revision to a SHA on main."""
        return git_llvm_rev.translate_rev_to_sha(
            git_llvm_rev.LLVMConfig(remote=self.remote, dir=self.path),
            git_llvm_rev.Rev(branch=self.upstream_main, number=revision),
        )


def GetReadOnlyLLVMRepo() -> ReadOnlyLLVMRepo:
    """Returns a read-only LLVM repository."""
    if cros_llvm := cros_llvm_repo.try_get_path():
        return ReadOnlyLLVMRepo(
            path=cros_llvm,
            remote=cros_llvm_repo.UPSTREAM_REMOTE,
            upstream_main=cros_llvm_repo.UPSTREAM_MAIN,
        )
    return ReadOnlyLLVMRepo(
        path=_GetToolchainUtilsCopyOfLLVMProject(),
        remote="origin",
        upstream_main=git_llvm_rev.MAIN_BRANCH,
    )


def GetUpToDateReadOnlyLLVMRepo() -> ReadOnlyLLVMRepo:
    """GetReadOnlyLLVMRepo, with an added `git fetch` step."""
    repo = GetReadOnlyLLVMRepo()
    logging.info("Updating LLVM repository at %s...", repo.path)
    subprocess.run(
        ["git", "fetch", "--quiet", repo.remote, repo.upstream_main],
        check=True,
        cwd=repo.path,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
    )
    return repo


@functools.lru_cache(1)
def GetCachedUpToDateReadOnlyLLVMRepo() -> ReadOnlyLLVMRepo:
    """GetUpToDateReadOnlyLLVMRepo, but will cache the result."""
    return GetUpToDateReadOnlyLLVMRepo()


def GetGoogle3LLVMVersion(stable: bool) -> int:
    """Gets the latest google3 LLVM version.

    Args:
        stable: boolean, use the stable version or the unstable version

    Returns:
        The latest LLVM SVN version as an integer.

    Raises:
        subprocess.CalledProcessError: An invalid path has been provided to the
        `cat` command.
    """
    subdir = "stable" if stable else "llvm_unstable"
    cmd = [
        "cat",
        os.path.join(
            "/google/src/head/depot/google3/third_party/crosstool/v18",
            subdir,
            "installs/llvm/git_origin_rev_id",
        ),
    ]
    git_hash = subprocess_helpers.check_output(cmd).rstrip()
    return GetCachedUpToDateReadOnlyLLVMRepo().GetRevisionFromHash(git_hash)


def IsSvnOption(svn_option: str) -> Union[int, str]:
    """Validates whether the argument (string) is a git hash option.

    The argument is used to find the git hash of LLVM.

    Args:
        svn_option: The option passed in as a command line argument.

    Returns:
        lowercase svn_option if it is a known hash source, otherwise the
        svn_option as an int

    Raises:
        ValueError: Invalid svn option provided.
    """

    if svn_option.lower() in KNOWN_HASH_SOURCES:
        return svn_option.lower()

    try:
        svn_version = int(svn_option)

        return svn_version

    # Unable to convert argument to an int, so the option is invalid.
    #
    # Ex: 'one'.
    except ValueError:
        pass

    raise ValueError("Invalid LLVM git hash option provided: %s" % svn_option)


def GetLLVMHashAndVersionFromSVNOption(
    svn_option: Union[int, str]
) -> Tuple[str, int]:
    """Gets the LLVM hash and LLVM version based off of the svn option.

    Args:
        svn_option: A valid svn option obtained from the command line.
          Ex. 'google3', 'tot', or <svn_version> such as 365123.

    Returns:
        A tuple that is the LLVM git hash and LLVM version.
    """

    new_llvm_hash = LLVMHash()
    llvm_repo = GetCachedUpToDateReadOnlyLLVMRepo()
    # Determine which LLVM git hash to retrieve.
    if svn_option == "tot":
        git_hash = new_llvm_hash.GetTopOfTrunkGitHash()
        version = llvm_repo.GetRevisionFromHash(git_hash)
    elif isinstance(svn_option, int):
        version = svn_option
        git_hash = llvm_repo.GetHashFromRevision(version)
    else:
        assert svn_option in ("google3", "google3-unstable")
        version = GetGoogle3LLVMVersion(stable=svn_option == "google3")
        git_hash = llvm_repo.GetHashFromRevision(version)

    return git_hash, version


def GetCrOSCurrentLLVMHash(chromeos_root: Path) -> str:
    """Retrieves the current ChromeOS LLVM hash.

    Specifically, this returns the _upstream_ hash that ChromeOS' LLVM is based
    on.

    Args:
        chromeos_root: A ChromeOS source tree root.

    Raises:
        AssertionError if `chromeos_root` isn't a CrOS tree root.
        ManifestValueError if the toolchain manifest doesn't match the
        expected structure.
    """
    assert chroot.IsChromeOSRoot(
        chromeos_root
    ), f"{chromeos_root} isn't the root of a ChromeOS checkout"
    llvm_project = chromeos_root / cros_paths.LLVM_PROJECT
    hash_or_ref = manifest_utils.extract_current_llvm_hash_or_ref(chromeos_root)
    refs_heads = "refs/heads/"
    # If this is a hash, we're done.
    if not hash_or_ref.startswith(refs_heads):
        return hash_or_ref

    # Otherwise, find the `merge-base` between upstream and what we have.
    ref = hash_or_ref[len(refs_heads) :]
    cros_ref = f"{cros_llvm_repo.UPSTREAM_REMOTE}/{ref}"
    llvm_repo = GetCachedUpToDateReadOnlyLLVMRepo()
    llvm_upstream_main = f"{llvm_repo.remote}/{llvm_repo.upstream_main}"
    merge_base = git_utils.merge_base(
        llvm_repo.path,
        [cros_ref, llvm_upstream_main],
    )
    if not merge_base:
        raise ValueError(
            f"Can't find a merge-base between {cros_ref} and {llvm_upstream_main}"
        )
    return merge_base


class LLVMHash:
    """Provides methods to retrieve a LLVM hash."""

    @staticmethod
    @contextlib.contextmanager
    def CreateTempDirectory() -> Iterator:
        temp_dir = tempfile.mkdtemp()

        try:
            yield temp_dir
        finally:
            if os.path.isdir(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)

    def CloneLLVMRepo(self, temp_dir: str) -> None:
        """Clones the LLVM repo.

        Args:
            temp_dir: The temporary directory to clone the repo to.

        Raises:
            ValueError: Failed to clone the LLVM repo.
        """
        clone_cmd = ["git", "clone", _LLVM_GIT_URL, temp_dir]
        clone_cmd_obj = subprocess.run(
            clone_cmd, check=False, stderr=subprocess.PIPE
        )
        if clone_cmd_obj.returncode:
            raise ValueError(
                "Failed to clone the LLVM repo; stderr: "
                f"{repr(clone_cmd_obj.stderr)}"
            )

    def GetLLVMHash(self, version: int) -> str:
        """Retrieves the LLVM hash corresponding to the LLVM version passed in.

        Args:
            version: The LLVM version to use as a delimiter.

        Returns:
            The hash as a string that corresponds to the LLVM version.
        """
        return GetCachedUpToDateReadOnlyLLVMRepo().GetHashFromRevision(version)

    def GetCrOSCurrentLLVMHash(self, chromeos_tree: Path) -> str:
        """Retrieves the current ChromeOS LLVM hash."""
        return GetCrOSCurrentLLVMHash(chromeos_tree)

    def GetCrOSLLVMNextHash(self) -> str:
        """Retrieves the current ChromeOS llvm-next hash.

        Specifically, this returns the _upstream_ hash that ChromeOS' LLVM-next
        is based on.
        """
        return llvm_next.LLVM_NEXT_HASH

    def GetGoogle3LLVMHash(self) -> str:
        """Retrieves the google3 LLVM hash."""
        return self.GetLLVMHash(GetGoogle3LLVMVersion(stable=True))

    def GetGoogle3UnstableLLVMHash(self) -> str:
        """Retrieves the LLVM hash of google3's unstable compiler."""
        return self.GetLLVMHash(GetGoogle3LLVMVersion(stable=False))

    def GetTopOfTrunkGitHash(self) -> str:
        """Gets the latest git hash from top of trunk of LLVM."""

        path_to_main_branch = "refs/heads/main"
        llvm_tot_git_hash = subprocess_helpers.check_output(
            ["git", "ls-remote", _LLVM_GIT_URL, path_to_main_branch]
        )
        return llvm_tot_git_hash.rstrip().split()[0]


def DetectLatestLLVMBranch(
    chromiumos_tree: Path,
    rev: int,
) -> Optional[str]:
    """Returns the latest llvm-next branch for `rev`.

    If no branches exist for `rev`, returns None.
    """
    llvm_project = chromiumos_tree / cros_paths.LLVM_PROJECT
    # Fetch ahead of time, so we always have the most up-to-date set of remote
    # refs possible.
    git_utils.fetch(llvm_project, remote=git_utils.CROS_EXTERNAL_REMOTE)
    branch_prefix = f"cros/chromeos/llvm-r{rev}-"
    # Note that `branches` has strings with leading prefixes (e.g., `remotes/`).
    # The code below is written to ignore those.
    branches = git_utils.branch_list(llvm_project, glob=f"{branch_prefix}*")
    llvm_branch_re = re.compile(re.escape(branch_prefix) + r"(\d+)$")
    most_recent_branch = None
    most_recent_branch_number = None
    for branch_path in branches:
        m = llvm_branch_re.search(branch_path)
        if not m:
            logging.warning(
                "Ignoring llvm branch %s, which doesn't match regex %s?",
                branch_path,
                llvm_branch_re,
            )
            continue

        branch = branch_path[m.start() : m.end()]
        branch_number = int(m.group(1))
        if (
            most_recent_branch_number is not None
            and branch_number < most_recent_branch_number
        ):
            continue

        most_recent_branch = branch
        most_recent_branch_number = branch_number
    return most_recent_branch


def main() -> None:
    """Prints the git hash of LLVM.

    Parses the command line for the optional command line
    arguments.
    """
    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.INFO,
    )

    # Create parser and add optional command-line arguments.
    parser = argparse.ArgumentParser(description="Finds the LLVM hash.")
    parser.add_argument(
        "--llvm_version",
        type=IsSvnOption,
        required=True,
        help="which git hash of LLVM to find. Either a svn revision, or one "
        "of %s" % sorted(KNOWN_HASH_SOURCES),
    )
    parser.add_argument(
        "--chromeos_tree",
        type=Path,
        help="""
        Path to a ChromeOS tree. If not passed, one will be inferred. If none
        can be inferred, this script will fail.
        """,
    )

    # Parse command-line arguments.
    args_output = parser.parse_args()

    cur_llvm_version = args_output.llvm_version
    chromeos_tree = args_output.chromeos_tree
    if not chromeos_tree:
        # Try to infer this unconditionally, so mishandling of this script can
        # be more easily detected (which allows more flexibility in the
        # implementation in the future for things outside of what directly
        # needs this value).
        chromeos_tree = chroot.FindChromeOSRootAboveToolchainUtils()

    new_llvm_hash = LLVMHash()
    if isinstance(cur_llvm_version, int):
        # Find the git hash of the specific LLVM version.
        print(new_llvm_hash.GetLLVMHash(cur_llvm_version))
    elif cur_llvm_version == "llvm":
        print(new_llvm_hash.GetCrOSCurrentLLVMHash(chromeos_tree))
    elif cur_llvm_version == "llvm-next":
        print(new_llvm_hash.GetCrOSLLVMNextHash())
    elif cur_llvm_version == "google3":
        print(new_llvm_hash.GetGoogle3LLVMHash())
    elif cur_llvm_version == "google3-unstable":
        print(new_llvm_hash.GetGoogle3UnstableLLVMHash())
    else:
        assert cur_llvm_version == "tot"
        print(new_llvm_hash.GetTopOfTrunkGitHash())
