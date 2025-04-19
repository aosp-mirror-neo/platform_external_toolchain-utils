# Copyright 2020 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for nightly_revert_checker."""

import textwrap
import time
import unittest
from unittest import mock

from cros_utils import tiny_render
from llvm_tools import nightly_revert_checker
from llvm_tools import revert_checker


# pylint: disable=protected-access


class Test(unittest.TestCase):
    """Tests for nightly_revert_checker."""

    def test_email_rendering_works_for_singular_revert(self):
        def prettify_sha(sha: str) -> tiny_render.Piece:
            return "pretty_" + sha

        def get_sha_description(sha: str) -> tiny_render.Piece:
            return "subject_" + sha

        email = nightly_revert_checker._generate_revert_email(
            repository_name="${repo}",
            friendly_name="${name}",
            sha="${sha}",
            prettify_sha=prettify_sha,
            get_sha_description=get_sha_description,
            new_reverts=[
                revert_checker.Revert(
                    sha="${revert_sha}", reverted_sha="${reverted_sha}"
                )
            ],
        )

        expected_email = nightly_revert_checker._Email(
            subject="[revert-checker/${repo}] new revert discovered across "
            "${name}",
            body=[
                "It looks like there may be a new revert across ${name} (",
                "pretty_${sha}",
                ").",
                tiny_render.line_break,
                tiny_render.line_break,
                "That is:",
                tiny_render.UnorderedList(
                    [
                        [
                            "pretty_${revert_sha}",
                            " (appears to revert ",
                            "pretty_${reverted_sha}",
                            "): ",
                            "subject_${revert_sha}",
                        ]
                    ]
                ),
                tiny_render.line_break,
                "PTAL and consider reverting them locally.",
            ],
        )

        self.assertEqual(email, expected_email)

    def test_email_rendering_works_for_multiple_reverts(self):
        def prettify_sha(sha: str) -> tiny_render.Piece:
            return "pretty_" + sha

        def get_sha_description(sha: str) -> tiny_render.Piece:
            return "subject_" + sha

        email = nightly_revert_checker._generate_revert_email(
            repository_name="${repo}",
            friendly_name="${name}",
            sha="${sha}",
            prettify_sha=prettify_sha,
            get_sha_description=get_sha_description,
            new_reverts=[
                revert_checker.Revert(
                    sha="${revert_sha1}", reverted_sha="${reverted_sha1}"
                ),
                revert_checker.Revert(
                    sha="${revert_sha2}", reverted_sha="${reverted_sha2}"
                ),
                # Keep this out-of-order to check that we sort based on SHAs
                revert_checker.Revert(
                    sha="${revert_sha0}", reverted_sha="${reverted_sha0}"
                ),
            ],
        )

        expected_email = nightly_revert_checker._Email(
            subject="[revert-checker/${repo}] new reverts discovered across "
            "${name}",
            body=[
                "It looks like there may be new reverts across ${name} (",
                "pretty_${sha}",
                ").",
                tiny_render.line_break,
                tiny_render.line_break,
                "These are:",
                tiny_render.UnorderedList(
                    [
                        [
                            "pretty_${revert_sha0}",
                            " (appears to revert ",
                            "pretty_${reverted_sha0}",
                            "): ",
                            "subject_${revert_sha0}",
                        ],
                        [
                            "pretty_${revert_sha1}",
                            " (appears to revert ",
                            "pretty_${reverted_sha1}",
                            "): ",
                            "subject_${revert_sha1}",
                        ],
                        [
                            "pretty_${revert_sha2}",
                            " (appears to revert ",
                            "pretty_${reverted_sha2}",
                            "): ",
                            "subject_${revert_sha2}",
                        ],
                    ]
                ),
                tiny_render.line_break,
                "PTAL and consider reverting them locally.",
            ],
        )

        self.assertEqual(email, expected_email)

    def test_sha_prettification_for_email(self):
        sha = "a" * 40
        rev = 123456
        self.assertEqual(
            nightly_revert_checker.prettify_sha_for_email(sha, rev),
            tiny_render.Switch(
                text=f"r{rev} ({sha[:12]})",
                html=tiny_render.Link(
                    href=f"https://github.com/llvm/llvm-project/commit/{sha}",
                    inner=f"r{rev}",
                ),
            ),
        )

    @mock.patch.object(time, "time")
    def test_emailing_about_stale_heads_skips_in_simple_cases(self, time_time):
        now = 1_000_000_000
        time_time.return_value = now

        def assert_no_email(state: nightly_revert_checker.State):
            self.assertFalse(
                nightly_revert_checker.maybe_email_about_stale_heads(
                    state,
                    repository_name="foo",
                    recipients=nightly_revert_checker._EmailRecipients(
                        well_known=[], direct=[]
                    ),
                    prettify_sha=lambda *args: self.fail(
                        "SHAs shouldn't be prettified"
                    ),
                    is_dry_run=True,
                )
            )

        assert_no_email(nightly_revert_checker.State())
        assert_no_email(
            nightly_revert_checker.State(
                heads={
                    "foo": nightly_revert_checker.HeadInfo(
                        last_sha="",
                        first_seen_timestamp=0,
                        next_notification_timestamp=now + 1,
                    ),
                    "bar": nightly_revert_checker.HeadInfo(
                        last_sha="",
                        first_seen_timestamp=0,
                        next_notification_timestamp=now * 2,
                    ),
                }
            )
        )

    def test_state_autoupgrades_from_json_properly(self):
        state = nightly_revert_checker.State.from_json({"abc123": ["def456"]})
        self.assertEqual(state.seen_reverts, {"abc123": ["def456"]})
        self.assertEqual(state.heads, {})

    def test_state_round_trips_through_json(self):
        state = nightly_revert_checker.State(
            seen_reverts={"abc123": ["def456"]},
            heads={
                "head_name": nightly_revert_checker.HeadInfo(
                    last_sha="abc",
                    first_seen_timestamp=123,
                    next_notification_timestamp=456,
                ),
            },
        )
        self.assertEqual(
            state, nightly_revert_checker.State.from_json(state.to_json())
        )

    @mock.patch.object(time, "time")
    @mock.patch.object(nightly_revert_checker, "_send_revert_email")
    def test_emailing_about_stale_with_one_report(
        self, send_revert_email, time_time
    ):
        def prettify_sha(sha: str) -> str:
            return f"pretty({sha})"

        now = 1_000_000_000
        two_days_ago = now - 2 * nightly_revert_checker.ONE_DAY_SECS
        time_time.return_value = now
        recipients = nightly_revert_checker._EmailRecipients(
            well_known=[], direct=[]
        )
        self.assertTrue(
            nightly_revert_checker.maybe_email_about_stale_heads(
                nightly_revert_checker.State(
                    heads={
                        "foo": nightly_revert_checker.HeadInfo(
                            last_sha="<foo sha>",
                            first_seen_timestamp=two_days_ago,
                            next_notification_timestamp=now - 1,
                        ),
                        "bar": nightly_revert_checker.HeadInfo(
                            last_sha="",
                            first_seen_timestamp=0,
                            next_notification_timestamp=now + 1,
                        ),
                    }
                ),
                repository_name="repo",
                recipients=recipients,
                prettify_sha=prettify_sha,
                is_dry_run=False,
            )
        )
        send_revert_email.assert_called_once()
        recipients, email = send_revert_email.call_args[0]

        self.assertEqual(
            tiny_render.render_text_pieces(email.body),
            "Hi! This is a friendly notification that the current upstream "
            "LLVM SHA is being tracked by the LLVM revert checker:\n"
            "  - foo at pretty(<foo sha>), which was last updated ~2 days "
            "ago.\n"
            "If that's still correct, great! If it looks wrong, the revert "
            "checker's SHA autodetection may need an update. Please file a "
            "bug at go/crostc-bug if an update is needed. Thanks!",
        )

    def test_appending_footers_when_none_exist(self):
        base_message = textwrap.dedent(
            """\
            hello: world!

            This is a simple commit message.
            """
        ).rstrip()
        want_message = textwrap.dedent(
            """\
            hello: world!

            This is a simple commit message.

            foo: bar
            bar: baz
            """
        ).rstrip()
        self.assertEqual(
            nightly_revert_checker._append_footers_to_commit_message(
                base_message,
                ("foo: bar", "bar: baz"),
            ),
            want_message,
        )

    def test_appending_footers_when_some_exist(self):
        base_message = textwrap.dedent(
            """\
            hello: world!

            This is a simple commit message.
            this: is not a footer though
            because: it is in the same paragraph as the commit message

            but: this is a footer!
            """
        ).rstrip()
        want_message = textwrap.dedent(
            """\
            hello: world!

            This is a simple commit message.
            this: is not a footer though
            because: it is in the same paragraph as the commit message

            but: this is a footer!
            foo: bar
            """
        ).rstrip()
        self.assertEqual(
            nightly_revert_checker._append_footers_to_commit_message(
                base_message,
                ("foo: bar",),
            ),
            want_message,
        )

    def test_appending_footers_when_last_paragraph_is_tricky(self):
        base_message = textwrap.dedent(
            """\
            hello: world!

            This is a simple commit message.
            this: is not a footer though
            because: it is in the same paragraph as the commit message
            """
        ).rstrip()
        want_message = textwrap.dedent(
            """\
            hello: world!

            This is a simple commit message.
            this: is not a footer though
            because: it is in the same paragraph as the commit message

            foo: bar
            """
        ).rstrip()
        self.assertEqual(
            nightly_revert_checker._append_footers_to_commit_message(
                base_message,
                ("foo: bar",),
            ),
            want_message,
        )
