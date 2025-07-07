# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for cros_image_tools"""

from pathlib import Path
import subprocess
import unittest
from unittest import mock

from cros_utils import cros_image_tools


# These are tests, so protected-access into cros_image_tools is OK.
# pylint: disable=protected-access

_EXAMPLE_FDISK_OUTPUT = """\
Disk /dev/loop1: 10.34 GiB, 11102403072 bytes, 21684381 sectors
Units: sectors of 1 * 512 = 512 bytes
Sector size (logical/physical): 512 bytes / 512 bytes
I/O size (minimum/optimal): 512 bytes / 512 bytes
Disklabel type: gpt
Disk identifier: 452D7BB8-D902-E746-BF46-2B59A1DCE197
First usable LBA: 34
Last usable LBA: 21684347
Alternative LBA: 21684380
Partition entries starting LBA: 2
Allocated partition entries: 128
Partition entries ending LBA: 33

Device          Start      End  Sectors Type-UUID UUID Name       Attrs
/dev/loop1p1  4907008 21684332 16777325 UUID UUID2 STATE
/dev/loop1p2    20480   151551   131072 UUID UUID2 KERN-A     GUID:48,49,50,51,52,53,54,55,56
/dev/loop1p3   712704  4907007  4194304 UUID UUID2 ROOT-A
/dev/loop1p4   151552   282623   131072 UUID UUID2 KERN-B
/dev/loop1p5   708608   712703     4096 UUID UUID2 ROOT-B
/dev/loop1p6    16448    16448        1 UUID UUID2 KERN-C
/dev/loop1p7    16449    16449        1 UUID UUID2 ROOT-C
/dev/loop1p8   282624   315391    32768 UUID UUID2 OEM
/dev/loop1p9    16450    16450        1 UUID UUID2 reserved
/dev/loop1p10   16451    16451        1 UUID UUID2 reserved
/dev/loop1p11      64    16447    16384 UUID UUID2 RWFW
/dev/loop1p12  446464   708607   262144 UUID UUID2 EFI-SYSTEM LegacyBIOSBootable

Partition table entries are not in disk order.
"""


class Test(unittest.TestCase):
    """Tests for cros_image_tools."""

    @mock.patch.object(subprocess, "run")
    def test_find_root_partition_identifies_the_right_partition(self, run_mock):
        run_mock_return = mock.Mock()
        run_mock_return.stdout = _EXAMPLE_FDISK_OUTPUT
        run_mock.return_value = run_mock_return

        self.assertEqual(
            cros_image_tools._find_root_partition(Path("/dev/loop1")),
            Path("/dev/loop1p3"),
        )
