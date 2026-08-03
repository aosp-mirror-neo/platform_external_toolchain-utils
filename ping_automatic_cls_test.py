# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for ping_automatic_cls."""

import datetime
import unittest

import ping_automatic_cls


class Test(unittest.TestCase):
    """Tests for ping_automatic_cls."""

    def test_get_week_start_sunday(self) -> None:
        test_cases = (
            (datetime.date(2026, 8, 2), datetime.date(2026, 8, 2)),
            (datetime.date(2026, 8, 3), datetime.date(2026, 8, 2)),
            (datetime.date(2026, 8, 4), datetime.date(2026, 8, 2)),
            (datetime.date(2026, 8, 8), datetime.date(2026, 8, 2)),
            (datetime.date(2026, 8, 9), datetime.date(2026, 8, 9)),
            (datetime.date(2026, 1, 1), datetime.date(2025, 12, 28)),
        )
        for date_in, expected in test_cases:
            with self.subTest(d=date_in):
                self.assertEqual(
                    ping_automatic_cls.get_week_start_sunday(date_in), expected
                )
