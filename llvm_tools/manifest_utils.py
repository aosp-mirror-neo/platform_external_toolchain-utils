# Copyright 2023 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Provides utilities to read and edit the ChromiumOS Manifest entries.

While this code reads and edits the internal manifest, it should only operate
on toolchain projects (llvm-project, etc.) which are public.
"""

from pathlib import Path
import shutil
import subprocess
from typing import List, Optional, Union
from xml.etree import ElementTree

from cros_utils import cros_paths
from llvm_tools import atomic_write_file


LLVM_PROJECT_PATH = str(cros_paths.LLVM_PROJECT)


class FormattingError(Exception):
    """Error occurred when formatting the manifest."""


class UpdateManifestError(Exception):
    """Error occurred when updating the manifest."""


class ManifestParseError(Exception):
    """Error occurred when parsing the contents of the manifest."""


def make_xmlparser() -> ElementTree.XMLParser:
    """Return a new xmlparser with custom TreeBuilder."""
    return ElementTree.XMLParser(
        target=ElementTree.TreeBuilder(insert_comments=True)
    )


def _find_llvm_project_in_manifest_tree(
    xmlroot: ElementTree.Element,
) -> Optional[ElementTree.Element]:
    """Returns the llvm-project `project` in `xmlroot`, if it exists."""
    for child in xmlroot:
        if (
            child.tag == "project"
            and child.attrib.get("path") == LLVM_PROJECT_PATH
        ):
            return child
    return None


def extract_current_llvm_hash_or_ref(src_tree: Path) -> str:
    """Returns the current LLVM SHA for the CrOS tree rooted at `src_tree`.

    Raises:
        ManifestParseError if the manifest didn't have the expected contents.
    """
    xmlroot = ElementTree.parse(
        get_chromeos_manifest_path(src_tree), parser=make_xmlparser()
    ).getroot()
    return extract_current_llvm_hash_or_ref_from_xml(xmlroot)


def extract_current_llvm_hash_or_ref_from_xml(
    xmlroot: ElementTree.Element,
) -> str:
    """Returns the current LLVM SHA for the parsed XML file.

    Raises:
        ManifestParseError if the manifest didn't have the expected contents.
    """
    if xmlroot.tag != "manifest":
        raise ManifestParseError(
            f"Root tag is {xmlroot.tag}; should be `manifest`."
        )

    llvm_project = _find_llvm_project_in_manifest_tree(xmlroot)
    if llvm_project is None:
        raise ManifestParseError("No llvm-project `project` found in manifest.")

    revision = llvm_project.attrib.get("revision")
    if not revision:
        raise ManifestParseError("Toolchain's `project` has no revision.")

    return revision


def update_chromeos_manifest_in_manifest_dir(
    revision: str, manifest_dir: Path, chromeos_tree: Optional[Path] = None
) -> Path:
    """update_chromeos_manifest, taking the directory to manifest-interal.

    Args:
        revision: Revision of LLVM
        manifest_dir: The directory of the manifest change
        chromeos_tree: The ChromeOS tree to run miscellaneous tools (e.g.,
            `cros` in). Inferred from `manifest_dir` if none is passed.
    """
    if not chromeos_tree:
        # Since this is just 'anywhere in a ChromeOS tree', no need to find the
        # _actual_ root.
        chromeos_tree = manifest_dir
    # Get an absolute path to this here, since `format_manifest` later may
    # change the cwd, and `manifest_dir` may be relative to cwd.
    manifest_path = get_chromeos_manifest_path_from_manifest_dir(
        manifest_dir
    ).absolute()
    parser = make_xmlparser()
    xmltree = ElementTree.parse(manifest_path, parser)
    update_chromeos_manifest_tree(revision, xmltree.getroot())
    with atomic_write_file.atomic_write(manifest_path, mode="wb") as f:
        xmltree.write(f, encoding="utf-8")
    format_manifest(manifest_path, cwd=chromeos_tree)
    return manifest_path


def update_chromeos_manifest(revision: str, src_tree: Path) -> Path:
    """Replaces the manifest project revision with 'revision'.

    Notably, this function reformats the manifest file to preserve
    the formatting as specified by 'cros format'.

    Args:
        revision: Revision (git sha) to use in the manifest.
        src_tree: Path to the root of the source tree checkout.

    Returns:
        The manifest path.

    Post:
        The llvm-project revision info in the chromeos repo manifest
        is updated with 'revision'.

    Raises:
        UpdateManifestError: The manifest could not be changed.
        FormattingError: The manifest could not be reformatted.
    """
    return update_chromeos_manifest_in_manifest_dir(
        revision,
        get_manifest_internal_path(src_tree),
        chromeos_tree=src_tree,
    )


def get_manifest_internal_path(src_tree: Path) -> Path:
    """Return the path to manifest-internal inside of the CrOS tree."""
    return src_tree / "manifest-internal"


def get_chromeos_manifest_path(src_tree: Path) -> Path:
    """Return the path to the toolchain manifest."""
    return get_chromeos_manifest_path_from_manifest_dir(
        get_manifest_internal_path(src_tree)
    )


def get_chromeos_manifest_path_from_manifest_dir(manifest_dir: Path) -> Path:
    """Return the path to the toolchain manifest in a manifest dir."""
    return manifest_dir / "_toolchain.xml"


def update_chromeos_manifest_tree(revision: str, xmlroot: ElementTree.Element):
    """Update the revision info for LLVM for a manifest XML root."""
    llvm_project_elem = _find_llvm_project_in_manifest_tree(xmlroot)
    # Element objects can be falsy, so we need to explicitly check None.
    if llvm_project_elem is None:
        raise UpdateManifestError("xmltree did not have llvm-project")
    llvm_project_elem.attrib["revision"] = revision


def format_manifest(repo_manifest: Path, cwd: Optional[Path] = None):
    """Use cros format to format the given manifest."""
    if not shutil.which("cros"):
        raise FormattingError(
            "unable to format manifest, 'cros'" " executable not in PATH"
        )
    cmd: List[Union[str, Path]] = ["cros", "format", repo_manifest]
    subprocess.run(cmd, check=True, cwd=cwd)
