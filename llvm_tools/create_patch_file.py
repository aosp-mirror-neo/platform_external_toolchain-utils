# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Create a PATCHES.json file from ChromiumOS LLVM branches.

If a PATCHES.json file already exists, this script edits it with
only new patches.

Commits on these branches can have metedata footer entries such as:

  patch.cherry: true
  patch.version_range.from: 0
  patch.version_range.until: null
  patch.platforms: chromiumos, android

Which will lead to different metadata in the corresponding PATCHS.json.
"""

import argparse
import dataclasses
import functools
import json
import logging
from pathlib import Path
import re
from typing import Dict, Iterable, List, Optional, Set

from cros_utils import cros_paths
from cros_utils import git_utils
from llvm_tools import atomic_write_file
from llvm_tools import get_llvm_hash
from llvm_tools import git_llvm_rev
from llvm_tools import llvm_next
from llvm_tools import patch_utils


# Don't allow patches to have file names longer than this number of
# characters. We should have some number here as titles
# can be broken, but we also need it long enough to ensure
# unique file names.
_MAX_PATCH_NAME_LENGTH = 128

# Default branch pattern to look for.
_DEFAULT_BRANCH_PATTERN = "*/chromeos/llvm-*"


_CHANGE_ID_REGEX = re.compile(r"^change-id:\s*\w+\s*$", re.IGNORECASE)
_COMMIT_MESSAGE_END_GUESS = re.compile(r"^---(:? .*)?$")
_CHROMEOS_SCOPED_EMAIL = (
    "chromeos-scoped@luci-project-accounts.iam.gserviceaccount.com"
)


@dataclasses.dataclass
class LLVMPatchContext:
    """Information needed to reason about patches on LLVM branches."""

    llvm_dir: Path
    patch_dir: Path
    branch_refs: Iterable[str]
    main_branch_ref: str

    def __post_init__(self):
        self.llvm_config = git_llvm_rev.LLVMConfig(
            git_llvm_rev.MAIN_BRANCH, self.llvm_dir
        )


@dataclasses.dataclass(frozen=True)
class PatchCombo:
    """Holds PatchEntry info and the actual git patch contents."""

    entry: patch_utils.PatchEntry
    contents: str


@dataclasses.dataclass(frozen=True)
class BranchContext:
    """Information needed to create a singular branch from patches."""

    branch_ref: str
    merge_base: str
    llvm_rev: git_llvm_rev.Rev
    patch_entry_combos: List[PatchCombo]

    @property
    def patch_entries(self):
        return [p.entry for p in self.patch_entry_combos]


def _maybe_string_to_int(s: Optional[str]) -> Optional[int]:
    if s is None:
        return None
    if s.lower() in {"null", "none"}:
        return None
    return int(s)


def _get_platforms(commit_metadata: Dict[str, str]) -> List[str]:
    return sorted(
        p.strip()
        for p in commit_metadata.get("patch.platforms", "chromiumos").split(",")
        if p.strip()
    )


def _get_metadata_info(commit_metadata: Dict[str, str]) -> List[str]:
    return [
        p.strip()
        for p in commit_metadata.get("patch.metadata.info", "").split(",")
        if p.strip()
    ]


def _get_metadata_original_sha(
    commit_metadata: Dict[str, str],
) -> Optional[str]:
    return commit_metadata.get("patch.metadata.original_sha")


@functools.lru_cache
def _translate_sha_to_rev_cached(
    llvm_config: git_llvm_rev.LLVMConfig, sha: str
):
    return git_llvm_rev.translate_sha_to_rev(llvm_config, sha)


def filter_change_id(patch_contents: str) -> str:
    """Remove the Change-Id line from the commit message."""
    out = []
    passed_commit_message = False
    for line in patch_contents.splitlines(keepends=True):
        if _COMMIT_MESSAGE_END_GUESS.match(line):
            passed_commit_message = True
        elif not passed_commit_message and _CHANGE_ID_REGEX.match(line):
            # Skip.
            continue
        out.append(line)
    return "".join(out)


def create_branch_contexts(
    patch_context: LLVMPatchContext,
) -> List[BranchContext]:
    """Package all LLVM branch data into an easily usable BranchContext."""

    # Compile this regex outside of the O(nm) loop.
    replace_regex = re.compile(r"\W+")
    entries: List[BranchContext] = []
    for branch_ref in patch_context.branch_refs:
        merge_base = git_utils.merge_base(
            patch_context.llvm_dir, [patch_context.main_branch_ref, branch_ref]
        )
        if not merge_base:
            logging.warning(
                "No merge base between '%s' and '%s'. Skipping.",
                patch_context.main_branch_ref,
                branch_ref,
            )
            continue
        logging.info(
            "Merge base for '%s' and '%s': '%s'",
            patch_context.main_branch_ref,
            branch_ref,
            merge_base,
        )
        commit_shas = list(
            git_utils.commits_between(
                patch_context.llvm_dir, merge_base, branch_ref
            )
        )
        this_branch_combos: List[PatchCombo] = []
        for commit_sha in commit_shas:
            # Skip any commits made by the LUCI infrastructure. These should
            # not exist, but if they do, ignore them.
            if (
                git_utils.commit_author_email(
                    patch_context.llvm_dir,
                    commit_sha,
                )
                == _CHROMEOS_SCOPED_EMAIL
            ):
                logging.warning(
                    "commit %s was made by %s, which is invalid. Skipping.",
                    commit_sha,
                    _CHROMEOS_SCOPED_EMAIL,
                )
                continue
            patch_raw_data = filter_change_id(
                git_utils.format_patch(patch_context.llvm_dir, commit_sha)
            )
            commit_metadata = git_utils.parse_message_metadata(
                patch_raw_data.splitlines()
            )
            subject = git_utils.get_message_subject(
                patch_context.llvm_dir, commit_sha
            )
            # When we recreate cherrypicked commits, we want to ensure
            # we preserve their original upstream SHAs when possible.
            # This information is sometimes available in the metadata.
            # We usually try to use the original_sha as the filename too,
            # when available.
            original_sha = _get_metadata_original_sha(commit_metadata)
            if commit_metadata.get("patch.cherry", "false").lower() == "true":
                if original_sha:
                    rel_patch_path = f"cherry/{original_sha}.patch"
                else:
                    rel_patch_path = f"cherry/{commit_sha}.patch"
            else:
                cleaned_name = replace_regex.sub("-", subject)[
                    : _MAX_PATCH_NAME_LENGTH + 1
                ]
                rel_patch_path = f"{cleaned_name}.patch"
            metadata = {
                "info": _get_metadata_info(commit_metadata),
                "title": subject,
            }
            if original_sha:
                metadata["original_sha"] = original_sha
            entry = patch_utils.PatchEntry(
                workdir=patch_context.patch_dir,
                metadata=metadata,
                rel_patch_path=rel_patch_path,
                platforms=_get_platforms(commit_metadata),
                version_range={
                    "from": _maybe_string_to_int(
                        commit_metadata.get("patch.version_range.from")
                    ),
                    "until": _maybe_string_to_int(
                        commit_metadata.get("patch.version_range.until")
                    ),
                },
            )
            this_branch_combos.append(PatchCombo(entry, patch_raw_data))
        entries.append(
            BranchContext(
                branch_ref=branch_ref,
                merge_base=merge_base,
                llvm_rev=_translate_sha_to_rev_cached(
                    patch_context.llvm_config, merge_base
                ),
                patch_entry_combos=this_branch_combos,
            )
        )
    return entries


def find_new_patches(
    branch_context: BranchContext,
    existing_patches: List[patch_utils.PatchEntry],
) -> List[PatchCombo]:
    """Find unseen patches committed along a given branch."""

    if not branch_context.patch_entry_combos:
        # We may not have landed anything yet, so just skip this branch
        # if so.
        logging.info(
            "No commits found on LLVM branch for '%s'. Skipping.",
            branch_context.branch_ref,
        )
        return []
    applicable_existing = [
        p
        for p in existing_patches
        if p.can_patch_version(branch_context.llvm_rev.number)
    ]
    logging.debug("Found applicable patches:")
    for patch in applicable_existing:
        logging.debug("* %s", patch.title())
    # We drop the base commit, which should always be the first one. We may
    # want to have a more thorough check, but for now, we'll just have an
    # assert.
    starting_title = branch_context.patch_entry_combos[0].entry.title()
    assert "base commit" in starting_title.lower(), (
        "branch_patches did not start with a base commit"
        f" (title was '{starting_title}')"
    )
    # The 1 + is to make sure we skip over the base commit.
    len_of_existing_and_base = 1 + len(applicable_existing)
    if len_of_existing_and_base > len(branch_context.patch_entry_combos):
        logging.warning(
            "Expected at least %s patches on branch, but found only %s. Did"
            " you apply the patches from PATCHES.json to the '%s' branch?",
            len_of_existing_and_base,
            len(branch_context.patch_entry_combos),
            branch_context.branch_ref,
        )
    new_patch_combos = branch_context.patch_entry_combos[
        len_of_existing_and_base:
    ]
    if not new_patch_combos:
        logging.info(
            "No new patches on LLVM branch for '%s'.", branch_context.branch_ref
        )
        return []
    logging.info(
        "New patches on LLVM branch for '%s':", branch_context.branch_ref
    )
    for combo in new_patch_combos:
        logging.info("* %s", combo.entry.title())
    return new_patch_combos


def _find_branch_refs(
    llvm_dir: Path, branch_patterns: Optional[List[str]] = None
) -> Set[str]:
    """Return git branch refs which match the given patterns.

    If 'branch_patterns' is not specified or is empty, use a default glob
    pattern.
    """
    branch_patterns = (
        branch_patterns if branch_patterns else [_DEFAULT_BRANCH_PATTERN]
    )
    branch_refs: Set[str] = set()
    for branch_pattern in branch_patterns:
        branch_refs.update(git_utils.branch_list(llvm_dir, branch_pattern))
    return branch_refs


def _find_new_patch_combos(
    chromiumos_root: Path,
    patch_context: LLVMPatchContext,
    existing_patches: List[patch_utils.PatchEntry],
    check_all_branches: bool = False,
) -> List[PatchCombo]:
    """Find applicable patches for each branch that need to be added."""
    # Go through each branch, check if that branch is within the
    # given bounds, then check if there's any new patches on each branch.
    # If so, add them to the PATCHES.json and write their contents to
    # the patch directory.
    patches_for_each_branch = create_branch_contexts(patch_context)
    new_patch_combos: List[PatchCombo] = []
    if check_all_branches:
        llvm_current_rev = 0
        llvm_next_rev = float("inf")
    else:
        llvm_current_rev = git_llvm_rev.translate_sha_to_rev(
            patch_context.llvm_config,
            get_llvm_hash.GetCrOSCurrentLLVMHash(chromiumos_root),
        ).number
        llvm_next_rev = llvm_next.LLVM_NEXT_REV
    for container in patches_for_each_branch:
        if not llvm_current_rev <= container.llvm_rev.number <= llvm_next_rev:
            logging.info(
                "Skipping branch '%s': merge base is outside"
                " current and next bounds [%s...%s]",
                container.branch_ref,
                llvm_current_rev,
                llvm_next_rev,
            )
            continue
        logging.info(
            "Checking for new commits on branch '%s'",
            container.branch_ref,
        )
        new_patch_combos += find_new_patches(container, existing_patches)
    return new_patch_combos


def parse_args(argv: List[str]) -> argparse.Namespace:
    """Parse passed in argv list."""

    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    chromiumos_root_action = parser.add_argument(
        "--chromiumos-root",
        type=Path,
        help="Path to ChromiumOS root. If not specified, it is autodetected.",
    )
    llvm_dir_action = parser.add_argument(
        "--llvm-dir",
        type=Path,
        help="""Path to a ChromiumOS llvm-project directory. If not
        specified, it is autodetected.""",
    )
    parser.add_argument(
        "--patch-dir",
        required=True,
        type=Path,
        help="""Path to the directory containing the PATCHES.json and
        its associated patch files. If the PATCHES.json file does not exist,
        create it.""",
    )
    parser.add_argument(
        "--check-all-branches",
        action="store_true",
        help="""By default, we only check for new patches on branches
        which exist between LLVM Current and LLVM Next. Passing this flag
        changes the behaviour to instead check every branch which matches
        the branch patterns.
        """,
    )
    parser.add_argument(
        "-b",
        "--branch",
        action="append",
        dest="branch_patterns",
        default=[],
        help=f"""Search for branches which match a given glob.
        Default is {_DEFAULT_BRANCH_PATTERN}. This can be passed multiple
        times to match every necessary branch.
        """,
    )
    args = parser.parse_args(argv)
    if not args.chromiumos_root:
        if repo_root := cros_paths.script_chromiumos_checkout():
            args.chromiumos_root = repo_root
        else:
            raise argparse.ArgumentError(
                chromiumos_root_action,
                "Could not find chromiumos root automatically."
                " Pass --chromiumos-root manually.",
            )
    llvm_dir_error = argparse.ArgumentError(
        llvm_dir_action,
        "Could not find llvm dir automatically. Pass --llvm-dir manually.",
    )
    if not args.llvm_dir:
        if not args.chromiumos_root:
            raise llvm_dir_error
        llvm_dir = args.chromiumos_root / cros_paths.LLVM_PROJECT
        if not (llvm_dir / ".git").is_dir():
            raise llvm_dir_error
        args.llvm_dir = llvm_dir
    return args


def main(argv: List[str]):
    """Entry point for the program."""
    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.INFO,
    )

    args = parse_args(argv)
    patches_json_file = args.patch_dir / "PATCHES.json"
    try:
        with open(patches_json_file, encoding="utf-8") as f:
            existing_patches = patch_utils.json_to_patch_entries(
                args.patch_dir, f
            )
    except FileNotFoundError:
        existing_patches = []
    main_branch_ref = (
        f"{git_utils.CROS_EXTERNAL_REMOTE}/{git_utils.CROS_MAIN_BRANCH}"
    )
    patch_context = LLVMPatchContext(
        llvm_dir=args.llvm_dir,
        patch_dir=args.patch_dir,
        branch_refs=_find_branch_refs(args.llvm_dir, args.branch_patterns),
        main_branch_ref=main_branch_ref,
    )
    new_patch_combos = _find_new_patch_combos(
        args.chromiumos_root,
        patch_context,
        existing_patches,
        args.check_all_branches,
    )
    if not new_patch_combos:
        logging.info("No new patches to add. Nothing to do.")
        return
    for combo in new_patch_combos:
        patch_path = combo.entry.patch_path()
        logging.info("Writing patch '%s'", patch_path)
        patch_path.parent.mkdir(parents=True, exist_ok=True)
        with atomic_write_file.atomic_write(patch_path, encoding="utf-8") as f:
            f.write(combo.contents)
    logging.info("Writing PATCHES.json to '%s'", patches_json_file)
    with atomic_write_file.atomic_write(
        patches_json_file, "w", encoding="utf-8"
    ) as f:
        json.dump(
            [p.to_dict() for p in existing_patches]
            + [c.entry.to_dict() for c in new_patch_combos],
            f,
            indent=2,
            sort_keys=True,
        )
        f.write("\n")
