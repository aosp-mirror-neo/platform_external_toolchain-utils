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

from cros_utils import gerrit_utils
from llvm_tools import manifest_utils


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


def _generate_bash_cherry_pick_commands(
    cherry_picks: list[CherrypickDesc],
    project_mappings: dict[str, str],
    android_tree: Path,
    topic: str,
    tag_or_branch: TagOrBranch | None,
) -> list[str]:
    """Generates the cherry-pick commands."""
    commands = [f"# Cherry-pick commands for topic: {topic}"]

    project_to_picks = collections.defaultdict(list)
    for pick in cherry_picks:
        project_to_picks[pick.project].append(pick)

    # Sort projects by name for determinism in output
    for project in sorted(project_to_picks.keys()):
        picks = project_to_picks[project]
        # picks are now in correct dependency order
        project_path = project_mappings.get(project)
        if not project_path:
            logging.warning(
                "Project %s not found in manifest, skipping.", project
            )
            continue

        full_path = android_tree / project_path
        quoted_path = shlex.quote(str(full_path))
        for pick in picks:
            commands.append(f"(cd {quoted_path} && {pick.cherrypick_command})")

        if tag_or_branch:
            cmd = " ".join(shlex.quote(x) for x in tag_or_branch.get_command())
            commands.append(f"(cd {quoted_path} && {cmd})")

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
        default=gerrit_utils.ANDROID_INTERNAL_GERRIT_HOST,
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

    cls = gerrit_utils.fetch_cls_for_topic(opts.gerrit_host, opts.topic)
    if not cls:
        logging.info("No open CLs found for topic %s", opts.topic)
        return 0

    def fetch_command_for_change(
        change: gerrit_utils.CLDetails,
    ) -> CherrypickDesc | None:
        """Fetches cherry-pick for a change, returning a CherrypickDesc."""
        command = gerrit_utils.fetch_cherry_pick_command(
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
        cls = gerrit_utils.resolve_and_sort_cl_dependencies(
            cls, opts.gerrit_host, executor
        )
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
