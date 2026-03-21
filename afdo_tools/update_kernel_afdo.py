# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""This script updates kernel profiles based on what's available in gs://.

It supports updating on canary, stable, and beta branches.
"""

import argparse
import dataclasses
import datetime
import enum
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Iterable

from cros_utils import cros_paths
from cros_utils import git_utils
from cros_utils import gs


# Folks who should be on the CC-line of any CLs that get uploaded.
CL_CC = ("gbiv@chromium.org",)

# Determine which gsutil to use.
# 'gsutil.py' is provided by depot_tools, whereas 'gsutil'
# is provided by either https://cloud.google.com/sdk/docs/install, or
# the 'google-cloud-cli' package. Since we need depot_tools to even
# use 'repo', 'gsutil.py' is guaranteed to exist.
GSUTIL = "gsutil.py"


class Arch(enum.Enum):
    """An enum for CPU architectures."""

    AMD64 = "amd64"
    ARM = "arm"

    @property
    def cwp_gs_location(self) -> str:
        """Returns the location in gs:// where these profiles live."""
        if self == self.AMD64:
            return "gs://chromeos-prebuilt/afdo-job/vetted/kernel/amd64"
        if self == self.ARM:
            return "gs://chromeos-prebuilt/afdo-job/vetted/kernel/arm"
        assert False, f"Uncovered arch -> gs:// mapping for {self}"


@dataclasses.dataclass(frozen=True, eq=True, order=True)
class KernelVersion:
    """A class representing a version of the kernel."""

    major: int
    minor: int

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}"

    @classmethod
    def parse(cls, val: str) -> "KernelVersion":
        m = re.fullmatch(r"(\d+).(\d+)", val)
        if not m:
            raise ValueError(f"{val!r} is an invalid kernel version")
        return cls(major=int(m.group(1)), minor=int(m.group(2)))


ARM_KERNEL_5_15 = (Arch.ARM, KernelVersion(5, 15))

# Versions that rolling should be skipped on, for one reason or another.
SKIPPED_VERSIONS: dict[int, Iterable[tuple[Arch, KernelVersion]]] = {
    # Kernel tracing was disabled on ARM in 114, b/275560674
    114: (ARM_KERNEL_5_15,),
    115: (ARM_KERNEL_5_15,),
    # Kernel profiles are no longer generated as of M126. Don't complain about
    # them.
    124: (ARM_KERNEL_5_15,),
    125: (ARM_KERNEL_5_15,),
    126: (ARM_KERNEL_5_15,),
}


@dataclasses.dataclass(frozen=True)
class ProfileSelectionInfo:
    """Preferences about profiles to select."""

    # A consistent timestamp for the program to run with.
    now: datetime.datetime

    # Maximum age of a profile that can be selected.
    max_profile_age: datetime.timedelta


def get_parser() -> argparse.ArgumentParser:
    """Returns an argument parser for this script."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging.",
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Automatically upload all changes that were made.",
    )
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="Run `git fetch` in toolchain-utils prior to running.",
    )
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=10,
        help="""
        The maximum number of days old a kernel profile can be before
        it's ignored by this script. Default: %(default)s
        """,
    )
    parser.add_argument(
        "--chromeos-tree",
        type=Path,
        help="""
        Root of a ChromeOS tree. This is optional to pass in, but doing so
        unlocks extra convenience features on `--upload`. This script will try
        to autodetect a tree if this isn't specified.
        """,
    )
    parser.add_argument(
        "channel",
        nargs="*",
        type=git_utils.Channel.parse,
        default=list(git_utils.Channel),
        help=f"""
        Channel(s) to update. If none are passed, this will update all
        channels. Choose from {[x.value for x in git_utils.Channel]}.
        """,
    )
    return parser


def git_checkout(git_dir: Path, branch: git_utils.ChannelBranch) -> None:
    subprocess.run(
        [
            "git",
            "checkout",
            "--quiet",
            f"{branch.remote}/{branch.branch_name}",
        ],
        check=True,
        cwd=git_dir,
        stdin=subprocess.DEVNULL,
    )


def git_fetch(git_dir: Path) -> None:
    subprocess.run(
        ["git", "fetch"],
        check=True,
        cwd=git_dir,
        stdin=subprocess.DEVNULL,
    )


@dataclasses.dataclass(frozen=True, eq=True, order=True)
class ArchUpdateConfig:
    """The AFDO update config for one architecture."""

    versions_to_track: list[KernelVersion]
    metadata_file: Path


def read_update_cfg_file(
    toolchain_utils: Path, file_path: Path
) -> dict[Arch, ArchUpdateConfig]:
    """Reads `update_kernel_afdo.cfg`."""
    # These files were originally meant to be `source`d in bash, and are very
    # simple. These are read from branches, so we'd need cherry-picks to go
    # back and replace them with a singular format. Could be nice to move to
    # JSON or something.

    # Parse assignments that look like `FOO="bar"`. No escaping or variable
    # expansion is supported.
    kv_re = re.compile(r'^([a-zA-Z_0-9]+)="([^"]*)"(?:\s*#.*)?', re.MULTILINE)
    kvs = kv_re.findall(file_path.read_text(encoding="utf-8"))
    # Subtle: the regex above makes it so `kv_re.findall` returns a series of
    # (variable_name, variable_value).
    settings = dict(kvs)

    logging.debug("Parsing cfg file gave back settings: %s", settings)
    archs = (
        (Arch.AMD64, "AMD"),
        (Arch.ARM, "ARM"),
    )

    results = {}
    for arch, arch_var_name in archs:
        # This is a space-separated list of kernel versions.
        kernel_versions = settings[f"{arch_var_name}_KVERS"]
        parsed_versions = [
            KernelVersion.parse(x) for x in kernel_versions.split()
        ]

        metadata_file = settings[f"{arch_var_name}_METADATA_FILE"]
        results[arch] = ArchUpdateConfig(
            versions_to_track=parsed_versions,
            metadata_file=toolchain_utils / metadata_file,
        )
    return results


@dataclasses.dataclass(frozen=True, eq=True)
class KernelGsProfile:
    """Represents a kernel profile in gs://."""

    release_number: int
    chrome_build: str
    cwp_timestamp: int
    suffix: str
    gs_timestamp: datetime.datetime

    _FILE_NAME_PARSE_RE = re.compile(r"R(\d+)-(\d+\.\d+)-(\d+)(\..+\..+)")

    @property
    def file_name_no_suffix(self) -> str:
        return (
            f"R{self.release_number}-{self.chrome_build}-{self.cwp_timestamp}"
        )

    @property
    def file_name(self) -> str:
        return f"{self.file_name_no_suffix}{self.suffix}"

    @classmethod
    def from_file_name(
        cls, timestamp: datetime.datetime, file_name: str
    ) -> "KernelGsProfile":
        m = cls._FILE_NAME_PARSE_RE.fullmatch(file_name)
        if not m:
            raise ValueError(f"{file_name!r} doesn't parse as a profile name")
        release_number, chrome_build, cwp_timestamp, suffix = m.groups()
        return cls(
            release_number=int(release_number),
            chrome_build=chrome_build,
            cwp_timestamp=int(cwp_timestamp),
            suffix=suffix,
            gs_timestamp=timestamp,
        )


class KernelProfileFetcher:
    """Fetches kernel profiles from gs://. Caches results."""

    def __init__(self) -> None:
        self._cached_results: dict[str, list[KernelGsProfile]] = {}

    @classmethod
    def _fetch_impl(cls, gs_url: str) -> list[KernelGsProfile]:
        results = []
        for gs_entry in gs.ls(gs_url):
            profile_name = os.path.basename(gs_entry.gs_path)
            # All directories end with `/`, so  their basenames are empty.
            if not profile_name:
                continue
            assert gs_entry.last_modified is not None, (
                "Non-directory unexpectedly has a None last-modified date: "
                f"{gs_entry}"
            )
            results.append(
                KernelGsProfile.from_file_name(
                    gs_entry.last_modified, profile_name
                )
            )
        return results

    def fetch(self, gs_url: str) -> list[KernelGsProfile]:
        cached = self._cached_results.get(gs_url)
        if cached is None:
            logging.info("Fetching profiles from %s...", gs_url)
            fetched = self._fetch_impl(gs_url)
            logging.info("Found %d profiles in %s", len(fetched), gs_url)
            self._cached_results[gs_url] = fetched
            cached = fetched

        # Create a copy to keep mutations from causing problems.
        # KernelGsProfiles are frozen, at least.
        return cached[:]


def find_newest_afdo_artifact(
    fetcher: KernelProfileFetcher,
    arch: Arch,
    kernel_version: KernelVersion,
    release_number: int,
) -> KernelGsProfile | None:
    """Returns info about the latest AFDO artifact for the given parameters."""
    gs_base_location = arch.cwp_gs_location
    kernel_profile_dir = os.path.join(gs_base_location, str(kernel_version))
    kernel_profiles = fetcher.fetch(kernel_profile_dir)
    if not kernel_profiles:
        logging.error(
            "Failed to find any kernel profiles in %s", kernel_profile_dir
        )
        return None

    valid_profiles = [
        x for x in kernel_profiles if x.release_number == release_number
    ]
    if not valid_profiles:
        logging.warning(
            "Failed to find any M%d kernel profiles in %s",
            release_number,
            kernel_profile_dir,
        )
        return None

    # We want the most recently uploaded profile, since that should correspond
    # with the newest profile. If there're multiple profiles for some reason,
    # choose what _should_ be a consistent tie-breaker.
    return max(
        valid_profiles,
        key=lambda x: (x.gs_timestamp, x.cwp_timestamp, x.chrome_build),
    )


def read_afdo_descriptor_file(path: Path) -> dict[KernelVersion, str]:
    """Reads the AFDO descriptor file.

    "AFDO descriptor file" is jargon to refer to the actual JSON file that PUpr
    monitors.
    """
    try:
        with path.open(encoding="utf-8") as f:
            raw_contents = json.load(f)
    except FileNotFoundError:
        return {}

    # The format of this is:
    # {
    #   "chromeos-kernel-${major}_${minor}": {
    #     "name": "${profile_gs_name}",
    #   }
    # }
    key_re = re.compile(r"^chromeos-kernel-(\d)+_(\d+)$")
    result = {}
    for kernel_key, val in raw_contents.items():
        m = key_re.fullmatch(kernel_key)
        if not m:
            raise ValueError(f"Invalid key in JSON: {kernel_key}")
        major, minor = m.groups()
        version = KernelVersion(major=int(major), minor=int(minor))
        result[version] = val["name"]
    return result


def write_afdo_descriptor_file(
    path: Path, contents: dict[KernelVersion, str]
) -> bool:
    """Writes the file at path with the given contents.

    Returns:
        True if the file was written due to changes, False otherwise.
    """
    contents_dict = {
        f"chromeos-kernel-{k.major}_{k.minor}": {"name": gs_name}
        for k, gs_name in contents.items()
    }

    contents_json = json.dumps(contents_dict, indent=4, sort_keys=True)
    try:
        existing_contents = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        existing_contents = ""

    # Compare the _textual representation_ of each of these, since things like
    # formatting changes should be propagated eagerly.
    if contents_json == existing_contents:
        return False

    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(contents_json, encoding="utf-8")
    tmp_path.rename(path)
    return True


@dataclasses.dataclass
class UpdateResult:
    """Result of `update_afdo_for_channel`."""

    # True if changes were made to the AFDO files that map kernel versions to
    # AFDO profiles.
    made_changes: bool

    # Whether issues were had updating one or more profiles. If this is True,
    # you may expect that there will be logs about the issues already.
    had_failures: bool


def fetch_and_validate_newest_afdo_artifact(
    fetcher: KernelProfileFetcher,
    selection_info: ProfileSelectionInfo,
    arch: Arch,
    kernel_version: KernelVersion,
    branch: git_utils.ChannelBranch,
    channel: git_utils.Channel,
) -> tuple[str, bool] | None:
    """Tries to update one AFDO profile on a branch.

    Returns:
        None if something failed, and the update couldn't be completed.
        Otherwise, this returns a tuple of (profile_name, is_old). If `is_old`
        is True, this function logs an error.
    """
    newest_artifact = find_newest_afdo_artifact(
        fetcher, arch, kernel_version, branch.release_number
    )
    # Try an older branch if we're not on stable. We should fail harder if we
    # only have old profiles on stable, though.
    if newest_artifact is None and channel != git_utils.Channel.STABLE:
        newest_artifact = find_newest_afdo_artifact(
            fetcher, arch, kernel_version, branch.release_number - 1
        )

    if newest_artifact is None:
        logging.error(
            "No new profile found for %s/%s on M%d; not updating entry",
            arch,
            kernel_version,
            branch.release_number,
        )
        return None

    logging.info(
        "Newest profile is %s for %s/%s on M%d",
        newest_artifact.file_name,
        arch,
        kernel_version,
        branch.release_number,
    )
    age = selection_info.now - newest_artifact.gs_timestamp
    is_old = False
    if age > selection_info.max_profile_age:
        is_old = True
        logging.error(
            "Profile %s is %s old. The configured limit is %s.",
            newest_artifact.file_name,
            age,
            selection_info.max_profile_age,
        )
    return newest_artifact.file_name_no_suffix, is_old


def remove_untracked_mappings(
    descriptors: dict[KernelVersion, str],
    versions_to_track: Iterable[KernelVersion],
) -> dict[KernelVersion, str]:
    version_set = set(versions_to_track)
    return {k: v for k, v in descriptors.items() if k in version_set}


def update_afdo_for_channel(
    fetcher: KernelProfileFetcher,
    toolchain_utils: Path,
    selection_info: ProfileSelectionInfo,
    channel: git_utils.Channel,
    branch: git_utils.ChannelBranch,
    skipped_versions: dict[int, Iterable[tuple[Arch, KernelVersion]]],
) -> UpdateResult:
    """Updates AFDO on the given channel."""
    git_checkout(toolchain_utils, branch)
    update_cfgs = read_update_cfg_file(
        toolchain_utils,
        toolchain_utils / "afdo_tools" / "update_kernel_afdo.cfg",
    )

    to_skip = skipped_versions.get(branch.release_number)
    made_changes = False
    had_failures = False
    for arch, cfg in update_cfgs.items():
        afdo_mappings = remove_untracked_mappings(
            read_afdo_descriptor_file(cfg.metadata_file),
            cfg.versions_to_track,
        )
        for kernel_version in cfg.versions_to_track:
            if to_skip and (arch, kernel_version) in to_skip:
                logging.info(
                    "%s/%s on M%d is in the skip list; ignoring it.",
                    arch,
                    kernel_version,
                    branch.release_number,
                )
                continue

            artifact_info = fetch_and_validate_newest_afdo_artifact(
                fetcher,
                selection_info,
                arch,
                kernel_version,
                branch,
                channel,
            )
            if artifact_info is None:
                # Assume that the problem was already logged.
                had_failures = True
                continue

            newest_name, is_old = artifact_info
            if is_old:
                # Assume that the problem was already logged, but continue to
                # land this in case it makes a difference.
                had_failures = True

            afdo_mappings[kernel_version] = newest_name

        if write_afdo_descriptor_file(cfg.metadata_file, afdo_mappings):
            made_changes = True
            logging.info(
                "Wrote new AFDO mappings for arch %s on M%d",
                arch,
                branch.release_number,
            )
        else:
            logging.info(
                "No changes to write for arch %s on M%d",
                arch,
                branch.release_number,
            )
    return UpdateResult(
        made_changes=made_changes,
        had_failures=had_failures,
    )


def commit_new_profiles(
    toolchain_utils: Path, channel: git_utils.Channel, had_failures: bool
) -> None:
    """Runs `git commit -a` with an appropriate message."""
    commit_message_lines = [
        "afdo_metadata: Publish the new kernel profiles",
        "",
    ]

    if had_failures:
        commit_message_lines += (
            "This brings some profiles to their newest versions. The CrOS",
            "toolchain detective has been notified about the failures that",
            "occurred in this update.",
        )
    else:
        commit_message_lines.append(
            "This brings all profiles to their newest versions."
        )

    if channel != git_utils.Channel.CANARY:
        commit_message_lines += (
            "",
            "Have PM pre-approval because this shouldn't break the release",
            "branch.",
        )

    commit_message_lines += (
        "",
        "Never rebase this CL! If there's a merge conflict, either abandon",
        "this CL, or let Chrotomation do so after a few days. Rebasing could",
        "cause a performance regression.",
        "",
        "BUG=None",
        "TEST=Verified in kernel-release-afdo-verify-orchestrator",
    )

    commit_msg = "\n".join(commit_message_lines)
    subprocess.run(
        [
            "git",
            "commit",
            "--quiet",
            "-a",
            "-m",
            commit_msg,
        ],
        cwd=toolchain_utils,
        check=True,
        stdin=subprocess.DEVNULL,
    )


def upload_head_to_gerrit(
    toolchain_utils: Path,
    chromeos_tree: Path | None,
    branch: git_utils.ChannelBranch,
) -> None:
    """Uploads HEAD to gerrit as a CL, and sets reviewers/CCs."""
    cl_ids = git_utils.upload_to_gerrit(
        toolchain_utils,
        branch.remote,
        branch.branch_name,
        cc=CL_CC,
    )

    if len(cl_ids) > 1:
        raise ValueError(f"Unexpected: wanted just one CL upload; got {cl_ids}")

    cl_id = cl_ids[0]
    logging.info("Uploaded CL http://crrev.com/c/%s successfully.", cl_id)

    if chromeos_tree is None:
        logging.info(
            "Skipping gerrit convenience commands, since no CrOS tree was "
            "specified."
        )
        return

    git_utils.set_autoreview_topic_and_labels(chromeos_tree, cl_id)


def main(argv: list[str]) -> None:
    toolchain_utils = cros_paths.script_toolchain_utils_root()

    opts = get_parser().parse_args(argv)
    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.DEBUG if opts.debug else logging.INFO,
    )

    chromeos_tree = opts.chromeos_tree
    if not chromeos_tree:
        chromeos_tree = cros_paths.script_chromiumos_checkout()
        if chromeos_tree:
            logging.info("Autodetected ChromeOS tree root at %s", chromeos_tree)

    if opts.fetch:
        logging.info("Fetching in %s...", toolchain_utils)
        git_fetch(toolchain_utils)

    selection_info = ProfileSelectionInfo(
        now=datetime.datetime.now(datetime.timezone.utc),
        max_profile_age=datetime.timedelta(days=opts.max_age_days),
    )

    branches = git_utils.autodetect_cros_channels(toolchain_utils)
    logging.debug("Current branches: %s", branches)

    assert all(
        x in branches for x in git_utils.Channel
    ), "branches are missing channels?"

    fetcher = KernelProfileFetcher()
    had_failures = False
    with git_utils.create_worktree(toolchain_utils) as worktree:
        for channel in opts.channel:
            branch = branches[channel]
            result = update_afdo_for_channel(
                fetcher,
                worktree,
                selection_info,
                channel,
                branch,
                SKIPPED_VERSIONS,
            )
            had_failures = had_failures or result.had_failures
            if not result.made_changes:
                logging.info("No new updates to post on %s", channel)
                continue

            commit_new_profiles(worktree, channel, result.had_failures)
            if opts.upload:
                logging.info("New profiles were committed. Uploading...")
                upload_head_to_gerrit(worktree, chromeos_tree, branch)
            else:
                logging.info(
                    "--upload not specified. Leaving commit for %s at %s",
                    channel,
                    git_utils.resolve_ref(worktree, "HEAD"),
                )

    if had_failures:
        sys.exit(
            "At least one failure was encountered running this script; see "
            "above logs. Most likely the things you're looking for are logged "
            "at the ERROR level."
        )
