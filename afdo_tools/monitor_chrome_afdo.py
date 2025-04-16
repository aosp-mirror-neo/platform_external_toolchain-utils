# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Complains if Chrome's AFDO profiles are too old on a branch."""

import argparse
import collections
import dataclasses
import datetime
import enum
import logging
import os
from pathlib import Path
import re
import sys
import textwrap
from typing import Dict, Iterable, List, Optional, Tuple

from cros_utils import bugs
from cros_utils import cros_paths
from cros_utils import git_utils
from cros_utils import gs


# Profiles with benchmark versions less than this won't be fully parsed.
# There're legacy profile formats/subtypes/arches/etc that we should no longer
# care about.
MIN_PROFILE_MAJOR_VERSION = 120

# For synthesized cronjob logs, how many hours should pass without any reports
# before the job is considered 'turned down'.
# Default to around a week, since branches come and go 1x/mo.
CRONJOB_TURNDOWN_TIME_HOURS = 7 * 24

# Complaint is used below to make function signatures clearer. Semantically
# each Complaint is a list of paragraphs that should be printed together as a
# single diagnostic. Lines may be reflowed.
Complaint = List[str]

# Iterable of milestones to skip monitoring for. Once a milestone leaves
# stable, it can be removed from this.
SKIP_MILESTONES = (
    # b/384142128#comment33
    131,
)


class ProfileArch(enum.Enum):
    """The arch of a Chrome AFDO profile."""

    ARM = "arm"
    AMD64 = "amd64"

    @classmethod
    def parse(cls, arch: str) -> "ProfileArch":
        for x in cls:
            if x.value == arch:
                return x
        raise ValueError(f"No corresponding ProfileArch for {arch!r}")


class ProfileSubtype(enum.Enum):
    """The subtype of a Chrome AFDO profile."""

    ATOM = "atom"
    ARM32 = "arm32"
    BIGCORE = "bigcore"
    EXP = "exp"
    NONE = "none"

    @classmethod
    def parse(cls, subtype: str) -> "ProfileArch":
        for x in cls:
            if x.value == subtype:
                return x
        raise ValueError(f"No corresponding ProfileSubtype for {subtype!r}")


# All of the (arch, subtype) combos we care to monitor at the moment, and where
# they exist under a ${chromium}/src checkout.
#
# Note that `exp` and `arm32` profiles are ignored, as they aren't used in
# production.
CHROME_STAMP_FILE_LOCATIONS: Dict[Tuple[ProfileArch, ProfileSubtype], str] = {
    (
        ProfileArch.ARM,
        ProfileSubtype.NONE,
    ): "chromeos/profiles/arm.afdo.newest.txt",
    (
        ProfileArch.AMD64,
        ProfileSubtype.ATOM,
    ): "chromeos/profiles/atom.afdo.newest.txt",
    (
        ProfileArch.AMD64,
        ProfileSubtype.BIGCORE,
    ): "chromeos/profiles/bigcore.afdo.newest.txt",
}

# N.B., This is expected to return a subset of CHROME_STAMP_FILE_LOCATIONS. That
# said, iterating over CHROME_STAMP_FILE_LOCATIONS.keys() can be confusing
# in places, so have a thin wrapper.
def monitored_profile_configs() -> Iterable[Tuple[ProfileArch, ProfileSubtype]]:
    """Returns an iterable of all currently-monitored profile configurations."""
    return CHROME_STAMP_FILE_LOCATIONS.keys()


@dataclasses.dataclass(frozen=True, order=True, eq=True)
class ChromeVersion:
    """Chrome version."""

    major: int
    minor: int
    build: int
    patch: int
    revision: int


@dataclasses.dataclass(frozen=True, eq=True)
class ChromeGsProfile:
    """Represents a Chrome profile in gs://."""

    # When the profile was last modified.
    last_modified: datetime.datetime
    # Arch of the profile.
    arch: ProfileArch
    # Subtype of the profile, e.g., atom, bigcore
    subtype: ProfileSubtype
    # The version of Chrome used to generate the benchmark part of this
    # profile.
    benchmark_part_version: ChromeVersion
    # The version of Chrome CWP profiles are sourced from.
    cwp_part_version: ChromeVersion
    # The timestamp at which the CWP profile was generated.
    cwp_timestamp: int

    _FULL_NAME_RE = re.compile(
        r"^"
        r"chromeos-chrome-"
        r"(?P<arch>[^-]+)-"
        r"(?P<subtype>[^-]+)-"
        r"(?P<cwp_major>\d+)-"
        r"(?P<cwp_build>\d+)\."
        r"(?P<cwp_patch>\d+)-"
        r"(?P<cwp_timestamp>\d+)-"
        r"benchmark-"
        r"(?P<bench_major>\d+)\."
        r"(?P<bench_minor>\d+)\."
        r"(?P<bench_build>\d+)\."
        r"(?P<bench_patch>\d+)-"
        r"r(?P<bench_revision>\d+)"
        r"-redacted\.afdo\.xz"
        r"$"
    )

    def full_name(self) -> str:
        return (
            "chromeos-chrome-"
            f"{self.arch.value}-"
            f"{self.subtype.value}-"
            f"{self.cwp_part_version.major}-"
            f"{self.cwp_part_version.build}."
            f"{self.cwp_part_version.patch}-"
            f"{self.cwp_timestamp}-"
            "benchmark-"
            f"{self.benchmark_part_version.major}."
            f"{self.benchmark_part_version.minor}."
            f"{self.benchmark_part_version.build}."
            f"{self.benchmark_part_version.patch}-"
            f"r{self.benchmark_part_version.revision}"
            "-redacted.afdo.xz"
        )

    @classmethod
    def from_full_name_if_new_enough(
        cls, last_modified: datetime.datetime, full_name: str
    ) -> Optional["ChromeGsProfile"]:
        m = cls._FULL_NAME_RE.match(full_name)
        if not m:
            raise ValueError(f"{full_name!r} is not parseable as a profile")
        groups = m.groupdict()

        bench_major = int(groups["bench_major"])
        if bench_major < MIN_PROFILE_MAJOR_VERSION:
            return None

        arch = ProfileArch.parse(groups["arch"])
        raw_subtype = groups["subtype"]
        subtype = ProfileSubtype.parse(raw_subtype)

        cwp_part_version = ChromeVersion(
            major=int(groups["cwp_major"]),
            minor=0,
            build=int(groups["cwp_build"]),
            patch=int(groups["cwp_patch"]),
            revision=0,
        )
        benchmark_part_version = ChromeVersion(
            major=int(groups["bench_major"]),
            minor=int(groups["bench_minor"]),
            build=int(groups["bench_build"]),
            patch=int(groups["bench_patch"]),
            revision=int(groups["bench_revision"]),
        )
        return cls(
            last_modified=last_modified,
            arch=arch,
            subtype=subtype,
            benchmark_part_version=benchmark_part_version,
            cwp_part_version=cwp_part_version,
            cwp_timestamp=int(groups["cwp_timestamp"]),
        )


def fetch_release_afdo_profiles() -> Dict[int, List[ChromeGsProfile]]:
    """Fetches release Chrome AFDO profiles, grouped by major version.

    The major version used is specifically the benchmark part. List ordering is
    unspecified.
    """
    results = collections.defaultdict(list)
    for gs_entry in gs.ls("gs://chromeos-prebuilt/afdo-job/vetted/release"):
        profile_name = os.path.basename(gs_entry.gs_path)
        # All directories end with `/`, so  their basenames are empty.
        if not profile_name:
            continue
        assert gs_entry.last_modified is not None, (
            "Non-directory unexpectedly has a None last-modified date: "
            f"{gs_entry}"
        )
        profile = ChromeGsProfile.from_full_name_if_new_enough(
            gs_entry.last_modified, profile_name
        )
        if profile:
            results[profile.benchmark_part_version.major].append(profile)
    return results


def find_most_recent_branch_profile(
    afdo_profiles: Dict[int, List[ChromeGsProfile]],
    arch: ProfileArch,
    subtype: ProfileSubtype,
    branch_number: int,
) -> ChromeGsProfile:
    """Returns the most recent profile for a given branch number.

    Falls back to prior branches if none could be found for the given branch.

    Raises:
        ValueError if no compatible profiles could be found.
    """
    logging.debug(
        "Finding most recent profile for M%d, arch=%s, subtype=%s",
        branch_number,
        arch,
        subtype,
    )
    for i in range(branch_number, 0, -1):
        branch_profiles = afdo_profiles.get(i)
        if not branch_profiles:
            logging.debug("No profiles found for branch M%d", i)
            continue

        matching_profiles = (
            x
            for x in branch_profiles
            if x.arch is arch and x.subtype is subtype
        )
        result = max(
            matching_profiles, key=lambda x: x.last_modified, default=None
        )
        if result:
            return result
        logging.debug("No _matching_ profiles found for branch M%d", i)

    raise ValueError(
        f"Found no branch profiles for {arch}-{subtype} starting "
        f"at M{branch_number}"
    )


def check_cwp_profiles_are_new(
    branches: List[Tuple[git_utils.Channel, git_utils.ChannelBranch]],
    afdo_profiles: Dict[int, List[ChromeGsProfile]],
    now: datetime.datetime,
    max_profile_age: datetime.timedelta,
) -> Dict[int, List[Complaint]]:
    """Checks to see if the CWP profile parts for the given channel look good.

    Returns:
        Complaints about profiles, per-milestone.
    """
    complaints = {}
    for channel, branch in branches:
        if branch.release_number in SKIP_MILESTONES:
            logging.warning(
                "Release M%d is marked as skipped; ignoring CWP profiles.",
                branch.release_number,
            )
            continue

        logging.info(
            "Monitoring CWP profiles for M%s (%s)",
            branch.release_number,
            channel,
        )
        branch_complaints = []

        for arch, subtype in monitored_profile_configs():
            most_recent_profile = find_most_recent_branch_profile(
                afdo_profiles=afdo_profiles,
                arch=arch,
                subtype=subtype,
                branch_number=branch.release_number,
            )
            time_since_modification = now - most_recent_profile.last_modified
            logging.info(
                "Most recent profile for %s-%s is %s, which is %s old.",
                arch,
                subtype,
                most_recent_profile.full_name(),
                time_since_modification,
            )
            if time_since_modification < max_profile_age:
                continue

            branch_complaints.append(
                [
                    textwrap.dedent(
                        f"""\
                        CWP profile on M{branch.release_number} for
                        {arch}-{subtype} is too old. Its age is
                        {time_since_modification}, but the limit is
                        {max_profile_age}.
                        """
                    ),
                    textwrap.dedent(
                        """\
                        You might want to reach out to CWP and ensure profile
                        generation is going smoothly.
                        """
                    ),
                ]
            )

        if branch_complaints:
            complaints[branch.release_number] = branch_complaints
    return complaints


def find_newest_chrome_version(chromeos_chrome_files: List[str]) -> str:
    """Returns the newest Chrome version from the given ebuilds.

    Returns:
        The Chrome version, as a string that can be used in a Chromium
        repository.
    """
    chrome_re = re.compile(
        r"^chromeos-chrome-((\d+)\.(\d+)\.(\d+)\.(\d+))_rc-r(\d+).ebuild$"
    )
    candidates = []
    for f in chromeos_chrome_files:
        m = chrome_re.fullmatch(f)
        if not m:
            continue

        full_version, major, minor, build, patch, revision = m.groups()
        ver = ChromeVersion(
            major=int(major),
            minor=int(minor),
            build=int(build),
            patch=int(patch),
            revision=int(revision),
        )
        candidates.append((ver, full_version))

    if not candidates:
        raise ValueError(
            f"No stable Chrome ebuilds found in {chromeos_chrome_files}"
        )
    _, result = max(candidates)
    return result


def find_afdo_profile_by_version(
    afdo_profiles: Dict[int, List[ChromeGsProfile]],
    stamp_contents: str,
) -> ChromeGsProfile:
    for profile_listing in afdo_profiles.values():
        for profile in profile_listing:
            if profile.full_name() == stamp_contents:
                return profile
    raise ValueError(
        f"No available profile is associated with stamp {stamp_contents}"
    )


def maybe_diagnose_current_chrome_afdo_profile(
    *,
    channel: git_utils.Channel,
    branch: git_utils.ChannelBranch,
    arch: ProfileArch,
    subtype: ProfileSubtype,
    now: datetime.datetime,
    afdo_profiles: Dict[int, List[ChromeGsProfile]],
    current_profile_stamp: str,
    max_profile_age: datetime.timedelta,
) -> Optional[Complaint]:
    """Potentially complains about the age of the given profile.

    Returns:
        A complaint to issue about the given profile, if any.
    """
    current_profile = find_afdo_profile_by_version(
        afdo_profiles, current_profile_stamp
    )

    age = now - current_profile.last_modified
    logging.info(
        "Profile on M%s (%s) for %s-%s is %s, which is %s old.",
        branch.release_number,
        channel,
        arch,
        subtype,
        current_profile.full_name(),
        age,
    )
    if age < max_profile_age:
        return None

    logging.error(
        "Profile is too old; maximum allowable age is %s.", max_profile_age
    )

    complaint = [
        textwrap.dedent(
            f"""\
            AFDO profile on M{branch.release_number} for {arch}-{subtype} is
            too old. Its age is {age}, and the limit is {max_profile_age}.
            """
        ),
    ]

    # Opportunistically search to see if something newer could've landed, and
    # log it if so.
    most_recent_uploaded_profile = find_most_recent_branch_profile(
        afdo_profiles, arch, subtype, branch.release_number
    )
    if most_recent_uploaded_profile == current_profile:
        complaint.append(
            textwrap.dedent(
                """\
                No newer verified profile of this type exists. Maybe the
                benchmark generation pipeline is having issues?
                """
            ),
        )
    else:
        new_age = now - most_recent_uploaded_profile.last_modified
        complaint += (
            textwrap.dedent(
                f"""\
                NOTE: A newer profile of this type, which is {new_age} old,
                exists in gs://. It's
                {most_recent_uploaded_profile.full_name()}.
                """
            ),
            textwrap.dedent(
                """\
                Since a newer profile exists but has not yet been rolled, Skia
                autorollers may be malfunctioning.
                """
            ),
        )

    return complaint


def check_afdo_profiles_are_new(
    *,
    chrome_src: Path,
    chromiumos_overlay: Path,
    branches: List[Tuple[git_utils.Channel, git_utils.ChannelBranch]],
    afdo_profiles: Dict[int, List[ChromeGsProfile]],
    now: datetime.datetime,
    max_profile_age: datetime.timedelta,
) -> Dict[int, List[Complaint]]:
    """Checks to see if the AFDO profiles for the given channel look good.

    Returns:
        Complaints about profiles, per-milestone.
    """
    complaints = {}
    for channel, branch in branches:
        if branch.release_number in SKIP_MILESTONES:
            logging.warning(
                "Release M%d is marked as skipped; ignoring AFDO profiles.",
                branch.release_number,
            )
            continue

        logging.info(
            "Monitoring landed AFDO profiles for M%s (%s)",
            branch.release_number,
            channel,
        )
        chromeos_chrome_contents = git_utils.maybe_list_dir_contents_at_commit(
            git_dir=chromiumos_overlay,
            ref=f"{branch.remote}/{branch.branch_name}",
            path_from_git_root="chromeos-base/chromeos-chrome",
        )
        if chromeos_chrome_contents is None:
            raise ValueError(
                f"No chromeos-base/chromeos-chrome directory at "
                f"{branch.branch_name}"
            )

        newest_chrome_version = find_newest_chrome_version(
            chromeos_chrome_contents
        )
        logging.info(
            "Newest Chrome version on %s is %s", channel, newest_chrome_version
        )

        branch_complaints = []
        for arch, subtype in monitored_profile_configs():
            stamp_file = CHROME_STAMP_FILE_LOCATIONS[(arch, subtype)]
            stamp_contents = git_utils.maybe_show_file_at_commit(
                git_dir=chrome_src,
                ref=newest_chrome_version,
                path_from_git_root=stamp_file,
            )
            if not stamp_contents:
                raise ValueError(
                    f"No version file found at {stamp_file} in Chromium at "
                    f"{newest_chrome_version}"
                )

            maybe_complaint = maybe_diagnose_current_chrome_afdo_profile(
                channel=channel,
                branch=branch,
                arch=arch,
                subtype=subtype,
                now=now,
                afdo_profiles=afdo_profiles,
                current_profile_stamp=stamp_contents.strip(),
                max_profile_age=max_profile_age,
            )
            if maybe_complaint:
                branch_complaints.append(maybe_complaint)

        if branch_complaints:
            complaints[branch.release_number] = branch_complaints

    return complaints


def merge_milestone_complaints(
    a: Dict[int, List[Complaint]], b: Dict[int, List[Complaint]]
) -> Dict[int, List[Complaint]]:
    """Merges two per-milestone Complaints dicts into one."""
    return {k: sorted(a.get(k, []) + b.get(k, [])) for k in a.keys() | b.keys()}


def format_complaint(complaint: Complaint, width: int) -> str:
    """Formats a complaint for printing. May return multiple lines."""
    result_paragraphs = (textwrap.fill(x, width) for x in complaint)
    return "\n\n".join(result_paragraphs)


def format_complaints(
    milestone: int, complaints: List[Complaint], width: int
) -> str:
    lines = [f"Complaint(s) for M{milestone}:"]
    for complaint in sorted(complaints):
        # Set width to 70 because 80cols is standard, and we're adding
        # indentation below.
        formatted = format_complaint(complaint, width=width)
        indented = formatted.replace("\n", "\n\t  ")
        lines.append(f"\t- {indented}")
    return "\n".join(lines)


def upload_cronjob_reports(
    branches: List[Tuple[git_utils.Channel, git_utils.ChannelBranch]],
    milestone_complaints: Dict[int, List[Complaint]],
) -> None:
    """Uploads synthesized cronjob reports outlining this script's findings."""
    for channel, branch in branches:
        logging.info(
            "Uploading cronjob report for M%d (%s)...",
            branch.release_number,
            channel,
        )
        complaints = milestone_complaints.get(branch.release_number)
        if complaints:
            failed = True
            message = format_complaints(
                branch.release_number,
                complaints,
                width=70,
            )
        else:
            failed = False
            message = "All profiles are sufficiently fresh."

        bugs.SendCronjobLog(
            cronjob_name=f"Chrome AFDO Monitor, M{branch.release_number}",
            failed=failed,
            message=message,
            turndown_time_hours=CRONJOB_TURNDOWN_TIME_HOURS,
        )


def main(argv: List[str]) -> None:
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
        "--chrome-tree",
        type=Path,
        required=True,
        help="Path to a Chrome tree to inspect.",
    )
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="Run `git fetch` in relevant directories prior to running.",
    )
    parser.add_argument(
        "--max-profile-age-days",
        type=int,
        default=10,
        help="""
        The maximum number of days old the newest Chrome profile can be before
        this script starts erroring about it. Default: %(default)s
        """,
    )
    parser.add_argument(
        "--max-cwp-age-days",
        type=int,
        default=10,
        help="""
        The maximum number of days old the newest CWP profile can be before
        this script starts erroring about it. Default: %(default)s
        """,
    )
    parser.add_argument(
        "--upload-cronjob-reports",
        action="store_true",
        help="""
        If specified, this script will upload per-channel cronjob reports
        (for synthesized, per-milestone cronjobs, e.g., `M125 Chrome AFDO
        monitor`) rather than reporting its results through stdout and exit
        codes. If this flag is passed, the exit code of this script will be 0
        even if there are old AFDO profiles detected.
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
    opts = parser.parse_args(argv)

    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.DEBUG if opts.debug else logging.INFO,
    )

    chromeos_tree = cros_paths.script_chromiumos_checkout_or_exit()
    chromiumos_overlay = chromeos_tree / cros_paths.CHROMIUMOS_OVERLAY
    chrome_src = opts.chrome_tree / "src"

    if opts.fetch:
        logging.info("Fetching chromiumos-overlay...")
        git_utils.fetch(chromiumos_overlay)
        logging.info("Fetching chromium...")
        git_utils.fetch(chrome_src)

    channel_branches = git_utils.autodetect_cros_channels(
        git_repo=chromiumos_overlay
    )

    logging.info("Fetching listing of released AFDO profiles...")
    afdo_profiles = fetch_release_afdo_profiles()
    logging.info(
        "%d profiles fetched", sum(len(x) for x in afdo_profiles.values())
    )

    now = datetime.datetime.now(datetime.timezone.utc)
    branch_tuples = [(x, channel_branches[x]) for x in opts.channel]
    cwp_complaints = check_cwp_profiles_are_new(
        branch_tuples,
        afdo_profiles,
        now,
        max_profile_age=datetime.timedelta(days=opts.max_cwp_age_days),
    )
    afdo_complaints = check_afdo_profiles_are_new(
        chrome_src=chrome_src,
        chromiumos_overlay=chromiumos_overlay,
        branches=branch_tuples,
        afdo_profiles=afdo_profiles,
        now=now,
        max_profile_age=datetime.timedelta(days=opts.max_profile_age_days),
    )

    milestone_complaints = merge_milestone_complaints(
        cwp_complaints, afdo_complaints
    )

    if opts.upload_cronjob_reports:
        upload_cronjob_reports(branch_tuples, milestone_complaints)
        logging.info("All cronjob reports published; my job here is done.")
        return

    if not milestone_complaints:
        logging.info("All checks passed.")
        return

    for i, (milestone, complaints) in enumerate(
        sorted(milestone_complaints.items())
    ):
        if i:
            print()
        print(format_complaints(milestone, complaints, width=70))

    logging.error("Issues were found; exiting with an error.")
    sys.exit(0 if opts.upload_cronjob_reports else 1)
