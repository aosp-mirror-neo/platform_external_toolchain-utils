# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for update_kernel_afdo."""

import datetime
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from unittest import mock

from afdo_tools import update_kernel_afdo


class Test(unittest.TestCase):
    """Tests for update_kernel_afdo."""

    def make_tempdir(self) -> Path:
        x = Path(tempfile.mkdtemp(prefix="update_kernel_afdo_test_"))
        self.addCleanup(shutil.rmtree, x)
        return x

    def test_kernel_version_parsing(self):
        self.assertEqual(
            update_kernel_afdo.KernelVersion.parse("5.10"),
            update_kernel_afdo.KernelVersion(major=5, minor=10),
        )

        with self.assertRaisesRegex(ValueError, ".*invalid kernel version.*"):
            update_kernel_afdo.KernelVersion.parse("5")

    def test_kernel_version_formatting(self):
        self.assertEqual(
            str(update_kernel_afdo.KernelVersion(major=5, minor=10)), "5.10"
        )

    def test_read_update_cfg_file(self):
        valid_contents = textwrap.dedent(
            """
            # some comment
            # wow
            AMD_KVERS="1.0 1.1"
            ARM_KVERS="1.2"
            AMD_METADATA_FILE="amd/file/path.json" # comment
            ARM_METADATA_FILE="arm/file/path.json"
            """
        )
        tmpdir = self.make_tempdir()
        cfg_path = tmpdir / "test.cfg"
        cfg_path.write_text(valid_contents, encoding="utf-8")
        cfg = update_kernel_afdo.read_update_cfg_file(tmpdir, cfg_path)
        expected_amd64 = update_kernel_afdo.ArchUpdateConfig(
            versions_to_track=[
                update_kernel_afdo.KernelVersion(1, 0),
                update_kernel_afdo.KernelVersion(1, 1),
            ],
            metadata_file=tmpdir / "amd/file/path.json",
        )
        expected_arm = update_kernel_afdo.ArchUpdateConfig(
            versions_to_track=[
                update_kernel_afdo.KernelVersion(1, 2),
            ],
            metadata_file=tmpdir / "arm/file/path.json",
        )

        self.assertEqual(
            cfg,
            {
                update_kernel_afdo.Arch.AMD64: expected_amd64,
                update_kernel_afdo.Arch.ARM: expected_arm,
            },
        )

    def test_parse_kernel_gs_profile(self):
        timestamp = datetime.datetime.fromtimestamp(1234, datetime.timezone.utc)
        profile = update_kernel_afdo.KernelGsProfile.from_file_name(
            timestamp,
            "R124-15808.0-1710149961.afdo.xz",
        )
        self.assertEqual(
            profile,
            update_kernel_afdo.KernelGsProfile(
                release_number=124,
                chrome_build="15808.0",
                cwp_timestamp=1710149961,
                suffix=".afdo.xz",
                gs_timestamp=timestamp,
            ),
        )

    def test_kernel_gs_profile_file_name(self):
        timestamp = datetime.datetime.fromtimestamp(1234, datetime.timezone.utc)
        profile = update_kernel_afdo.KernelGsProfile.from_file_name(
            timestamp,
            "R124-15808.0-1710149961.afdo.xz",
        )
        self.assertEqual(profile.file_name_no_suffix, "R124-15808.0-1710149961")
        self.assertEqual(profile.file_name, "R124-15808.0-1710149961.afdo.xz")

    def test_untracked_version_removal(self):
        kernel_5_10 = update_kernel_afdo.KernelVersion(5, 10)
        kernel_5_15 = update_kernel_afdo.KernelVersion(5, 15)
        kernel_6_1 = update_kernel_afdo.KernelVersion(6, 1)

        self.assertEqual(
            update_kernel_afdo.remove_untracked_mappings(
                descriptors={kernel_5_10: "foo", kernel_5_15: "bar"},
                versions_to_track=(kernel_5_15, kernel_6_1),
            ),
            {kernel_5_15: "bar"},
        )

    @mock.patch.object(subprocess, "run")
    def test_kernel_profile_fetcher_caches_urls(self, subprocess_run):
        subprocess_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            # Don't use textwrap.dedent; linter complains about the line being
            # too long in that case.
            stdout="""
753112  2024-03-04T10:38:50Z gs://here/5.4/R124-15786.10-1709548729.afdo.xz
TOTAL: 2 objects, 1234 bytes (1.1KiB)
""",
        )

        fetcher = update_kernel_afdo.KernelProfileFetcher()
        # Fetch these twice, and assert both that:
        # - Only one fetch is performed.
        # - Mutating the first list won't impact the later fetch.
        result = fetcher.fetch("gs://here/5.4")
        self.assertEqual(len(result), 1)
        del result[:]
        result = fetcher.fetch("gs://here/5.4")
        self.assertEqual(len(result), 1)
        subprocess_run.assert_called_once()

    @mock.patch.object(update_kernel_afdo.KernelProfileFetcher, "fetch")
    def test_newest_afdo_artifact_finding_works(self, fetch):
        late = update_kernel_afdo.KernelGsProfile.from_file_name(
            datetime.datetime.fromtimestamp(1236, datetime.timezone.utc),
            "R124-15786.10-1709548729.afdo.xz",
        )
        early = update_kernel_afdo.KernelGsProfile.from_file_name(
            datetime.datetime.fromtimestamp(1234, datetime.timezone.utc),
            "R124-99999.99-9999999999.afdo.xz",
        )
        fetch.return_value = [early, late]

        self.assertEqual(
            update_kernel_afdo.find_newest_afdo_artifact(
                update_kernel_afdo.KernelProfileFetcher(),
                update_kernel_afdo.Arch.AMD64,
                update_kernel_afdo.KernelVersion(5, 4),
                release_number=124,
            ),
            late,
        )

    def test_afdo_descriptor_file_round_trips(self):
        tmpdir = self.make_tempdir()
        file_path = tmpdir / "desc-file.json"

        contents = {
            update_kernel_afdo.KernelVersion(5, 10): "file1",
            update_kernel_afdo.KernelVersion(5, 15): "file2",
        }
        self.assertTrue(
            update_kernel_afdo.write_afdo_descriptor_file(file_path, contents)
        )
        self.assertEqual(
            update_kernel_afdo.read_afdo_descriptor_file(file_path),
            contents,
        )

    def test_afdo_descriptor_file_refuses_to_rewrite_identical_contents(self):
        tmpdir = self.make_tempdir()
        file_path = tmpdir / "desc-file.json"

        contents = {
            update_kernel_afdo.KernelVersion(5, 10): "file1",
            update_kernel_afdo.KernelVersion(5, 15): "file2",
        }
        self.assertTrue(
            update_kernel_afdo.write_afdo_descriptor_file(file_path, contents)
        )
        self.assertFalse(
            update_kernel_afdo.write_afdo_descriptor_file(file_path, contents)
        )
