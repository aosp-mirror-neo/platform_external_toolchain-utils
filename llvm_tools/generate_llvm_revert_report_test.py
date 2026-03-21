# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Unit tests for generate_llvm_revert_report."""

import io
import unittest

from llvm_tools import generate_llvm_revert_report


class TestRevert(unittest.TestCase):
    """Unit tests for generate_llvm_revert_report.Revert"""

    def test_from_dict_extra_fields(self) -> None:
        data = {
            "createdOn": 1762960808,
            "status": "MERGED",
            "subject": "Test",
            "url": "https://the_url/",
        }
        parsed = generate_llvm_revert_report.Revert.from_dict(data)
        self.assertEqual(
            parsed,
            generate_llvm_revert_report.Revert(
                status="MERGED",
                subject="Test",
                url="https://the_url/",
            ),
        )

    def test_from_dict_missing_fields(self) -> None:
        data = {
            "status": "MERGED",
            "url": "https://the_url/",
        }
        with self.assertRaises(KeyError):
            generate_llvm_revert_report.Revert.from_dict(data)


class TestWriteReverts(unittest.TestCase):
    """Tests that writing the reverts produces the expected output."""

    def test_no_reverts(self) -> None:
        output = io.StringIO()
        generate_llvm_revert_report.write_reverts_as_csv(output, [])
        self.assertEqual(
            output.getvalue(),
            '"Status","URI","Subject","Notes"\r\n',
        )

    def test_with_reverts(self) -> None:
        output = io.StringIO()
        reverts = [
            generate_llvm_revert_report.Revert(
                url="https://1/",
                subject="One",
                status="MERGED",
            ),
            generate_llvm_revert_report.Revert(
                url="https://2/3",
                subject="Another",
                status="ABANDONED",
            ),
        ]
        generate_llvm_revert_report.write_reverts_as_csv(output, reverts)
        self.assertEqual(
            output.getvalue(),
            (
                '"Status","URI","Subject","Notes"\r\n'
                '"MERGED","https://1/","One"\r\n'
                '"ABANDONED","https://2/3","Another"\r\n'
            ),
        )
