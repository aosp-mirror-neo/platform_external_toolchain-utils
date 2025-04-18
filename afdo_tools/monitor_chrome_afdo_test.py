# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for monitor_chrome_afdo."""

import dataclasses
import datetime
from typing import List
import unittest

from afdo_tools import monitor_chrome_afdo


def arbitrary_time() -> datetime.datetime:
    """Returns an arbitrary datetime, in UTC."""
    return datetime.datetime(2020, 1, 2, 3, 4, 5, 6, datetime.timezone.utc)


def arbitrary_chrome_gs_profile_name() -> monitor_chrome_afdo.ChromeGsProfile:
    """Returns an arbitrary profile name >= MIN_PROFILE_MAJOR_VERSION."""
    major_cwp_version = monitor_chrome_afdo.MIN_PROFILE_MAJOR_VERSION
    major_bench_version = major_cwp_version + 1
    return (
        f"chromeos-chrome-arm-none-{major_cwp_version}-6440.4-1716810247-"
        f"benchmark-{major_bench_version}.1.6533.2-r3-redacted.afdo.xz"
    )


def arbitrary_chrome_gs_profile() -> monitor_chrome_afdo.ChromeGsProfile:
    """Returns an arbitrary ChromeGsProfile."""
    full_name = arbitrary_chrome_gs_profile_name()
    x = monitor_chrome_afdo.ChromeGsProfile.from_full_name_if_new_enough(
        last_modified=arbitrary_time(),
        full_name=full_name,
    )
    assert x is not None, f"Profile name was too old? Name: {full_name}"
    return x


def increasing_chrome_gs_profile_sequence(
    count: int,
) -> List[monitor_chrome_afdo.ChromeGsProfile]:
    """Returns an iterable of successive Chrome GS profiles.

    They're all successive in that:
    1. Their `last_modified` are yielded in increasing order.
    2. Their benchmark versions are also yielded in increasing order.

    Otherwise, all attributes (profile arch, subtype, etc) will remain
    consistent across all profiles.

    Returns:
        A list of `count` profiles.
    """
    baseline = arbitrary_chrome_gs_profile()
    return [
        dataclasses.replace(
            baseline,
            last_modified=baseline.last_modified
            + datetime.timedelta(seconds=x),
            cwp_timestamp=baseline.cwp_timestamp + x,
        )
        for x in range(count)
    ]


class Test(unittest.TestCase):
    """Tests for monitor_chrome_afdo."""

    def test_all_profile_arch_parsing(self):
        for arch in monitor_chrome_afdo.ProfileArch:
            self.assertEqual(
                arch, monitor_chrome_afdo.ProfileArch.parse(arch.value)
            )
        with self.assertRaises(ValueError):
            monitor_chrome_afdo.ProfileArch.parse("not-a-profile-arch")

    def test_all_profile_subtype_parsing(self):
        for subtype in monitor_chrome_afdo.ProfileSubtype:
            self.assertEqual(
                subtype, monitor_chrome_afdo.ProfileSubtype.parse(subtype.value)
            )
        with self.assertRaises(ValueError):
            monitor_chrome_afdo.ProfileSubtype.parse("not-a-profile-subtype")

    def test_gs_profile_parsing(self):
        last_modified = arbitrary_time()
        profile_name = arbitrary_chrome_gs_profile_name()
        self.assertEqual(
            monitor_chrome_afdo.ChromeGsProfile.from_full_name_if_new_enough(
                last_modified=last_modified,
                full_name=profile_name,
            ),
            monitor_chrome_afdo.ChromeGsProfile(
                last_modified=last_modified,
                arch=monitor_chrome_afdo.ProfileArch.ARM,
                subtype=monitor_chrome_afdo.ProfileSubtype.NONE,
                benchmark_part_version=monitor_chrome_afdo.ChromeVersion(
                    major=monitor_chrome_afdo.MIN_PROFILE_MAJOR_VERSION + 1,
                    minor=1,
                    build=6533,
                    patch=2,
                    revision=3,
                ),
                cwp_part_version=monitor_chrome_afdo.ChromeVersion(
                    major=monitor_chrome_afdo.MIN_PROFILE_MAJOR_VERSION,
                    minor=0,
                    build=6440,
                    patch=4,
                    revision=0,
                ),
                cwp_timestamp=1716810247,
            ),
        )

    def test_gs_profile_name_round_trips(self):
        last_modified = arbitrary_time()
        profile_name = arbitrary_chrome_gs_profile_name()
        self.assertEqual(
            monitor_chrome_afdo.ChromeGsProfile.from_full_name_if_new_enough(
                last_modified=last_modified, full_name=profile_name
            ).full_name(),
            profile_name,
        )

    def test_gs_profile_parsing_on_old_profile(self):
        major_bench_version = monitor_chrome_afdo.MIN_PROFILE_MAJOR_VERSION - 1

        last_modified = arbitrary_time()
        profile_name = (
            f"chromeos-chrome-nonsense_arch-nonsense_type-0-6440.4-1716810247-"
            f"benchmark-{major_bench_version}.1.6533.2-r3-redacted.afdo.xz"
        )
        self.assertIsNone(
            monitor_chrome_afdo.ChromeGsProfile.from_full_name_if_new_enough(
                last_modified=last_modified,
                full_name=profile_name,
            )
        )

    def test_finding_newest_chrome_version_on_no_stable_ebuilds(self):
        with self.assertRaisesRegex(ValueError, "^No stable Chrome ebuilds.*"):
            monitor_chrome_afdo.find_newest_chrome_version(
                [
                    "MANIFEST",
                    "files/",
                    "chromeos-chrome-9999.ebuild",
                ]
            )

    def test_finding_newest_chrome_version_multiple_ebuilds(self):
        self.assertEqual(
            monitor_chrome_afdo.find_newest_chrome_version(
                [
                    "MANIFEST",
                    "files/",
                    "chromeos-chrome-9999.ebuild",
                    "chromeos-chrome-127.0.6533.0_rc-r1.ebuild",
                    "chromeos-chrome-126.0.6014.0_rc-r2.ebuild",
                ]
            ),
            "127.0.6533.0",
        )

    def test_afdo_version_finding_works(self):
        profile1, profile2, profile3 = increasing_chrome_gs_profile_sequence(3)
        profiles = {
            1: [profile1, profile2],
            2: [profile3],
        }

        with self.assertRaisesRegex(ValueError, "^No available profile.*"):
            monitor_chrome_afdo.find_afdo_profile_by_version(
                profiles, "not a profile version"
            )

        self.assertIs(
            monitor_chrome_afdo.find_afdo_profile_by_version(
                profiles, profile2.full_name()
            ),
            profile2,
        )

    def test_branch_profile_finding_works_in_simple_cases(self):
        profile1, profile2, profile3 = increasing_chrome_gs_profile_sequence(3)
        arch = monitor_chrome_afdo.ProfileArch.AMD64
        subtype = monitor_chrome_afdo.ProfileSubtype.BIGCORE
        # Assert this since a having `(arch, subtype)` as the arch/subtype of
        # _all_ profiles will cause nonsense results.
        self.assertNotEqual((arch, subtype), (profile2.arch, profile2.subtype))

        profile2 = dataclasses.replace(profile2, arch=arch, subtype=subtype)

        profiles = {
            1: [profile1],
            2: [profile2, profile3],
        }
        result = monitor_chrome_afdo.find_most_recent_branch_profile(
            afdo_profiles=profiles,
            arch=arch,
            subtype=subtype,
            branch_number=2,
        )
        self.assertIs(result, profile2)

        with self.assertRaisesRegex(ValueError, "^Found no branch profiles.*"):
            monitor_chrome_afdo.find_most_recent_branch_profile(
                afdo_profiles=profiles,
                arch=arch,
                subtype=subtype,
                branch_number=1,
            )

    def test_branch_profile_finding_falls_back_to_prior_branches(self):
        profile1, profile2, profile3 = increasing_chrome_gs_profile_sequence(3)
        arch = monitor_chrome_afdo.ProfileArch.AMD64
        subtype = monitor_chrome_afdo.ProfileSubtype.BIGCORE
        self.assertNotEqual((arch, subtype), (profile1.arch, profile1.subtype))

        profile1 = dataclasses.replace(profile2, arch=arch, subtype=subtype)
        profiles = {
            1: [profile1],
            2: [profile2],
            3: [profile3],
        }
        result = monitor_chrome_afdo.find_most_recent_branch_profile(
            afdo_profiles=profiles,
            arch=arch,
            subtype=subtype,
            branch_number=3,
        )
        self.assertIs(result, profile1)
