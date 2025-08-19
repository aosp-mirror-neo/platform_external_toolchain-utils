# Copyright 2020 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for email_sender."""


import contextlib
import io
import json
from typing import Any
import unittest
from unittest import mock

from cros_utils import email_sender
from cros_utils import gs


class Test(unittest.TestCase):
    """Tests for email_sender."""

    def patch_gs_upload(
        self,
    ) -> tuple[mock.MagicMock, io.BytesIO]:
        patcher = mock.patch.object(gs, "streaming_upload_to")
        streaming_upload_mock = patcher.start()
        self.addCleanup(patcher.stop)

        retval = io.BytesIO()
        streaming_upload_mock.return_value = contextlib.nullcontext(retval)
        return streaming_upload_mock, retval

    def test_email_sending_rejects_invalid_inputs(self) -> None:
        write_file, _ = self.patch_gs_upload()

        test_cases: tuple[dict[str, Any], ...] = (
            {
                # no subject
                "subject": "",
                "identifier": "foo",
                "direct_recipients": ["gbiv@google.com"],
                "text_body": "hi",
            },
            {
                "subject": "foo",
                # no identifier
                "identifier": "",
                "direct_recipients": ["gbiv@google.com"],
                "text_body": "hi",
            },
            {
                "subject": "foo",
                "identifier": "foo",
                # no recipients
                "direct_recipients": [],
                "text_body": "hi",
            },
            {
                "subject": "foo",
                "identifier": "foo",
                "direct_recipients": ["gbiv@google.com"],
                # no body
            },
            {
                "subject": "foo",
                "identifier": "foo",
                # direct recipients lack @google.
                "direct_recipients": ["gbiv"],
                "text_body": "hi",
            },
            {
                "subject": "foo",
                "identifier": "foo",
                # non-list recipients
                "direct_recipients": "gbiv@google.com",
                "text_body": "hi",
            },
            {
                "subject": "foo",
                "identifier": "foo",
                # non-list recipients
                "well_known_recipients": "detective",
                "text_body": "hi",
            },
        )

        sender = email_sender.EmailSender()
        for case in test_cases:
            with self.assertRaises(ValueError):
                sender.SendGSEmail(**case)

        write_file.assert_not_called()

    def test_email_sending_translates_to_reasonable_json(self) -> None:
        write_file, written_data = self.patch_gs_upload()

        email_sender.EmailSender().SendGSEmail(
            subject="hello",
            identifier="world",
            well_known_recipients=["detective"],
            direct_recipients=["gbiv@google.com"],
            text_body="text",
            html_body="html",
        )

        write_file.assert_called_once()
        call_file_path: str = write_file.call_args[0][0]
        self.assertTrue(
            call_file_path.startswith(email_sender.GS_PATH + "/"),
            call_file_path,
        )

        written_obj = json.loads(written_data.getvalue())
        self.assertEqual(
            written_obj,
            {
                "subject": "hello",
                "email_identifier": "world",
                "well_known_recipients": ["detective"],
                "direct_recipients": ["gbiv@google.com"],
                "body": "text",
                "html_body": "html",
            },
        )
