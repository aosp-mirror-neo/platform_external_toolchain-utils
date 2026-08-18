# Copyright 2020 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for nightly_revert_checker."""

from pathlib import Path
import subprocess
import textwrap
import time
import unittest
from unittest import mock

from cros_utils import bugs
from cros_utils import tiny_render
from llvm_tools import git_llvm_rev
from llvm_tools import nightly_revert_checker


# pylint: disable=protected-access

ARBITRARY_LLVM_CONFIG = git_llvm_rev.LLVMConfig(
    remote="/remote/that/does/not/exist",
    dir=Path("/dir/that/does/not/exist"),
)


class Test(unittest.TestCase):
    """Tests for nightly_revert_checker."""

    def test_bug_rendering_works_for_singular_revert(self) -> None:
        rev_map = {
            "sha_main": 100000,
            "sha_revert_0": 100005,
            "sha_reverted0": 100001,
        }

        title, body = nightly_revert_checker._generate_revert_bug(
            friendly_name="goog/main",
            sha="sha_main",
            get_sha_rev=rev_map.__getitem__,
            get_sha_description=lambda sha: f"subject_{sha}",
            new_reverts=[
                nightly_revert_checker.MultiRevert(
                    revert_sha="sha_revert_0",
                    reverted_shas=["sha_reverted0"],
                )
            ],
        )

        self.assertEqual(
            title,
            "[revert-checker/android] Cherrypick new revert for r100000",
        )
        expected_body = (
            "It looks like there may be a new revert across goog/main "
            "for r100000.\n"
            "\n"
            "That is:\n"
            "  - r100005 (appears to revert r100001): "
            "subject_sha_revert_0 [sha_revert_0]\n"
            "\n"
            "PTAL and cherrypick the revert needed."
        )
        self.assertEqual(body, expected_body)

    def test_bug_rendering_works_for_multiple_reverts(self) -> None:
        rev_map = {
            "sha_main": 100000,
            "sha_revert_0": 100002,
            "sha_reverted0": 100001,
            "sha_revert_1": 100005,
            "sha_revert1a": 100003,
            "sha_revert1b": 100004,
        }

        title, body = nightly_revert_checker._generate_revert_bug(
            friendly_name="goog/main",
            sha="sha_main",
            get_sha_rev=rev_map.__getitem__,
            get_sha_description=lambda sha: f"subject_{sha}",
            new_reverts=[
                nightly_revert_checker.MultiRevert(
                    revert_sha="sha_revert_1",
                    reverted_shas=["sha_revert1a", "sha_revert1b"],
                ),
                nightly_revert_checker.MultiRevert(
                    revert_sha="sha_revert_0",
                    reverted_shas=["sha_reverted0"],
                ),
            ],
        )

        self.assertEqual(
            title,
            "[revert-checker/android] Cherrypick new reverts for r100000",
        )
        expected_body = (
            "It looks like there may be new reverts across goog/main "
            "for r100000.\n"
            "\n"
            "These are:\n"
            "  - r100002 (appears to revert r100001): "
            "subject_sha_revert_0 [sha_revert_0]\n"
            "  - r100005 (appears to revert r100003, r100004): "
            "subject_sha_revert_1 [sha_revert_1]\n"
            "\n"
            "PTAL and cherrypick the reverts needed."
        )
        self.assertEqual(body, expected_body)

    @mock.patch.object(
        nightly_revert_checker,
        "locate_new_reverts_across_shas",
        autospec=True,
    )
    @mock.patch.object(
        nightly_revert_checker,
        "_generate_revert_bug",
        autospec=True,
    )
    @mock.patch.object(
        nightly_revert_checker.bugs,
        "CreateNewBug",
        autospec=True,
    )
    def test_do_file_bugs_files_bug(
        self,
        mock_create_new_bug: mock.Mock,
        mock_gen_bug: mock.Mock,
        mock_locate: mock.Mock,
    ) -> None:
        mock_locate.return_value = (
            nightly_revert_checker.State(),
            [
                nightly_revert_checker.NewRevertInfo(
                    friendly_name="goog/main",
                    sha="sha123",
                    new_reverts=[],
                )
            ],
        )
        mock_gen_bug.return_value = ("Bug Title", "Bug Body")

        recipients = nightly_revert_checker._EmailRecipients(
            well_known=[], direct=["android-llvm-dev@google.com"]
        )

        nightly_revert_checker.do_file_bugs(
            is_dry_run=False,
            llvm_config=ARBITRARY_LLVM_CONFIG,
            upstream_main_branch="main",
            repository="android",
            interesting_shas=[("goog/main", "sha123")],
            state=nightly_revert_checker.State(),
            recipients=recipients,
            gemini_state=None,
            is_chromeos=False,
        )

        mock_create_new_bug.assert_called_once_with(
            component_id=bugs.INTERNAL_ANDROID_COMPONENT,
            title="Bug Title",
            body="Bug Body",
            assignee="android-llvm-bug-triage@google.com",
            cc=["android-llvm-dev@google.com"],
            issue_type=bugs.IssueType.PROCESS,
            priority=bugs.Priority.P4,
            severity=bugs.Severity.S4,
        )

    def test_sha_prettification_for_email(self) -> None:
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
    def test_emailing_about_stale_heads_skips_in_simple_cases(
        self, time_time: mock.Mock
    ) -> None:
        now = 1_000_000_000
        time_time.return_value = now

        def assert_no_email(
            state: nightly_revert_checker.State,
        ) -> None:
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

    def test_state_round_trips_through_json(self) -> None:
        state = nightly_revert_checker.State(
            seen_reverts={"abc123": ["def456"]},
            last_seen_llvm_shas={"abc123": 456},
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
        self, send_revert_email: mock.Mock, time_time: mock.Mock
    ) -> None:
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

    def test_appending_footers_when_none_exist(self) -> None:
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

    def test_appending_footers_when_some_exist(self) -> None:
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

    def test_update_new_state_head_info(self) -> None:
        now = 1_000_000_000
        old_state = nightly_revert_checker.State(
            heads={
                "dropped": nightly_revert_checker.HeadInfo(
                    last_sha="sha_dropped",
                    first_seen_timestamp=now - 100,
                    next_notification_timestamp=now + 100,
                ),
                "kept": nightly_revert_checker.HeadInfo(
                    last_sha="sha_kept",
                    first_seen_timestamp=now - 200,
                    next_notification_timestamp=now + 200,
                ),
                "updated": nightly_revert_checker.HeadInfo(
                    last_sha="sha_updated_old",
                    first_seen_timestamp=now - 300,
                    next_notification_timestamp=now + 300,
                ),
            }
        )
        interesting_shas = [
            ("kept", "sha_kept"),
            ("updated", "sha_updated_new"),
            ("added", "sha_added"),
        ]
        new_state = nightly_revert_checker.State()

        nightly_revert_checker.update_new_state_head_info(
            now=now,
            interesting_shas=interesting_shas,
            old_state=old_state,
            new_state=new_state,
        )

        self.assertNotIn("dropped", new_state.heads)
        self.assertEqual(new_state.heads["kept"], old_state.heads["kept"])
        self.assertEqual(
            new_state.heads["updated"],
            nightly_revert_checker.HeadInfo(
                last_sha="sha_updated_new",
                first_seen_timestamp=now,
                next_notification_timestamp=now
                + nightly_revert_checker.HEAD_STALENESS_ALERT_INITIAL_SECS,
            ),
        )
        self.assertEqual(
            new_state.heads["added"],
            nightly_revert_checker.HeadInfo(
                last_sha="sha_added",
                first_seen_timestamp=now,
                next_notification_timestamp=now
                + nightly_revert_checker.HEAD_STALENESS_ALERT_INITIAL_SECS,
            ),
        )

    def test_appending_footers_when_last_paragraph_is_tricky(self) -> None:
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


@mock.patch.object(git_llvm_rev, "translate_sha_to_rev", autospec=True)
class RevertsOldCommitTest(unittest.TestCase):
    """Tests for reverts_old_commit."""

    def test_sha_translation_failure(
        self, mock_translate_sha_to_rev: mock.Mock
    ) -> None:
        mock_translate_sha_to_rev.side_effect = subprocess.CalledProcessError(
            1, "git"
        )
        self.assertFalse(
            nightly_revert_checker.reverts_old_commit(
                llvm_config=ARBITRARY_LLVM_CONFIG,
                revert_sha="revert_sha",
                reverted_shas=["reverted_sha"],
            )
        )

    def test_simple_true_case(
        self, mock_translate_sha_to_rev: mock.Mock
    ) -> None:
        mock_translate_sha_to_rev.side_effect = [
            git_llvm_rev.Rev(branch="main", number=100_000),
            git_llvm_rev.Rev(branch="main", number=50_000),
        ]
        self.assertTrue(
            nightly_revert_checker.reverts_old_commit(
                llvm_config=ARBITRARY_LLVM_CONFIG,
                revert_sha="revert_sha",
                reverted_shas=["reverted_sha"],
            )
        )

    def test_simple_false_case(
        self, mock_translate_sha_to_rev: mock.Mock
    ) -> None:
        mock_translate_sha_to_rev.side_effect = [
            git_llvm_rev.Rev(branch="main", number=100_000),
            git_llvm_rev.Rev(branch="main", number=90_000),
        ]
        self.assertFalse(
            nightly_revert_checker.reverts_old_commit(
                llvm_config=ARBITRARY_LLVM_CONFIG,
                revert_sha="revert_sha",
                reverted_shas=["reverted_sha"],
            )
        )
