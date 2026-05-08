# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for cros_cls."""

import datetime
import unittest
from unittest import mock

from llvm_tools import cros_cls


class TestChangeListURL(unittest.TestCase):
    """ChangeListURL tests."""

    def test_parsing_long_form_url(self) -> None:
        self.assertEqual(
            cros_cls.ChangeListURL.parse(
                "chromium-review.googlesource.com/c/chromiumos/overlays/"
                "chromiumos-overlay/+/123456",
            ),
            cros_cls.ChangeListURL(cl_id=123456, patch_set=None),
        )

    def test_parsing_long_form_internal_url(self) -> None:
        self.assertEqual(
            cros_cls.ChangeListURL.parse(
                "chrome-internal-review.googlesource.com/c/chromeos/"
                "manifest-internal/+/654321"
            ),
            cros_cls.ChangeListURL(cl_id=654321, patch_set=None, internal=True),
        )

    def test_parsing_long_form_git_corp_url(self) -> None:
        self.assertEqual(
            cros_cls.ChangeListURL.parse(
                "chromium-review.git.corp.google.com/c/chromiumos/overlays/"
                "chromiumos-overlay/+/123456",
            ),
            cros_cls.ChangeListURL(cl_id=123456, patch_set=None),
        )

    def test_parsing_long_form_git_corp_internal_url(self) -> None:
        self.assertEqual(
            cros_cls.ChangeListURL.parse(
                "chrome-internal-review.git.corp.google.com/c/chromeos/"
                "manifest-internal/+/654321"
            ),
            cros_cls.ChangeListURL(cl_id=654321, patch_set=None, internal=True),
        )

    def test_parsing_short_internal_url(self) -> None:
        self.assertEqual(
            cros_cls.ChangeListURL.parse("crrev.com/i/654321"),
            cros_cls.ChangeListURL(cl_id=654321, patch_set=None, internal=True),
        )

    def test_parsing_discards_http(self) -> None:
        self.assertEqual(
            cros_cls.ChangeListURL.parse("http://crrev.com/c/123456"),
            cros_cls.ChangeListURL(cl_id=123456, patch_set=None),
        )

    def test_parsing_discards_https(self) -> None:
        self.assertEqual(
            cros_cls.ChangeListURL.parse("https://crrev.com/c/123456"),
            cros_cls.ChangeListURL(cl_id=123456, patch_set=None),
        )

    def test_parsing_detects_patch_sets(self) -> None:
        self.assertEqual(
            cros_cls.ChangeListURL.parse("crrev.com/c/123456/14"),
            cros_cls.ChangeListURL(cl_id=123456, patch_set=14),
        )

    def test_parsing_is_okay_with_trailing_slash(self) -> None:
        self.assertEqual(
            cros_cls.ChangeListURL.parse("crrev.com/c/123456/"),
            cros_cls.ChangeListURL(cl_id=123456, patch_set=None),
        )
        self.assertEqual(
            cros_cls.ChangeListURL.parse("crrev.com/c/123456/14/"),
            cros_cls.ChangeListURL(cl_id=123456, patch_set=14),
        )

    def test_parsing_is_okay_with_valid_trailing_junk(self) -> None:
        self.assertEqual(
            cros_cls.ChangeListURL.parse("crrev.com/c/123456?foo=bar"),
            cros_cls.ChangeListURL(cl_id=123456, patch_set=None),
        )
        self.assertEqual(
            cros_cls.ChangeListURL.parse("crrev.com/c/123456/?foo=bar"),
            cros_cls.ChangeListURL(cl_id=123456, patch_set=None),
        )
        self.assertEqual(
            cros_cls.ChangeListURL.parse("crrev.com/c/123456/14/foo=bar"),
            cros_cls.ChangeListURL(cl_id=123456, patch_set=14),
        )
        self.assertEqual(
            cros_cls.ChangeListURL.parse("crrev.com/c/123456/14?foo=bar"),
            cros_cls.ChangeListURL(cl_id=123456, patch_set=14),
        )

        # While these aren't well-formed, Gerrit handles them without issue.
        self.assertEqual(
            cros_cls.ChangeListURL.parse("crrev.com/c/123456&foo=bar"),
            cros_cls.ChangeListURL(cl_id=123456, patch_set=None),
        )
        self.assertEqual(
            cros_cls.ChangeListURL.parse("crrev.com/c/123456/14&foo=bar"),
            cros_cls.ChangeListURL(cl_id=123456, patch_set=14),
        )

    def test_parsing_raises_on_invalid_trailing_jumk(self) -> None:
        with self.assertRaises(ValueError):
            cros_cls.ChangeListURL.parse("crrev.com/c/123456foo=bar")

        with self.assertRaises(ValueError):
            cros_cls.ChangeListURL.parse("crrev.com/c/123456/14foo=bar")

    def test_parsing_hash_c_url(self) -> None:
        self.assertEqual(
            cros_cls.ChangeListURL.parse(
                "https://chrome-internal-review.googlesource.com/#/c/9088380/"
            ),
            cros_cls.ChangeListURL(cl_id=9088380, internal=True),
        )
        self.assertEqual(
            cros_cls.ChangeListURL.parse(
                "https://chromium-review.git.corp.google.com/#/c/7832690/"
            ),
            cros_cls.ChangeListURL(cl_id=7832690, internal=False),
        )

    def test_str_functions_properly(self) -> None:
        self.assertEqual(
            str(
                cros_cls.ChangeListURL(
                    cl_id=1234,
                    patch_set=2,
                )
            ),
            "https://crrev.com/c/1234/2",
        )

        self.assertEqual(
            str(
                cros_cls.ChangeListURL(
                    cl_id=1234,
                    patch_set=None,
                )
            ),
            "https://crrev.com/c/1234",
        )

        self.assertEqual(
            str(
                cros_cls.ChangeListURL(
                    cl_id=1234,
                    patch_set=2,
                    internal=True,
                )
            ),
            "https://crrev.com/i/1234/2",
        )


class Test(unittest.TestCase):
    """General tests for cros_cls."""

    def test_release_builder_parsing_works(self) -> None:
        self.assertEqual(
            cros_cls.parse_release_from_builder_artifacts_link(
                "gs://chromeos-image-archive/amd64-generic-asan-cq/"
                "R122-15711.0.0-59730-8761718482083052481"
            ),
            "R122-15711.0.0",
        )
        self.assertEqual(
            cros_cls.parse_release_from_builder_artifacts_link(
                "gs://chromeos-image-archive/amd64-generic-asan-cq/"
                "R122-15711.0.0-59730-8761718482083052481/some/trailing/"
                "stuff.zip"
            ),
            "R122-15711.0.0",
        )

    def test_parse_build_status(self) -> None:
        self.assertEqual(
            cros_cls.BuilderStatus.parse("SCHEDULED"),
            cros_cls.BuilderStatus.SCHEDULED,
        )
        self.assertEqual(
            cros_cls.BuilderStatus.parse("started"),
            cros_cls.BuilderStatus.STARTED,
        )
        with self.assertRaisesRegex(
            ValueError, "Unknown builder status: UNKNOWN"
        ):
            cros_cls.BuilderStatus.parse("UNKNOWN")


class TestBuildIDParsing(unittest.TestCase):
    """BuildID parsing tests."""

    def test_parse_build_id_from_bb_add_output(self) -> None:
        output = (
            "http://ci.chromium.org/b/8698399525438704705 "
            "SCHEDULED 'chromeos/cq/brya-bazel-lite-cq'"
        )
        self.assertEqual(
            cros_cls.parse_build_id_from_bb_add_output(output),
            cros_cls.BuildID("8698399525438704705"),
        )

    def test_parse_new_build_id_from_bb_add_output(self) -> None:
        output = (
            "http://cr-buildbucket.appspot.com/build/8698399525438704706 "
            "SCHEDULED 'chromeos/cq/brya-bazel-lite-cq'"
        )
        self.assertEqual(
            cros_cls.parse_build_id_from_bb_add_output(output),
            cros_cls.BuildID("8698399525438704706"),
        )

    def test_parse_build_id_from_bb_add_output_multiple_ids(self) -> None:
        output = (
            "http://ci.chromium.org/b/123 SCHEDULED 'bot'\n"
            "http://ci.chromium.org/b/456 SCHEDULED 'another_bot'"
        )
        with self.assertRaisesRegex(
            ValueError, r"Expected one build-id from stdout"
        ):
            cros_cls.parse_build_id_from_bb_add_output(output)


class TestBbLsInfo(unittest.TestCase):
    """BbLsInfo tests."""

    def test_from_dict(self) -> None:
        build_id = "8681949416952307121"
        create_time_str = "2026-05-13T04:00:46.922407325Z"
        d = {
            "id": build_id,
            "status": "FAILURE",
            "createTime": create_time_str,
            "builder": {"builder": "staging-build-chromiumos-sdk"},
        }
        info = cros_cls.BbLsInfo.from_dict(d)
        expected = cros_cls.BbLsInfo(
            build_id=int(build_id),
            status=cros_cls.BuilderStatus.FAILURE,
            create_time=datetime.datetime.fromisoformat(create_time_str),
            builder_name="staging-build-chromiumos-sdk",
        )
        self.assertEqual(info, expected)

    @mock.patch.object(cros_cls, "_run_bb_decoding_output")
    def test_fetch_bb_ls_info(
        self, mock_run_bb_decoding_output: mock.MagicMock
    ) -> None:
        t1_str = "2026-05-13T04:00:00Z"
        t2_str = "2026-05-13T04:00:01Z"
        mock_run_bb_decoding_output.return_value = [
            {
                "id": "1",
                "status": "SUCCESS",
                "createTime": t1_str,
                "builder": {"builder": "b1"},
            },
            {
                "id": "2",
                "status": "FAILURE",
                "createTime": t2_str,
                "builder": {"builder": "b2"},
            },
        ]
        results = cros_cls.fetch_bb_ls_info(ls_args=["args"])
        expected = [
            cros_cls.BbLsInfo(
                build_id=1,
                status=cros_cls.BuilderStatus.SUCCESS,
                create_time=datetime.datetime.fromisoformat(t1_str),
                builder_name="b1",
            ),
            cros_cls.BbLsInfo(
                build_id=2,
                status=cros_cls.BuilderStatus.FAILURE,
                create_time=datetime.datetime.fromisoformat(t2_str),
                builder_name="b2",
            ),
        ]
        self.assertEqual(results, expected)
        mock_run_bb_decoding_output.assert_called_once_with(
            ["ls", "args"], multiline=True
        )

    def test_parse_build_id_from_bb_add_output_no_id(self) -> None:
        output = "No build ID here"
        with self.assertRaisesRegex(
            ValueError, r"Expected one build-id from stdout"
        ):
            cros_cls.parse_build_id_from_bb_add_output(output)
