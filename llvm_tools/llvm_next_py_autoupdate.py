# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Automatically keeps llvm_next.py in the current toolchain-utils fresh.

The llvm_next.py file this targets is in the same directory as this script in
toolchain-utils. Actual edits are made in a worktree, and locally `git
commit`ed by this script.

It does this by:
    - Removing obsolete testing URLs
    - Auto-updating patch-sets as appropriate
"""

import argparse
import logging
from pathlib import Path
import subprocess
from typing import Iterable

from cros_utils import cros_paths
from cros_utils import git_utils
from llvm_tools import cros_cls
from llvm_tools import llvm_next


def write_url_list(
    llvm_next_py_file_path: Path,
    new_manifest_cl: str | None,
    new_allowlist_urls: list[str],
) -> None:
    llvm_next_py = llvm_next_py_file_path.read_text(encoding="utf-8")

    # Replace LLVM_NEXT_MANIFEST_CL. We assume this always takes exactly one
    # line for simplicity. (If this somehow gets messed up, preuploads should
    # catch the invalid syntax)
    manifest_prefix = "_LLVM_NEXT_MANIFEST_CL: str | None = "
    start_idx = llvm_next_py.index(manifest_prefix)
    end_line_idx = llvm_next_py.index("\n", start_idx)

    manifest_val_str = repr(new_manifest_cl) if new_manifest_cl else "None"
    updated_manifest_line = f"{manifest_prefix}{manifest_val_str}"

    llvm_next_py = (
        llvm_next_py[:start_idx]
        + updated_manifest_line
        + llvm_next_py[end_line_idx:]
    )

    # Replace LLVM_NEXT_TESTING_URL_ALLOWLIST. This can either be a
    # single-line or multi-line tuple.
    allowlist_prefix = "_LLVM_NEXT_TESTING_URL_ALLOWLIST: tuple[str, ...] = ("
    start_idx = llvm_next_py.index(allowlist_prefix)

    after_start_paren = start_idx + len(allowlist_prefix)
    line_end = llvm_next_py.index("\n", after_start_paren)
    same_line_end_paren = llvm_next_py.find(")", after_start_paren, line_end)
    if same_line_end_paren != -1:
        end_paren = same_line_end_paren
    else:
        end_paren = llvm_next_py.index("\n)", after_start_paren)

    new_list_contents = "\n".join(repr(x) + "," for x in new_allowlist_urls)
    if new_list_contents:
        new_list_contents = "\n" + new_list_contents + "\n"

    llvm_next_py = (
        llvm_next_py[:after_start_paren]
        + new_list_contents
        + llvm_next_py[end_paren:]
    )

    llvm_next_py_file_path.write_text(llvm_next_py, encoding="utf-8")
    subprocess.run(
        ("cros", "format", llvm_next_py_file_path),
        check=True,
    )


def compute_new_urls(
    manifest_cl: cros_cls.ChangeListURL,
    is_manifest_closed: bool,
    all_changes: list[cros_cls.GerritChange],
    owners: list[str],
    current_allowlist_urls: Iterable[cros_cls.ChangeListURL],
) -> tuple[str | None, list[str]]:
    """Computes the new manifest CL and allowlist URLs.

    Args:
        manifest_cl: The current manifest CL URL.
        is_manifest_closed: True if the manifest CL is closed.
        all_changes: List of changes including main CL and deps.
        owners: List of toolchain owners.
        current_allowlist_urls: An iterable of current testing URL allowlist.

    Returns:
        A tuple of (new_manifest_cl_str, new_allowlist_urls_strs).
    """
    if is_manifest_closed:
        return None, []

    _, untrusted = cros_cls.partition_changes_by_uploader_trust(
        all_changes, owners
    )

    allowlist_list = tuple(current_allowlist_urls)
    allowlist_indices = {cl.cl_id: i for i, cl in enumerate(allowlist_list)}

    # Sort these first preferring existing ordering (to minimize diff), and
    # second... just choose the CL number for consistency.
    untrusted.sort(
        key=lambda c: (
            allowlist_indices.get(c.url.cl_id, len(allowlist_list)),
            c.url.cl_id,
        )
    )
    new_manifest_cl_str = str(manifest_cl)
    new_allowlist_urls: list[str] = []

    for change in untrusted:
        if change.url.cl_id != manifest_cl.cl_id:
            new_allowlist_urls.append(str(change.url))
            continue

        if change.url.patch_set != manifest_cl.patch_set:
            logging.info(
                "Manifest CL %s patch-set was updated by untrusted user; "
                "updating to lock it.",
                manifest_cl,
            )
            new_manifest_cl_str = str(change.url)

    return new_manifest_cl_str, new_allowlist_urls


def update_manifest_and_allowlist_urls(
    manifest_cl: cros_cls.ChangeListURL,
    owners: list[str],
) -> tuple[str | None, list[str]]:
    """Updates manifest and allowlist URLs by fetching deps and partitioning."""
    deps = cros_cls.fetch_gerrit_deps_of_most_recent_patchset(manifest_cl)

    main_cl_in_deps = None
    for change in deps:
        if change.url.cl_id == manifest_cl.cl_id:
            main_cl_in_deps = change
            break

    if not main_cl_in_deps:
        raise ValueError(f"Main CL {manifest_cl} not found in its own deps!")

    if main_cl_in_deps.status is None:
        raise ValueError(f"Status not available for main CL {manifest_cl}")

    is_manifest_closed = not main_cl_in_deps.status.is_open()
    return compute_new_urls(
        manifest_cl,
        is_manifest_closed,
        deps,
        owners,
        llvm_next.LLVM_NEXT_TESTING_URL_ALLOWLIST,
    )


def parse_opts(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="""
        Upload changes after making them, auto-add reviewer(s), and hit CQ+1.
        """,
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> None:
    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.INFO,
    )

    opts = parse_opts(argv)

    if not llvm_next.LLVM_NEXT_MANIFEST_CL:
        if not llvm_next.LLVM_NEXT_TESTING_URL_ALLOWLIST:
            logging.info("LLVM_NEXT_MANIFEST_CL is None; doing nothing.")
            return

        logging.info(
            "LLVM_NEXT_MANIFEST_CL is None; clearing testing URL allowlist."
        )
        new_manifest_cl = None
        new_allowlist_urls: list[str] = []
        change_descriptions = (
            "LLVM_NEXT_MANIFEST_CL is None; clearing testing URL allowlist."
        )
    else:
        manifest_cl = llvm_next.LLVM_NEXT_MANIFEST_CL
        owners = cros_cls.fetch_current_toolchain_owners()

        new_manifest_cl, new_allowlist_urls = (
            update_manifest_and_allowlist_urls(manifest_cl, owners)
        )

        current_manifest_str = (
            str(llvm_next.LLVM_NEXT_MANIFEST_CL)
            if llvm_next.LLVM_NEXT_MANIFEST_CL
            else None
        )
        update_needed = new_manifest_cl != current_manifest_str or set(
            new_allowlist_urls
        ) != {str(u) for u in llvm_next.LLVM_NEXT_TESTING_URL_ALLOWLIST}

        if not update_needed:
            logging.info("No updates needed to CL lists.")
            return

        desc_parts = []
        if new_manifest_cl != current_manifest_str:
            if new_manifest_cl is None:
                desc_parts.append(
                    f"Manifest CL {manifest_cl} was closed; clearing all URLs."
                )
            else:
                desc_parts.append(f"Manifest CL updated to {new_manifest_cl}")

        if set(new_allowlist_urls) != {
            str(u) for u in llvm_next.LLVM_NEXT_TESTING_URL_ALLOWLIST
        }:
            desc_parts.append(
                "Updated testing URL allowlist based on untrusted deps."
            )

        change_descriptions = "\n".join(f"- {x}" for x in desc_parts)

    logging.info("URL list changed; creating commit...")
    toolchain_utils_root = cros_paths.script_toolchain_utils_root()
    with git_utils.create_worktree(toolchain_utils_root) as worktree:
        write_url_list(
            worktree / "llvm_tools" / "llvm_next.py",
            new_manifest_cl,
            new_allowlist_urls,
        )
        sha = git_utils.commit_all_changes(
            worktree,
            message="\n".join(
                (
                    "llvm_tools: autoupdate CL list",
                    "",
                    change_descriptions,
                    "",
                    "BUG=None",
                    "TEST=CQ+1",
                )
            ),
        )
        logging.info("SHA of commit: %s", sha)
        if not opts.upload:
            return

        cl_list = git_utils.upload_to_gerrit(
            worktree,
            remote=git_utils.CROS_EXTERNAL_REMOTE,
            branch=git_utils.CROS_MAIN_BRANCH,
        )
        for cl in cl_list:
            git_utils.set_autoreview_topic_and_labels(toolchain_utils_root, cl)
