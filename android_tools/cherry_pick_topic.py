# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Implements logic to find CLs in a topic."""

import argparse
import collections
import concurrent.futures
import dataclasses
import logging
from pathlib import Path
import shlex
import subprocess
import threading
import urllib.parse

from android_tools import gerrit_utils
from llvm_tools import manifest_utils


@dataclasses.dataclass(frozen=True)
class CLDetails:
    """CL details fetched from Gerrit."""

    project: str
    cl_number: int


def resolve_and_sort_cl_dependencies(
    cls: list[CLDetails],
    gerrit_host: str,
    executor: concurrent.futures.ThreadPoolExecutor,
) -> list[CLDetails]:
    """Resolves and sorts all CL dependencies."""
    # So Gerrit's relation chains list every CL with some sort of parent-child
    # relationship to any other CL in the same relation chain.
    #
    # That means if we have a tree of CLs:
    #   - A is the parent of B
    #   - A is the parent of C
    #   - C is the parent of D
    #
    # Then getting the relation chain for _any_ of these CLs will get the
    # relation chain for _all_ of these CLs. The ordering in this list of B and
    # C will be indeterminate, _but_ since A is the central parent, it is
    # guaranteed to be before all of the other CLs (and C is guaranteed to be
    # before D).
    #
    # The idea here is then pretty simple: grab all unique relation chains
    # (where an empty relation chain for CL E is just a relation chain of E),
    # chop out obviously unnecessary entries, and then return a flattened list
    # of relation chains.
    #
    # The "unnecessary entries" are elements that extend past the end of any
    # element in `cls`. So going back to the above example, if `cls` just
    # contained C, we would capture either [A, B, C], or [A, C], depending on
    # how Gerrit sorted it.
    #
    # TODO: This _does_ mean that B _may_ be included when it shouldn't, but
    # scanning to figure that out is a bit of a pain.

    # All of this state is protected by `lock`.
    cl_map = {cl.cl_number: cl for cl in cls}
    processed_cl_numbers = set()
    all_chains: list[list[CLDetails]] = []

    lock = threading.Lock()

    def _fetch_dep_chain(cl_detail: CLDetails) -> None:
        """Fetches the dependency chain for the given CL.

        Updates captured state above appropriately.
        """
        with lock:
            # As mentioned above, multiple CLs in the same chain will return the
            # same chain. Skip this request if we've seen this CL during another
            # request.
            if cl_detail.cl_number in processed_cl_numbers:
                return

        chain_info = gerrit_utils.fetch_related_changes(
            gerrit_host, cl_detail.cl_number
        )

        # Note that chains are returned in order from children to parents. For
        # simplicity later, make it parents-first.
        chain_info.reverse()

        with lock:
            # It could be that we had racing `fetch_related_changes` invocations
            # for the same chain; bail if we've already processed this chain.
            if cl_detail.cl_number in processed_cl_numbers:
                return

            if not chain_info:
                processed_cl_numbers.add(cl_detail.cl_number)
                all_chains.append([cl_detail])
                return

            # Truncate the chain to the child-most CL that was actually
            # requested.
            child_most_cl_idx = next(
                (
                    i
                    for i in reversed(range(len(chain_info)))
                    if chain_info[i].cl_number in cl_map
                ),
                None,
            )
            assert child_most_cl_idx is not None, (
                "Could not find any of the requested CLs in the relation "
                f"chain for {cl_detail.cl_number}."
            )
            del chain_info[child_most_cl_idx + 1 :]

            processed_cl_numbers.update(c.cl_number for c in chain_info)

            current_chain: list[CLDetails] = []
            for related_cl_info in chain_info:
                status = related_cl_info.status
                if not status.is_open():
                    logging.info(
                        "Skipping CL %d with status %s from a relation chain.",
                        related_cl_info.cl_number,
                        status.value,
                    )
                    continue

                cl_to_add = cl_map.get(related_cl_info.cl_number)
                if not cl_to_add:
                    logging.info(
                        "Discovered new, unmerged CL %d from relation chain "
                        "of %d",
                        related_cl_info.cl_number,
                        cl_detail.cl_number,
                    )
                    cl_to_add = CLDetails(
                        project=related_cl_info.project,
                        cl_number=related_cl_info.cl_number,
                    )
                    cl_map[related_cl_info.cl_number] = cl_to_add

                current_chain.append(cl_to_add)

            if current_chain:
                all_chains.append(current_chain)

    logging.info("Resolving CL dependencies using relation chains...")
    futures = [executor.submit(_fetch_dep_chain, cl) for cl in cls]
    for f in futures:
        # `f.result()` reraises any exception the future encountered.
        f.result()

    # The chain ordering will be deterministic (sourced from Gerrit), but
    # threads will race to add to this list. Sort by the CL number for
    # determinism.
    all_chains.sort(key=lambda chain: chain[0].cl_number)
    logging.debug("Final CL chains after parent resolution: %s", all_chains)
    return [cl for chain in all_chains for cl in chain]


def fetch_cls_for_topic(gerrit_host: str, topic: str) -> list[CLDetails]:
    """Fetches CL details for a given topic."""
    # https://gerrit-review.googlesource.com/Documentation/rest-api-changes.html#list-changes
    # Include `is:open` under the assumption that merged/abandoned things are
    # undesirable to cherrypick.
    encoded_query = urllib.parse.urlencode({"q": f'topic:"{topic}" is:open'})
    url = f"{gerrit_host}/changes/?{encoded_query}"
    response_body = gerrit_utils.fetch_gob_curl_body_with_retries(url)

    changes = gerrit_utils.parse_gerrit_response(response_body)
    results = []
    for change in changes:
        project = change.get("project")
        cl_number = change.get("_number")
        if not project or not cl_number:
            logging.warning("Change %s is missing project or number", change)
            continue
        results.append(CLDetails(project=project, cl_number=cl_number))
    return results


@dataclasses.dataclass(frozen=True)
class CherrypickDesc:
    """Describes a cherry-pick to be performed."""

    project: str
    cherrypick_command: str
    cl_number: int


@dataclasses.dataclass(frozen=True)
class TagOrBranch:
    """Describes a tag or branch to be applied."""

    tag: str | None = None
    branch: str | None = None

    def __post_init__(self) -> None:
        if bool(self.tag) == bool(self.branch):
            raise ValueError("Exactly one of tag or branch must be set.")

    def get_command(self) -> tuple[str, ...]:
        """Gets the command to be executed."""
        if self.tag:
            return ("git", "tag", "-f", self.tag)
        assert self.branch, "__post_init__ verifies this"
        return ("repo", "start", "--head", self.branch, ".")

    def run_command(self, project: str, full_path: Path) -> bool:
        """Runs the command, returning whether it was successful."""
        cmd = self.get_command()
        if self.tag:
            verb = "Tagging"
            name = self.tag
        else:
            verb = "Starting branch"
            assert self.branch, "__post_init__ verifies this"
            name = self.branch

        logging.info("%s %s with %s", verb, project, name)
        try:
            subprocess.run(
                cmd,
                check=True,
                cwd=full_path,
                encoding="utf-8",
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            return True
        except subprocess.CalledProcessError as e:
            logging.error(
                "Failed to %s %s in %s: %s",
                "tag" if self.tag else "start branch",
                name,
                project,
                e.stdout,
            )
            return False


def _get_cherry_pick_command(fetch_info: dict) -> str | None:
    """Gets the cherry-pick command from the fetch_info dictionary."""

    def try_get_cmd(key: str) -> str | None:
        if obj := fetch_info.get(key):
            if commands := obj.get("commands"):
                if cherry_pick := commands.get("Cherry Pick"):
                    return cherry_pick
        return None

    # Prefer sso if possible, since that's git.
    if cmd := try_get_cmd("sso"):
        return cmd

    # Otherwise, take what we can get.
    for key in fetch_info:
        if cmd := try_get_cmd(key):
            return cmd

    return None


def fetch_cherry_pick_command(gerrit_host: str, change_id: str) -> str | None:
    """Fetches the cherry-pick command for a given change."""
    logging.info("Fetching cherry-pick command for %s", change_id)
    encoded_params = urllib.parse.urlencode({"o": "DOWNLOAD_COMMANDS"})
    url = (
        f"{gerrit_host}/a/changes/{change_id}/revisions/current"
        f"?{encoded_params}"
    )
    response_body = gerrit_utils.fetch_gob_curl_body_with_retries(url)

    revision_details = gerrit_utils.parse_gerrit_response(response_body)

    fetch_info = revision_details.get("fetch")
    if not fetch_info:
        logging.warning("No fetch_info for %s", change_id)
        return None

    command = _get_cherry_pick_command(fetch_info)
    if command:
        logging.info(
            "Successfully fetched cherry-pick command for %s", change_id
        )
        return command

    logging.warning("No Cherry-Pick command found for %s", change_id)
    return None


def _generate_bash_cherry_pick_commands(
    cherry_picks: list[CherrypickDesc],
    project_mappings: dict[str, str],
    android_tree: Path,
    topic: str,
    tag_or_branch: TagOrBranch | None,
) -> list[str]:
    """Generates the cherry-pick commands."""
    commands = [f"# Cherry-pick commands for topic: {topic}"]

    # Sort these by project so we can just print a tag command at the end of a
    # project.
    cherry_picks = sorted(cherry_picks, key=lambda x: (x.project, x.cl_number))
    last_cherry_pick_path: Path | None = None

    def note_cherry_pick_path(p: Path | None) -> None:
        """Note a cherry-pick will be performed at `p`.

        If `p` is None, no more cherry-picks will be performed.
        """
        nonlocal last_cherry_pick_path
        if last_cherry_pick_path and last_cherry_pick_path != p:
            if tag_or_branch:
                cmd = " ".join(
                    shlex.quote(x) for x in tag_or_branch.get_command()
                )
                commands.append(
                    f"(cd {shlex.quote(str(last_cherry_pick_path))} && {cmd})"
                )

        last_cherry_pick_path = p

    for pick in cherry_picks:
        project_path = project_mappings.get(pick.project)
        if not project_path:
            logging.warning(
                "Project %s not found in manifest, skipping.", pick.project
            )
            continue

        full_path = android_tree / project_path
        note_cherry_pick_path(full_path)
        commands.append(
            f"(cd {shlex.quote(str(full_path))} && {pick.cherrypick_command})"
        )

    note_cherry_pick_path(None)
    return commands


@dataclasses.dataclass(frozen=True)
class CherryPickResult:
    """Result of a single cherry-pick operation."""

    was_successful: bool
    abort_failed: bool


def _run_cherry_pick_command(
    pick: CherrypickDesc, full_path: Path, project_path: str
) -> CherryPickResult:
    """Runs a single cherry-pick command.

    Returns:
        A CherryPickResult. `was_successful` is true if the CL was applied (or
        already has been). `abort_failed` is true if a cherry-pick was
        needed, it failed, and `git cherry-pick --abort` also failed.
    """
    try:
        subprocess.run(
            pick.cherrypick_command,
            check=True,
            cwd=full_path,
            # Gerrit gives us `shell=True` commands.
            shell=True,
            encoding="utf-8",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        return CherryPickResult(was_successful=True, abort_failed=False)
    except subprocess.CalledProcessError as e:
        is_already_applied = "nothing to commit, working tree clean" in e.stdout
        # Cherry-picks may already be applied (e.g., CLs that have
        # already been merged, or CLs that mirror other CLs for
        # isolated testing).
        #
        # In any case, just log it as a nonfatal error; the intent
        # here is making sure all patches are integrated into the
        # user's tree, and this one clearly is.
        if is_already_applied:
            logging.info(
                "Cherry-pick for %s failed; ag/%d already applied.",
                project_path,
                pick.cl_number,
            )
            return CherryPickResult(was_successful=True, abort_failed=False)

        logging.error(
            "Cherry-picking ag/%d for %s failed; aborting.",
            pick.cl_number,
            project_path,
        )
        # This should always succeed; if not, there's nothing we can
        # really do...
        res = subprocess.run(
            ("git", "cherry-pick", "--abort"),
            check=False,
            cwd=full_path,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
        )
        if res.returncode:
            logging.error(
                "git cherry-pick --abort failed for ag/%d: %s",
                pick.cl_number,
                res.stdout,
            )
            return CherryPickResult(was_successful=False, abort_failed=True)

        return CherryPickResult(was_successful=False, abort_failed=False)


def _cherry_pick_project(
    project: str,
    cherry_picks_for_project: list[CherrypickDesc],
    project_mappings: dict[str, str],
    android_tree: Path,
    tag_or_branch: TagOrBranch | None,
) -> tuple[bool, list[CherrypickDesc]]:
    """Cherry-picks all commands for a single project."""
    project_failures = []
    project_path = project_mappings.get(project)
    any_nonfatal_failures = False
    if not project_path:
        logging.warning("Project %s not found in manifest, skipping.", project)
        return False, []

    full_path = android_tree / project_path
    any_successful_pick = False
    repo_in_bad_state = False
    for i, pick in enumerate(cherry_picks_for_project, 1):
        logging.info(
            "Running cherry-pick %d/%d in %s: ag/%d",
            i,
            len(cherry_picks_for_project),
            project_path,
            pick.cl_number,
        )
        logging.debug(
            "Cherry-pick command for ag/%d: %s",
            pick.cl_number,
            pick.cherrypick_command,
        )
        result = _run_cherry_pick_command(pick, full_path, project_path)
        if result.was_successful:
            any_successful_pick = True
        else:
            project_failures.append(pick)

        if result.abort_failed:
            any_nonfatal_failures = True
            # If aborting the cherry-pick failed, it's unclear what can be done
            # to fix that. Just indicate we shouldn't touch the repo further and
            # bubble the error up.
            repo_in_bad_state = True
            break

    if (
        tag_or_branch
        and any_successful_pick
        and not repo_in_bad_state
        and not tag_or_branch.run_command(project, full_path)
    ):
        any_nonfatal_failures = True

    return any_nonfatal_failures, project_failures


def _execute_cherry_pick_commands(
    executor: concurrent.futures.ThreadPoolExecutor,
    cherry_picks: list[CherrypickDesc],
    project_mappings: dict[str, str],
    android_tree: Path,
    tag_or_branch: TagOrBranch | None,
) -> tuple[bool, list[CherrypickDesc]]:
    """Executes the cherry-pick commands, returning a list of failures."""
    projects_to_cherry_picks = collections.defaultdict(list)
    for pick in cherry_picks:
        projects_to_cherry_picks[pick.project].append(pick)

    future_to_project = {
        executor.submit(
            _cherry_pick_project,
            project,
            picks,
            project_mappings,
            android_tree,
            tag_or_branch,
        ): project
        # While not all cherry-picks take the same time, generally
        # speaking, projects with more cherries to pick will take longer to
        # execute on those. `submit` those first in hopes it makes better use of
        # our executor.
        for project, picks in sorted(
            projects_to_cherry_picks.items(),
            key=lambda x: len(x[1]),
            reverse=True,
        )
    }

    failures = []
    any_nonfatal_failures = False
    for future in concurrent.futures.as_completed(future_to_project):
        project_nonfatal_failures, project_failures = future.result()
        any_nonfatal_failures = (
            any_nonfatal_failures or project_nonfatal_failures
        )
        failures.extend(project_failures)

    return any_nonfatal_failures, failures


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--debug", action="store_true", help="Enable debug logging"
    )
    parser.add_argument(
        "--gerrit-host",
        default=gerrit_utils.INTERNAL_GERRIT_HOST,
        help="Gerrit host to query",
    )
    parser.add_argument(
        "--topic",
        required=True,
        help="The gerrit topic to query.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print cherry-pick commands instead of executing them.",
    )
    tag_or_branch = parser.add_mutually_exclusive_group()
    tag_or_branch.add_argument(
        "--tag",
        help="An optional git tag to apply to each repo with cherry-picks, "
        "after all cherry-picks have been performed. If the tag exists, "
        "it will be overwritten.",
    )
    tag_or_branch.add_argument(
        "--branch",
        help="An optional branch to create in each repo with cherry-picks, "
        "using `repo start`. The branch is created before any "
        "cherry-picks are attempted.",
    )
    # branches.
    parser.add_argument(
        "--android-tree",
        type=Path,
        required=True,
        help="Path to an Android tree. Project paths will be resolved from "
        "the manifest.",
    )
    opts = parser.parse_args(argv)
    return opts


def main(argv: list[str]) -> int:
    opts = parse_args(argv)

    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.DEBUG if opts.debug else logging.INFO,
    )

    project_mappings = {}
    android_tree = opts.android_tree.resolve()
    if not (android_tree / ".repo").is_dir():
        logging.error(
            "--android-tree %s does not appear to be an Android tree "
            "(missing .repo directory)",
            android_tree,
        )
        return 1

    manifest_file = android_tree / ".repo" / "manifests" / "default.xml"
    if not manifest_file.is_file():
        logging.error("Manifest file not found at %s", manifest_file)
        return 1

    project_mappings = {
        m.project_name: m.project_path
        for m in manifest_utils.read_manifest_project_mappings(manifest_file)
    }
    logging.info("Successfully parsed manifest from %s", manifest_file)
    if opts.debug:
        for name, path in project_mappings.items():
            logging.debug("Found project %s at path %s", name, path)

    cls = fetch_cls_for_topic(opts.gerrit_host, opts.topic)
    if not cls:
        logging.info("No open CLs found for topic %s", opts.topic)
        return 0

    def fetch_command_for_change(change: CLDetails) -> CherrypickDesc | None:
        """Fetches cherry-pick for a change, returning a CherrypickDesc."""
        command = fetch_cherry_pick_command(
            opts.gerrit_host, str(change.cl_number)
        )
        if command:
            return CherrypickDesc(
                project=change.project,
                cherrypick_command=command,
                cl_number=change.cl_number,
            )
        return None

    tag_or_branch = None
    if opts.tag or opts.branch:
        tag_or_branch = TagOrBranch(tag=opts.tag, branch=opts.branch)

    # 8 is arbitrary, but should provide a significant speedup while being
    # mindful of Gerrit ratelimits (each thread is expected to perform at most
    # one Gerrit operation at a time).
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        cls = resolve_and_sort_cl_dependencies(cls, opts.gerrit_host, executor)
        results = executor.map(fetch_command_for_change, cls)
        cherry_picks = [pick for pick in results if pick]

        if not cherry_picks:
            logging.info(
                "No cherry-pick commands found for topic %s", opts.topic
            )
            return 0

        if opts.dry_run:
            commands = _generate_bash_cherry_pick_commands(
                cherry_picks=cherry_picks,
                project_mappings=project_mappings,
                android_tree=opts.android_tree,
                topic=opts.topic,
                tag_or_branch=tag_or_branch,
            )
            print("\n".join(commands))
            return 0

        any_nonfatal_failures, failures = _execute_cherry_pick_commands(
            executor=executor,
            cherry_picks=cherry_picks,
            project_mappings=project_mappings,
            android_tree=opts.android_tree,
            tag_or_branch=tag_or_branch,
        )
        if failures:
            logging.error("The following cherry-picks failed:")
            for pick in failures:
                logging.error(
                    "  -  %s (ag/%d): %s",
                    pick.project,
                    pick.cl_number,
                    pick.cherrypick_command,
                )
            return 1

        if any_nonfatal_failures:
            logging.error("Encountered non-fatal failures; see errors above.")
            return 1
    return 0
