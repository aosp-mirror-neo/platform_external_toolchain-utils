# Copyright 2023 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Provides utilities to read and edit the ChromiumOS Manifest entries.

While this code reads and edits the internal manifest, it should only operate
on toolchain projects (llvm-project, etc.) which are public.
"""

from xml.etree import ElementTree

from llvm_tools import manifest_utils
from llvm_tools import test_helpers


MANIFEST_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<manifest>
  <!-- Comment that should not be removed.
       Multiple lines. -->
  <include name="_remotes.xml" />
  <default revision="refs/heads/main"
           remote="cros"
           sync-j="8" />

  <include name="_kernel_upstream.xml" />

  <!-- Common projects for developing CrOS. -->
  <project path="src/repohooks"
           name="chromiumos/repohooks"
           groups="minilayout,paygen,firmware,buildtools,labtools,crosvm" />
  <repo-hooks in-project="chromiumos/repohooks"
              enabled-list="pre-upload" />
  <project path="chromite"
           name="chromiumos/chromite"
           groups="minilayout,paygen,firmware,buildtools,chromeos-admin">
    <copyfile src="AUTHORS" dest="AUTHORS" />
    <copyfile src="LICENSE" dest="LICENSE" />
  </project>
  <project path="src/third_party/llvm-project"
           name="external/github.com/llvm/llvm-project"
           groups="notdefault,bazel"
           revision="abcd" />
  <project path="chromite/third_party/pyelftools"
           name="chromiumos/third_party/pyelftools"
           revision="refs/heads/chromeos-0.22"
           groups="minilayout,paygen,firmware,buildtools" />
</manifest>
"""

# A simpler manifest fixture for testing project mappings specifically.
PROJECT_MAPPINGS_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<manifest>
  <project path="path/to/project1" name="name_of_project1" />
  <project path="path/to/project2" name="name_of_project2" revision="1234" />
  <project path="path/without/name" />
  <project name="name/without/path" />
  <!-- A non-project tag -->
  <default revision="refs/heads/main" remote="cros" />
</manifest>
"""


class TestManifestUtils(test_helpers.TempDirTestCase):
    """Test manifest_utils."""

    def test_update_chromeos_manifest(self) -> None:
        root = ElementTree.fromstring(
            MANIFEST_FIXTURE,
            parser=manifest_utils.make_xmlparser(),
        )
        manifest_utils.update_chromeos_manifest_tree("wxyz", root)
        string_root1 = ElementTree.tostring(root)
        self.assertRegex(
            str(string_root1, encoding="utf-8"),
            r'revision="wxyz"',
        )
        self.assertRegex(
            str(string_root1, encoding="utf-8"),
            r"<!-- Comment that should not be removed.",
        )
        self.assertNotRegex(
            str(string_root1, encoding="utf-8"),
            r'revision="abcd"',
        )
        # Check idempotence.
        manifest_utils.update_chromeos_manifest_tree("wxyz", root)
        string_root2 = ElementTree.tostring(root)
        self.assertEqual(string_root1, string_root2)

    def test_extract_current_llvm_hash(self) -> None:
        root = ElementTree.fromstring(
            MANIFEST_FIXTURE,
            parser=manifest_utils.make_xmlparser(),
        )
        self.assertEqual(
            manifest_utils.extract_current_llvm_hash_or_ref_from_xml(root),
            "abcd",
        )

    def test_read_manifest_project_mappings(self) -> None:
        tempdir = self.make_tempdir()
        temp_manifest_path = tempdir / "test_manifest.xml"
        temp_manifest_path.write_text(
            PROJECT_MAPPINGS_FIXTURE, encoding="utf-8"
        )

        mappings = list(
            manifest_utils.read_manifest_project_mappings(temp_manifest_path)
        )
        self.assertEqual(len(mappings), 2)

        self.assertEqual(mappings[0].project_path, "path/to/project1")
        self.assertEqual(mappings[0].project_name, "name_of_project1")

        self.assertEqual(mappings[1].project_path, "path/to/project2")
        self.assertEqual(mappings[1].project_name, "name_of_project2")
