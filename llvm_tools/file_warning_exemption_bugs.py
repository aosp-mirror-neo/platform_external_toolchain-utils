# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Creates `bugged`-compatible files for warning exemptions.

This script takes a YAML file produced by `generate_warning_exemption_files.py`
(and hand-edited as necessary by you), resolves package components as much as
possible, and produces a series of files that can be fed into the `bugged` tool
to automatically file bugs requesting the fixing of the warnings.

At the end of this tool's execution, it prints a `bugged` command that you can
run outside of the chroot to file all of the bugs.
"""

import argparse
import json
import logging
import multiprocessing.pool
from pathlib import Path
import re
import subprocess
import sys
import textwrap
from typing import Generator

from cros_utils import bugs
from cros_utils import cros_paths
from llvm_tools import chroot
from llvm_tools import warning_exemption
import yaml  # pylint: disable=import-error


# Matches a `HOMEPAGE=` in an ebuild if the HOMEPAGE points to a CrOS repo.
CROS_EBUILD_HOMEPAGE_RE = re.compile(
    "^HOMEPAGE=['\"]"
    + re.escape("https://chromium.googlesource.com")
    + "/(chromiumos/[^'\"]+)['\"]",
    re.MULTILINE,
)

# Matches `/+/${ref}/` in a HOMEPAGE link.
CROS_EBUILD_HOMEPAGE_PLUS_REF_RE = re.compile(r"/\+/[^/]+/")

# Matches CROS_WORKON_PROJECT
CROS_EBUILD_CROS_WORKON_PROJECT_RE = re.compile(
    "CROS_WORKON_PROJECT=['\"]([^'\"]+)['\"]"
)

CROS_EBUILD_PLATFORM_SUBDIR_RE = re.compile(
    "PLATFORM_SUBDIR=['\"]([^'\"]+)['\"]"
)

CROS_WORKON_SUBTREE_RE = re.compile("CROS_WORKON_SUBTREE=['\"]([^'\"]+)['\"]")


def pluralize(num: int, s: str, **kwargs) -> str:
    """A correct-enough-for-this-script function to make strings plural.

    Examples:
        >>> f = lambda n: pluralize(n, "%(num)d bug%(plural)s found")
        >>> f(3)
        "3 bugs found"
        >>> f(1)
        "1 bug found"
    """
    return s % {
        "num": num,
        "plural": "" if num == 1 else "s",
        **kwargs,
    }


class RepoList:
    """Holds a mapping of remote repo paths to local.

    e.g., "external/github.com/llvm/llvm-project" =>
          "src/third_party/llvm-project"
    """

    def __init__(self, cros_root: Path, remote_to_local_map: dict[str, str]):
        self._cros_root = cros_root
        self._remote_to_local = remote_to_local_map

    @classmethod
    def new_from_repo(cls, cros_root: Path):
        # TODO: Ew
        repo_list = subprocess.run(
            ("repo", "list"),
            check=True,
            cwd=cros_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            encoding="utf-8",
        ).stdout.strip()
        # `repo list` prints lines like `src/path/from/repo/root : remote/path`
        remote_to_local = {}
        for line in repo_list.splitlines():
            local_path, remote_path = [x.strip() for x in line.split(" : ")]
            assert (
                local_path not in remote_to_local
            ), f"{local_path} mentioned multiple times"
            remote_to_local[remote_path] = local_path

        return cls(cros_root, remote_to_local)

    def lookup_local_path(self, lookup_path: str) -> Path | None:
        longest_match = None
        for remote_path in self._remote_to_local:
            if not lookup_path.startswith(remote_path):
                continue

            if not longest_match or len(remote_path) > len(longest_match):
                longest_match = remote_path

        if not longest_match:
            return None

        local_root = self._cros_root / self._remote_to_local[longest_match]
        local_subpath = lookup_path[len(longest_match) :].lstrip("/")
        return local_root / local_subpath


def format_warning_bug_body(
    exemption_file_name: str,
    crostc_contact: str,
    package: warning_exemption.Package,
    warnings: list[str],
    builders: list[warning_exemption.Builder],
) -> str:
    """Returns a suitable body for the given bug."""
    pieces: list[str] = []

    if len(warnings) == 1:
        pieces += (
            f"The {warnings[0]} warning is being suppressed in {package}.",
            " This would ordinarily be an error, but due to a recent toolchain",
            " upgrade, it's being treated as a warning.",
        )
    else:
        pieces += (
            f"The following warnings are being suppressed in {package}. These",
            " would normally be errors, but due to a recent toolchain upgrade,",
            " they're being treated as warnings:\n",
        )
        for w in warnings:
            pieces.append(f"\n- -W{w}")

    if builders:
        if len(builders) == 1:
            piece = f"These were observed on the {builders[0].name} builder."
        else:
            piece = pluralize(
                len(builders) - 1,
                f"These warnings were observed on the {builders[0].name} "
                f"builder, and %(num)d other%(plural)s.",
            )
        pieces += ("\n\n", piece)

    pieces.append("\n\n")
    docs_link = "http://go/crostc-warning-exemption"
    pieces.append(
        textwrap.dedent(
            f"""\
            **Remediation instructions** are available at
            {docs_link}#toolchain-user-landing.
            The instructions for fixing these warnings require a file name.
            That's `{exemption_file_name}`. If you want to jump straight to
            repro instructions, those are at
            {docs_link}#how-do-i-repro-the-original-error-and-verify-my-fix.

            If you have questions, please don't hesitate to contact
            {crostc_contact}@!
            """
        )
    )
    return "".join(pieces)


def format_bug(
    *,
    title: str,
    body: str,
    component: int,
    assignee: str | None,
    parent: int,
    priority: int = 2,
) -> str:
    """Turns args into a `bugged`-compatible bug report."""
    bug_pieces = [title, "\n\n", body, "\n\n"]
    metadata = [
        ("COMPONENT", str(component)),
        ("TYPE", "INTERNAL_CLEANUP"),
        ("PRIORITY", f"P{priority}"),
        ("SEVERITY", "S2"),
    ]

    if assignee:
        metadata.append(("ASSIGNEE", assignee))

    bug_pieces += (f"{key}={val}\n" for key, val in metadata)
    # PARENT does not support `=`, only `+=` and `-=`. Handle that here.
    bug_pieces.append(f"PARENT+={parent}\n")
    return "".join(bug_pieces)


def parse_input_yaml(input_yaml: Path) -> warning_exemption.YamlFile:
    with input_yaml.open(encoding="utf-8") as f:
        return warning_exemption.YamlFile.from_yaml(yaml.safe_load(f))


def scrape_component_from_dir_metadata_file(
    file_contents: str,
) -> int | None:
    # dirmd is a tool offered by `depot_tools`, and should be available inside
    # of the chroot. It helpfully converts these files to JSON.
    raw_metadata = json.loads(
        subprocess.run(
            ("dirmd", "parse", "-format=dir-metadata"),
            check=True,
            input=file_contents,
            encoding="utf-8",
            stdout=subprocess.PIPE,
        ).stdout
    )

    # The output of this is in the form:
    # {
    #   "stdin": {
    #     "json": {
    #       // Contents of the file.
    #     }
    #   }
    # }
    metadata = raw_metadata["stdin"]["json"]

    # This has both private and public components. Prefer private out of
    # caution.
    if buganizer := metadata.get("buganizer"):
        if c := buganizer.get("componentId"):
            # Note that the JSON is serialized with string components, instead
            # of integers.
            return int(c)

    if buganizer_public := metadata.get("buganizerPublic"):
        if c := buganizer_public.get("componentId"):
            return int(c)

    return None


def find_ebuild_dir_metadata_candidates(
    repo_list: RepoList,
    ebuild_path: Path,
) -> Generator[Path, None, None]:
    """Yields directories to check for DIR_METADATA files for the given ebuild.

    These are yielded in order of preference.
    """

    def dir_and_parents_in_same_repo(d: Path) -> Generator[Path, None, None]:
        if not d.exists():
            return

        while True:
            yield d
            if (d / ".git").exists():
                break
            d = d.parent

            # This should never break out of `.repo`, but just in case...
            assert d != d.root, "Somehow this got to /?"

    # Bundle of heuristics to find a DIR_METADATA file.
    #
    # Generally, what's tried is:
    # 1. Find an adjacent DIR_METADATA.
    # 2. Try to determine whether there's a path in a well-known repo with a
    #    nearby DIR_METADATA.
    # 3. Try the parent dir.
    #
    # Parent dir is dispreferred, since ${CATEGORY} level DIR_METADATA is likely
    # to be less precise than what exists in source code. Going to a parent of
    # the parent leads to things like chromiumos-overlay, which is probably
    # _even less_ precise.
    ebuild_dir = ebuild_path.parent
    yield ebuild_dir

    ebuild_contents = ebuild_path.read_text(encoding="utf-8")
    # HOMEPAGEs are often populated and point to a direct path in the CrOS repo.
    if m := CROS_EBUILD_HOMEPAGE_RE.search(ebuild_contents):
        subpath = m.group(1)
        if subpath_ref := CROS_EBUILD_HOMEPAGE_PLUS_REF_RE.search(subpath):
            subpath = subpath.replace(subpath_ref.group(0), "/")
        if local_path := repo_list.lookup_local_path(subpath):
            # HOMEPAGE can point to:
            # - A directory (in which case, it should be checked directly)
            # - A file (in which case, its parent should be checked)
            # - Something that DNE (in which case, if there's a '.', assume it's
            #   a file and check the parent. Otherwise, assume it's a directory
            #   that got deleted)
            if local_path.is_file() or (
                not local_path.exists() and "." in local_path.name
            ):
                local_path = local_path.parent
            yield from dir_and_parents_in_same_repo(local_path)

    # If this is a cros-workon ebuild that unambiguously reads from a single
    # repo, try that.
    if m := CROS_EBUILD_CROS_WORKON_PROJECT_RE.search(ebuild_contents):
        project_path = m.group(1)
        if local_path := repo_list.lookup_local_path(project_path):
            subdir = None
            if m := CROS_EBUILD_PLATFORM_SUBDIR_RE.search(ebuild_contents):
                subdir = m.group(1)
                yield from dir_and_parents_in_same_repo(local_path / subdir)
            elif "CROS_WORKON_SUBTREE=" not in ebuild_contents:
                yield local_path
            elif m := CROS_WORKON_SUBTREE_RE.search(ebuild_contents):
                # CROS_WORKON_SUBTREE may either be a string or an array. If
                # it's a single string, it may be a string-sep'ed list of
                # multiple repos. If it's just one repo, we're fine; otherwise,
                # there's ambiguity.
                subtrees = m.group(1).split()
                if len(subtrees) == 1:
                    yield from dir_and_parents_in_same_repo(
                        local_path / subtrees[0]
                    )

    yield ebuild_dir.parent


def try_read_dir_metadata_component_for_ebuild(
    repo_list: RepoList,
    ebuild_path: Path,
) -> int | None:
    for try_dir in find_ebuild_dir_metadata_candidates(repo_list, ebuild_path):
        logging.debug("dirmd: trying %s for %s", try_dir, ebuild_path)
        try:
            contents = (try_dir / "DIR_METADATA").read_text(encoding="utf-8")
        except FileNotFoundError:
            continue

        if c := scrape_component_from_dir_metadata_file(contents):
            return c
    return None


def resolve_package_component(
    cros_root: Path,
    repo_list: RepoList,
    package: warning_exemption.Package,
) -> int | None:
    """Resolves a buganizer component for a package."""
    logging.info("Resolving %s to a specific ebuild...", package)
    equery = subprocess.run(
        ("cros_sdk", "--enter", "--", "equery", "w", str(package)),
        check=False,
        encoding="utf-8",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if equery.returncode:
        logging.error(
            "Failed resolving ebuild for %s; skipping component checks", package
        )
        return None

    # Only grab the last line, in case `cros_sdk` outputs some kind of logging.
    chroot_ebuild_path = equery.stdout.strip().splitlines()[-1]
    chroot_src_root = str(cros_paths.CHROOT_SOURCE_ROOT) + "/"
    assert chroot_ebuild_path.startswith(
        chroot_src_root
    ), f"Ebuild path {chroot_ebuild_path} isn't rooted in {chroot_src_root}"

    ebuild_path = cros_root / chroot_ebuild_path[len(chroot_src_root) :]
    logging.debug("Resolved package %s to ebuild %s", package, ebuild_path)
    # File this in the component and expect it to be autoassigned or triaged
    # appropriately.
    logging.info("Trying to resolve owners for %s", package)
    c = try_read_dir_metadata_component_for_ebuild(repo_list, ebuild_path)
    if c:
        logging.info("Inferred component %d for %s", c, package)
    else:
        logging.info("Could not infer component for %s", package)
    return c


def resolve_all_package_components(
    cros_root: Path,
    repo_list: RepoList,
    packages: list[warning_exemption.Package],
) -> list[int | None]:
    """Resolves buganizer components for all given packages."""
    # resolve_package_component takes multiple seconds, mostly in subprocesses.
    # Threads cheaply allow for massive speedups.
    with multiprocessing.pool.ThreadPool() as pool:
        resolve = lambda x: resolve_package_component(cros_root, repo_list, x)
        return pool.map(resolve, packages)


def format_bug_from_package_warnings(
    exemption_file_name: str,
    parent_bug: int,
    crostc_contact: str,
    severe_warnings: set[str],
    package_warnings: warning_exemption.YamlPackageWarnings,
    component: int | None,
) -> str:
    warnings = package_warnings.warning_names
    package = package_warnings.package

    if not warnings:
        raise ValueError("No warnings given to format a bug out of")

    title = f"-W{warnings[0]}"
    if len(warnings) > 1:
        title += f" and {len(warnings)-1} others are"
    else:
        title += " is"
    title += f" being suppressed in {package}"

    return format_bug(
        title=title,
        body=format_warning_bug_body(
            exemption_file_name,
            crostc_contact,
            package,
            warnings,
            package_warnings.observed_on,
        ),
        component=component or bugs.INTERNAL_CROSTC_COMPONENT,
        assignee=None if component else crostc_contact,
        parent=parent_bug,
        priority=1 if any(x in severe_warnings for x in warnings) else 2,
    )


def format_bug_for_mage_followup(
    parent_bug: int,
    crostc_contact: str,
    per_package_warnings: list[warning_exemption.YamlPackageWarnings],
    frozen_per_package_warnings: list[warning_exemption.YamlPackageWarnings],
) -> str | None:
    def extract_warning_set(
        warning_list: list[warning_exemption.YamlPackageWarnings],
    ) -> set[tuple[warning_exemption.Package, str]]:
        result: set[tuple[warning_exemption.Package, str]] = set()
        for package_warnings in warning_list:
            result.update(
                (package_warnings.package, x)
                for x in package_warnings.warning_names
            )
        return result

    bugs_filed_for = extract_warning_set(per_package_warnings)
    bugs_needed_for = extract_warning_set(frozen_per_package_warnings)
    bugs_missing_for = sorted(bugs_needed_for - bugs_filed_for)
    if not bugs_missing_for:
        return None

    title = pluralize(
        len(bugs_missing_for),
        "Autofiled bugs are missing for %(num)d exemption%(plural)s.",
    )
    body_lines = [
        pluralize(
            len(bugs_missing_for),
            "Bug%(plural)s that were autofiled by "
            "file_warning_exemption_bugs.py did not include warnings for:",
        ),
        "",
    ]
    body_lines += (
        f"  - [ ] {warning_name} in {p.category}/{p.package_name}"
        for p, warning_name in bugs_missing_for
    )
    body_lines += (
        "\n",
        "Please follow up on these as appropriate, then close this bug out.",
    )

    return format_bug(
        title=title,
        body="\n".join(body_lines),
        component=bugs.INTERNAL_CROSTC_COMPONENT,
        assignee=crostc_contact,
        parent=parent_bug,
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--debug", action="store_true", help="Enable debug logging"
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="YAML produced by generate_warning_exemption_files.py.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="""
        Empty directory to output bugspecs in. This script asks you to run a
        command (outside of the chroot) to actually create bug reports; this is
        the directory you'll run it in. The `out-dir` will be created if it
        doesn't exist.
        """,
    )
    parser.add_argument(
        "--parent-bug",
        type=int,
        required=True,
        help="Parent bug of all bugs that are filed.",
    )
    parser.add_argument(
        "--crostc-contact",
        type=str,
        required=True,
        help="""
        The username of the Mage, e.g., 'gbiv'. Users will be instructed to
        contact this person with any questions about these bugs.
        """,
    )

    opts = parser.parse_args(argv)
    if "@" in opts.crostc_contact:
        parser.error("--crostc-contact should be a username, not an email")
    return opts


def verify_chroot_exists(chromeos_root: Path):
    logging.info(
        "Verifying that `sudo` credentials are fresh (this may prompt for "
        "your password)."
    )
    subprocess.run(
        ("sudo", "true"),
        check=True,
        stdin=subprocess.DEVNULL,
    )

    logging.info("Verifying the chroot exists and can be entered...")
    subprocess.run(
        ("cros_sdk", "--enter", "--", "true"),
        check=True,
        cwd=chromeos_root,
        stdin=subprocess.DEVNULL,
    )
    logging.info("OK")


def main(argv: list[str]) -> None:
    # This script uses repo, which is not supported within the chroot.
    chroot.VerifyOutsideChroot()
    cros_root = cros_paths.script_chromiumos_checkout_or_exit()

    opts = parse_args(argv)
    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.DEBUG if opts.debug else logging.INFO,
    )

    input_yaml: Path = opts.input
    parent_bug: int = opts.parent_bug
    out_dir: Path = opts.out_dir
    crostc_contact: str = opts.crostc_contact

    repo_list = RepoList.new_from_repo(cros_root)
    verify_chroot_exists(cros_root)

    yaml_file = parse_input_yaml(input_yaml)
    severe_warnings = set(yaml_file.severe_warnings)
    bugworthy_warnings = [
        x for x in yaml_file.per_package_warnings if x.warning_names
    ]
    components = resolve_all_package_components(
        cros_root, repo_list, packages=[x.package for x in bugworthy_warnings]
    )
    formatted_bugs = [
        format_bug_from_package_warnings(
            exemption_file_name=yaml_file.exemption_go_file_name,
            parent_bug=parent_bug,
            crostc_contact=crostc_contact,
            severe_warnings=severe_warnings,
            package_warnings=warnings,
            component=component,
        )
        for warnings, component in zip(bugworthy_warnings, components)
    ]

    if not formatted_bugs:
        sys.exit(
            "No bugs generated; either there's a bug in this script, or "
            "there's nothing to file"
        )

    out_dir.mkdir(exist_ok=True)
    if next(out_dir.iterdir(), None):
        sys.exit(f"{out_dir} is nonempty; refusing to overwrite anything.")

    file_zfill_len = len(str(len(formatted_bugs)))
    for i, formatted_bug in enumerate(formatted_bugs):
        file_name = f"{str(i).zfill(file_zfill_len)}.bugged"
        f = out_dir / file_name
        logging.debug("Writing bug %s...", f)
        f.write_text(formatted_bug, encoding="utf-8")

    if followup_bug := format_bug_for_mage_followup(
        parent_bug=parent_bug,
        crostc_contact=crostc_contact,
        per_package_warnings=yaml_file.per_package_warnings,
        frozen_per_package_warnings=yaml_file.frozen_per_package_warnings,
    ):
        f = out_dir / "followup-bug"
        logging.debug("Writing followup bug %s...", f)
        f.write_text(followup_bug, encoding="utf-8")

    logging.info(
        "Successfully resolved component for %d/%d packages",
        sum(bool(x) for x in components),
        len(components),
    )
    logging.info(
        "%d bug creation files%s written to %s",
        len(formatted_bugs),
        " (and one follow-up bug)" if followup_bug else "",
        out_dir,
    )
    logging.info(
        "If you're sure the above looks good, please `cd` into your "
        "output directory, and run the following command:\n"
        "bash -c 'for x in *; do bugged create --format=markdown < ${x} || "
        "break; done'"
    )


if __name__ == "__main__":
    main(sys.argv[1:])
