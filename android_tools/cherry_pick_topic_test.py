# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tests for pick_topic."""
import pathlib
import unittest

from android_tools import cherry_pick_topic


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

    def test_generate_bash_cherry_pick_commands_preserves_dependency_order(
        self,
    ) -> None:
        """Verify command generation preserves dependency order.

        It should not use CL number order.
        """
        # CL 2 is parent (applied first), CL 1 is child.
        cherry_picks = [
            cherry_pick_topic.CherrypickDesc(
                project="project/a", cherrypick_command="cmd a2", cl_number=2
            ),
            cherry_pick_topic.CherrypickDesc(
                project="project/a", cherrypick_command="cmd a1", cl_number=1
            ),
        ]
        project_mappings = {
            "project/a": "path/to/a",
        }
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
            "(cd /tmp/android/path/to/a && cmd a2)",
            "(cd /tmp/android/path/to/a && cmd a1)",
        ]
        self.assertEqual(commands, expected_commands)
