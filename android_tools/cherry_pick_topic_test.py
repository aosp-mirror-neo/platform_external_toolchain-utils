# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tests for pick_topic."""
import concurrent.futures
import pathlib
import unittest
from unittest import mock

from android_tools import cherry_pick_topic
from cros_utils import gerrit_utils


class ResolveAndSortClDependenciesTest(unittest.TestCase):
    """Tests for resolve_and_sort_cl_dependencies."""

    @mock.patch.object(gerrit_utils, "fetch_related_changes")
    def test_truncates_chain_to_child_most_cl(
        self, mock_fetch_related_changes: mock.Mock
    ) -> None:
        """Verifies that the chain is truncated to the child-most CL."""
        cl1 = cherry_pick_topic.CLDetails(project="project/a", cl_number=1)
        cl2 = cherry_pick_topic.CLDetails(project="project/a", cl_number=2)
        cl3 = cherry_pick_topic.CLDetails(project="project/a", cl_number=3)

        # The input CLs only contain 1 and 3. The chain contains 1, 2, 3, 4.
        # The child-most CL in the input is 3. So the final list should be
        # [1, 2, 3].
        cls = [cl1, cl3]
        chain_info = [
            gerrit_utils.RelatedChangeInfo(
                cl_number=4,
                project="project/a",
                status=gerrit_utils.CLStatus.NEW,
            ),
            gerrit_utils.RelatedChangeInfo(
                cl_number=3,
                project="project/a",
                status=gerrit_utils.CLStatus.NEW,
            ),
            gerrit_utils.RelatedChangeInfo(
                cl_number=2,
                project="project/a",
                status=gerrit_utils.CLStatus.NEW,
            ),
            gerrit_utils.RelatedChangeInfo(
                cl_number=1,
                project="project/a",
                status=gerrit_utils.CLStatus.NEW,
            ),
        ]
        mock_fetch_related_changes.return_value = chain_info

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            result = cherry_pick_topic.resolve_and_sort_cl_dependencies(
                cls, "gerrit_host", executor
            )

        self.assertEqual(result, [cl1, cl2, cl3])
        mock_fetch_related_changes.assert_called_once_with("gerrit_host", 1)

    @mock.patch.object(gerrit_utils, "fetch_related_changes")
    def test_no_truncation_if_child_most_is_last(
        self, mock_fetch_related_changes: mock.Mock
    ) -> None:
        """Verifies no truncation when the child-most CL is the last one."""
        cl1 = cherry_pick_topic.CLDetails(project="project/a", cl_number=1)
        cl2 = cherry_pick_topic.CLDetails(project="project/a", cl_number=2)
        cl3 = cherry_pick_topic.CLDetails(project="project/a", cl_number=3)

        cls = [cl1, cl3]
        chain_info = [
            gerrit_utils.RelatedChangeInfo(
                cl_number=3,
                project="project/a",
                status=gerrit_utils.CLStatus.NEW,
            ),
            gerrit_utils.RelatedChangeInfo(
                cl_number=2,
                project="project/a",
                status=gerrit_utils.CLStatus.NEW,
            ),
            gerrit_utils.RelatedChangeInfo(
                cl_number=1,
                project="project/a",
                status=gerrit_utils.CLStatus.NEW,
            ),
        ]
        mock_fetch_related_changes.return_value = chain_info

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            result = cherry_pick_topic.resolve_and_sort_cl_dependencies(
                cls, "gerrit_host", executor
            )

        self.assertEqual(result, [cl1, cl2, cl3])
        mock_fetch_related_changes.assert_called_once_with("gerrit_host", 1)

    @mock.patch.object(gerrit_utils, "fetch_related_changes")
    def test_single_cl_from_chain(
        self, mock_fetch_related_changes: mock.Mock
    ) -> None:
        """Verifies correct handling when only one CL from a chain is given."""
        cl1 = cherry_pick_topic.CLDetails(project="project/a", cl_number=1)
        cl2 = cherry_pick_topic.CLDetails(project="project/a", cl_number=2)

        cls = [cl2]
        chain_info = [
            gerrit_utils.RelatedChangeInfo(
                cl_number=3,
                project="project/a",
                status=gerrit_utils.CLStatus.NEW,
            ),
            gerrit_utils.RelatedChangeInfo(
                cl_number=2,
                project="project/a",
                status=gerrit_utils.CLStatus.NEW,
            ),
            gerrit_utils.RelatedChangeInfo(
                cl_number=1,
                project="project/a",
                status=gerrit_utils.CLStatus.NEW,
            ),
        ]
        mock_fetch_related_changes.return_value = chain_info

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            result = cherry_pick_topic.resolve_and_sort_cl_dependencies(
                cls, "gerrit_host", executor
            )

        self.assertEqual(result, [cl1, cl2])
        mock_fetch_related_changes.assert_called_once_with("gerrit_host", 2)

    @mock.patch.object(gerrit_utils, "fetch_related_changes")
    def test_standalone_cl(self, mock_fetch_related_changes: mock.Mock) -> None:
        """Verifies correct handling of a standalone CL."""
        cl1 = cherry_pick_topic.CLDetails(project="project/a", cl_number=1)
        cls = [cl1]
        mock_fetch_related_changes.return_value = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            result = cherry_pick_topic.resolve_and_sort_cl_dependencies(
                cls, "gerrit_host", executor
            )

        self.assertEqual(result, [cl1])
        mock_fetch_related_changes.assert_called_once_with("gerrit_host", 1)

    @mock.patch.object(gerrit_utils, "fetch_related_changes")
    def test_multiple_cls_and_projects(
        self, mock_fetch_related_changes: mock.Mock
    ) -> None:
        """Verifies correct handling of multiple CLs in multiple projects."""
        cl1a = cherry_pick_topic.CLDetails(project="project/a", cl_number=1)
        cl2a = cherry_pick_topic.CLDetails(project="project/a", cl_number=2)
        cl1b = cherry_pick_topic.CLDetails(project="project/b", cl_number=3)
        cl2b = cherry_pick_topic.CLDetails(project="project/b", cl_number=4)

        cls = [cl2a, cl2b]

        def fetch_side_effect(
            gerrit_host: str, change_id: int
        ) -> list[gerrit_utils.RelatedChangeInfo]:
            del gerrit_host  # unused
            if change_id == cl2a.cl_number:
                return [
                    gerrit_utils.RelatedChangeInfo(
                        cl_number=2,
                        project="project/a",
                        status=gerrit_utils.CLStatus.NEW,
                    ),
                    gerrit_utils.RelatedChangeInfo(
                        cl_number=1,
                        project="project/a",
                        status=gerrit_utils.CLStatus.NEW,
                    ),
                ]
            if change_id == cl2b.cl_number:
                return [
                    gerrit_utils.RelatedChangeInfo(
                        cl_number=4,
                        project="project/b",
                        status=gerrit_utils.CLStatus.NEW,
                    ),
                    gerrit_utils.RelatedChangeInfo(
                        cl_number=3,
                        project="project/b",
                        status=gerrit_utils.CLStatus.NEW,
                    ),
                ]
            return []

        mock_fetch_related_changes.side_effect = fetch_side_effect

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            result = cherry_pick_topic.resolve_and_sort_cl_dependencies(
                cls, "gerrit_host", executor
            )

        self.assertEqual(result, [cl1a, cl2a, cl1b, cl2b])
        self.assertEqual(mock_fetch_related_changes.call_count, 2)
        mock_fetch_related_changes.assert_has_calls(
            [mock.call("gerrit_host", 2), mock.call("gerrit_host", 4)],
            any_order=True,
        )


class PickTopicTest(unittest.TestCase):
    """Tests for pick_topic."""

    def test_generate_bash_cherry_pick_commands_with_tag(self) -> None:
        """Verify cherry-pick command generation with a tag."""
        cherry_picks = [
            cherry_pick_topic.CherrypickDesc(
                project="project/a", cherrypick_command="cmd a1", cl_number=1
            ),
            cherry_pick_topic.CherrypickDesc(
                project="project/b", cherrypick_command="cmd b", cl_number=3
            ),
            cherry_pick_topic.CherrypickDesc(
                project="project/a", cherrypick_command="cmd a2", cl_number=2
            ),
        ]
        project_mappings = {
            "project/a": "path/to/a",
            "project/b": "path/to/b",
        }
        android_tree = pathlib.Path("/tmp/android")
        topic = "my-topic"
        tag_or_branch = cherry_pick_topic.TagOrBranch(tag="my-tag")
        # pylint: disable=protected-access
        commands = cherry_pick_topic._generate_bash_cherry_pick_commands(
            cherry_picks=cherry_picks,
            project_mappings=project_mappings,
            android_tree=android_tree,
            topic=topic,
            tag_or_branch=tag_or_branch,
        )
        expected_commands = [
            "# Cherry-pick commands for topic: my-topic",
            "(cd /tmp/android/path/to/a && cmd a1)",
            "(cd /tmp/android/path/to/a && cmd a2)",
            "(cd /tmp/android/path/to/a && git tag -f my-tag)",
            "(cd /tmp/android/path/to/b && cmd b)",
            "(cd /tmp/android/path/to/b && git tag -f my-tag)",
        ]
        self.assertEqual(commands, expected_commands)

    def test_generate_bash_cherry_pick_commands_no_tag(self) -> None:
        """Verify cherry-pick command generation without a tag."""
        cherry_picks = [
            cherry_pick_topic.CherrypickDesc(
                project="project/a", cherrypick_command="cmd a", cl_number=1
            )
        ]
        project_mappings = {"project/a": "path/to/a"}
        android_tree = pathlib.Path("/tmp/android")
        topic = "my-topic"
        # pylint: disable=protected-access
        commands = cherry_pick_topic._generate_bash_cherry_pick_commands(
            cherry_picks=cherry_picks,
            project_mappings=project_mappings,
            android_tree=android_tree,
            topic=topic,
            tag_or_branch=None,
        )
        expected_commands = [
            "# Cherry-pick commands for topic: my-topic",
            "(cd /tmp/android/path/to/a && cmd a)",
        ]
        self.assertEqual(commands, expected_commands)

    def test_generate_bash_cherry_pick_commands_project_not_in_manifest(
        self,
    ) -> None:
        """Verify cherry-pick commands with a project not in manifest."""
        cherry_picks = [
            cherry_pick_topic.CherrypickDesc(
                project="project/a", cherrypick_command="cmd a", cl_number=1
            ),
            cherry_pick_topic.CherrypickDesc(
                project="project/b", cherrypick_command="cmd b", cl_number=2
            ),
        ]
        project_mappings = {"project/a": "path/to/a"}
        android_tree = pathlib.Path("/tmp/android")
        topic = "my-topic"
        tag_or_branch = cherry_pick_topic.TagOrBranch(tag="my-tag")
        # pylint: disable=protected-access
        commands = cherry_pick_topic._generate_bash_cherry_pick_commands(
            cherry_picks=cherry_picks,
            project_mappings=project_mappings,
            android_tree=android_tree,
            topic=topic,
            tag_or_branch=tag_or_branch,
        )
        expected_commands = [
            "# Cherry-pick commands for topic: my-topic",
            "(cd /tmp/android/path/to/a && cmd a)",
            "(cd /tmp/android/path/to/a && git tag -f my-tag)",
        ]
        self.assertEqual(commands, expected_commands)

    def test_generate_bash_cherry_pick_commands_with_branch(self) -> None:
        """Verify cherry-pick command generation with a branch."""
        cherry_picks = [
            cherry_pick_topic.CherrypickDesc(
                project="project/a", cherrypick_command="cmd a1", cl_number=1
            ),
            cherry_pick_topic.CherrypickDesc(
                project="project/b", cherrypick_command="cmd b", cl_number=3
            ),
            cherry_pick_topic.CherrypickDesc(
                project="project/a", cherrypick_command="cmd a2", cl_number=2
            ),
        ]
        project_mappings = {
            "project/a": "path/to/a",
            "project/b": "path/to/b",
        }
        android_tree = pathlib.Path("/tmp/android")
        topic = "my-topic"
        tag_or_branch = cherry_pick_topic.TagOrBranch(branch="my-branch")
        # pylint: disable=protected-access
        commands = cherry_pick_topic._generate_bash_cherry_pick_commands(
            cherry_picks=cherry_picks,
            project_mappings=project_mappings,
            android_tree=android_tree,
            topic=topic,
            tag_or_branch=tag_or_branch,
        )
        expected_commands = [
            "# Cherry-pick commands for topic: my-topic",
            "(cd /tmp/android/path/to/a && cmd a1)",
            "(cd /tmp/android/path/to/a && cmd a2)",
            "(cd /tmp/android/path/to/a && repo start --head my-branch .)",
            "(cd /tmp/android/path/to/b && cmd b)",
            "(cd /tmp/android/path/to/b && repo start --head my-branch .)",
        ]
        self.assertEqual(commands, expected_commands)
