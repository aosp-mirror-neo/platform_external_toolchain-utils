# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Generate a Go file to exempt warnings from fatal_clang_warnings artifacts.

This is intended to be used to mass-exempt warnings for Mage rotations. The file
will contain one func like:

```
func warningSuppressionsForLLVM_rNN(packageNameAndCategory string) []string {
  // return `-Wno-*` flags required to make the given package build
}
```

Where NNN is the provided LLVM revision.
"""

import argparse
import collections
import dataclasses
import datetime
import json
import logging
import multiprocessing
import multiprocessing.pool
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import textwrap
from typing import DefaultDict, Generator, Iterable, Optional

from cros_utils import gs
from llvm_tools import cros_cls
from llvm_tools import llvm_next
from llvm_tools import warning_exemption


# It's a bit iffy to have a constant that's not completely a constant, but for
# simplicity's sake (esp. with tests, ...)
GO_COPYRIGHT_HEADER = f"""\
// Copyright {datetime.datetime.now().year} The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.
"""

# This parses two kinds of errors:
# 1. `clang-17: error: foo [-W...]`
# 2. `/file/path:123:45: error: foo [-W...]"
_FATAL_WARNING_RE = re.compile(
    r"""
    ^(?:([^:]*):\d+:\d+:\s|clang-\d+:\s)?  # clang-N or the file location
    error:\s                               # Nonfatal warnings need not apply.
    .*?\s+                                 # Diagnostic message.
    \[(-W[^\][]+)\]\s*$                    # List of warnings (likely incl.
                                           # -Werror)
    """,
    re.VERBOSE,
)


@dataclasses.dataclass(frozen=True, eq=True, order=True)
class FatalPackageWarning:
    """Represents a fatal warning recorded for a specific package."""

    # Package this happened in.
    package: warning_exemption.Package
    # Warning name, without `-W`. e.g., `all`, `extra`.
    warning_name: str


def absolutize_path(cwd: str, p: str) -> str:
    """Makes `p` into an absolute path, and normalizes it."""
    return os.path.normpath(os.path.join(cwd, p))


_BASH_STYLE_RE = re.compile(
    # All style sequences start with ESC
    "\x1b"
    # Then they can be one of:
    r"(?:"
    # - a single character
    r"[A-Za-z]"
    # - a '[', then a sequence of characters terminated in 'm'
    r"|\[[^m]*m"
    # - a ']', then a sequence of characters terminated in '\a' (BEL)
    r"|\][^\a]*"
    "\a)"
)


def remove_bash_style_sequences(s: str) -> str:
    """Removes all bash font style sequences from `s`."""
    return _BASH_STYLE_RE.sub("", s)


def scrape_fatal_warnings_from_stdout(
    stdout: str, absolutize_with_cwd: Optional[str] = None
) -> list[tuple[str, str]]:
    """Returns a list of fatal warnings scraped from `stdout`.

    Args:
        stdout: stdout to scrape
        absolutize_with_cwd: if provided and non-None, warning paths will be
            made absolute with `absolutize_with_cwd` being treated as CWD.

    Returns:
        A list of (full_warning_line, warning_name) for each warning in stdout.
    """
    warning_lines = set()
    lines_without_style = [
        remove_bash_style_sequences(x) for x in stdout.splitlines()
    ]
    for line in lines_without_style:
        m = _FATAL_WARNING_RE.fullmatch(line)
        if not m:
            continue

        warning_flags = m.group(2)
        warning_flags_no_werror = [
            x for x in warning_flags.split(",") if x != "-Werror"
        ]
        if len(warning_flags_no_werror) != 1:
            raise ValueError(
                f"Weird: parsed warnings {warning_flags_no_werror} out "
                f"of {line}"
            )

        warning_flag = warning_flags_no_werror[0]
        if not warning_flag.startswith("-W"):
            raise ValueError(
                f"Weird: parsed warning flag {warning_flag} without -W out "
                f"of {line}"
            )
        warning_flag_without_w = warning_flag[2:]

        fixed_line = line.strip()
        if absolutize_with_cwd is not None:
            if path_as_written := m.group(1):
                assert (
                    m.start(1) == 0
                ), f"Warning didn't start at beginning of line in {line}"
                fixed_path = absolutize_path(
                    absolutize_with_cwd, path_as_written
                )
                fixed_line = fixed_path + line[m.end(1) :]

        warning_lines.add((fixed_line, warning_flag_without_w))
    return sorted(warning_lines)


@dataclasses.dataclass(eq=True)
class FatalWarningGroup:
    """A grouping of fatal warning reports."""

    # Names of warnings, e.g., `alloca`
    warning_names: set[str] = dataclasses.field(default_factory=set)
    # Lines with fatal warnings, e.g.,
    # `foo/bar:12:34: error: baz [-Werror,-Wbaz]`,
    # with paths normalized where available.
    warning_lines: set[str] = dataclasses.field(default_factory=set)

    def add(self, other: "FatalWarningGroup"):
        self.warning_names |= other.warning_names
        self.warning_lines |= other.warning_lines


def parse_fatal_warnings_file(
    warnings_json_file: Path,
) -> Optional[tuple[warning_exemption.Package, FatalWarningGroup]]:
    logging.debug("Parsing warnings report: %s", warnings_json_file)
    with warnings_json_file.open(encoding="utf-8") as f:
        warnings_json = json.load(f)

    # The shape of warnings_json is:
    # {
    #   "cwd": "/path/to/directory",
    #   "command": ["ccache", "full", "compile", "command"],
    #   "stdout": "stdout/stderr of the build",
    #   "parent_process_data": [
    #     {
    #       "invocation": ["parent", "process", "command"],
    #       "env": ["ENV1=", "ENV2=value", ""]
    #     }
    #   ]
    # }
    parsed_warnings = scrape_fatal_warnings_from_stdout(
        warnings_json["stdout"], absolutize_with_cwd=warnings_json["cwd"]
    )
    if not parsed_warnings:
        logging.warning(
            "Could not scrape any fatal warning reports from %s; ignoring file",
            warnings_json_file,
        )
        return None

    # Hunt in parent process info for CATEGORY/PN env vars. Note that this isn't
    # guaranteed to be in the first parent
    for parent in warnings_json.get("parent_process_data", ()):
        parent_env = parent.get("env", ())
        category = ""
        package_name = ""
        for e in parent_env:
            if e.startswith("CATEGORY="):
                category = e.split("=", 1)[1]
            elif e.startswith("PN="):
                package_name = e.split("=", 1)[1]

        if category and package_name:
            break
    else:
        logging.error(
            "No CATEGORY/PN could be inferred for %s; ignoring file",
            warnings_json_file,
        )
        return None

    package = warning_exemption.Package(category, package_name)
    result_lines = {x for x, _ in parsed_warnings}
    result_names = {x for _, x in parsed_warnings}
    logging.debug(
        "Parsed %d unique fatal warning(s) for %s",
        len(result_lines),
        warnings_json_file,
    )
    return package, FatalWarningGroup(result_names, result_lines)


def find_all_warning_reports_in(root: Path) -> Generator[Path, None, None]:
    for dirpath_str, _, filenames in os.walk(root):
        dirpath = Path(dirpath_str)
        for filename in filenames:
            if filename.endswith(".json") and filename.startswith(
                "warnings_report"
            ):
                yield dirpath / filename


def parse_all_fatal_warnings(
    warning_reports: Path,
) -> DefaultDict[warning_exemption.Package, FatalWarningGroup]:
    logging.info("Parsing warning reports under %s", warning_reports)

    per_package_groups: DefaultDict[
        warning_exemption.Package, FatalWarningGroup
    ] = collections.defaultdict(FatalWarningGroup)
    for warning_report in find_all_warning_reports_in(warning_reports):
        parse_result = parse_fatal_warnings_file(warning_report)
        if not parse_result:
            continue
        package, warnings_group = parse_result
        per_package_groups[package].add(warnings_group)
    return per_package_groups


def create_exemption_comment_for_package(
    builders: list[warning_exemption.Builder],
) -> str:
    if not builders:
        return "// (No builder links were available for these exemptions)."

    if len(builders) == 1:
        commentary = "Observed and suppressed on 1 builder during testing."
    else:
        commentary = (
            f"Observed and suppressed on {len(builders)} builders "
            "during testing."
        )
    return textwrap.dedent(
        f"""\
        // {commentary}
        // e.g., {builders[0].name}: {builders[0].url}.
        """
    ).rstrip()


def group_warnings_per_package(
    fatal_warnings: dict[
        Optional[warning_exemption.Builder],
        dict[warning_exemption.Package, FatalWarningGroup],
    ],
) -> dict[
    warning_exemption.Package,
    tuple[FatalWarningGroup, set[warning_exemption.Builder]],
]:
    """Converts cmd output into file-creation-friendly input."""
    results: dict[
        warning_exemption.Package,
        tuple[FatalWarningGroup, set[warning_exemption.Builder]],
    ] = collections.defaultdict(lambda: (FatalWarningGroup(), set()))
    for builder, package_warnings in fatal_warnings.items():
        for package, warning_group in package_warnings.items():
            result_group, builders = results[package]
            result_group.add(warning_group)
            if builder:
                builders.add(builder)
    return results


def create_go_file(
    llvm_revision: int,
    per_package_warnings: dict[
        warning_exemption.Package,
        tuple[FatalWarningGroup, set[warning_exemption.Builder]],
    ],
) -> str:
    """Creates a file that parses as Go to ignore the given warnings.

    Note that this file is not guaranteed to be well-formatted; use of `go fmt`
    or similar is recommended.

    Args:
        llvm_revision: The LLVM revision to name the generated function after.
        per_package_warnings: A dictionary of
          {package: (warning_names, builders_observed_on)}. The builder
          collection may be empty, but warning_names may not.
    """
    func_name = f"warningSuppressionsForLLVM_r{llvm_revision}"
    header = textwrap.dedent(
        f"""\

        package main

        func {func_name}(packageNameAndCategory string) []string {{
        """
    )

    # List of pieces of the file to be "".join'ed. Keeps us from n^2 string
    # concat.
    file_pieces = [GO_COPYRIGHT_HEADER, header]
    if not per_package_warnings:
        file_pieces.append("    return nil\n}\n")
        return "".join(file_pieces)

    file_pieces.append("    switch packageNameAndCategory {\n")
    for package, (warning_group, builders) in sorted(
        per_package_warnings.items()
    ):
        wno_flags = ", ".join(
            f'"-Wno-{x}"' for x in sorted(warning_group.warning_names)
        )
        comment = create_exemption_comment_for_package(sorted(builders))
        case_stmt = textwrap.dedent(
            f"""\
            case "{package.category}/{package.package_name}":
                return []string{{ {wno_flags} }}
            """
        )
        indented_case = textwrap.indent(comment + "\n" + case_stmt, "    ")
        file_pieces.append(indented_case)

    file_pieces.append(
        textwrap.dedent(
            """\
                default:
                    return nil
                }
            }
            """
        )
    )
    return "".join(file_pieces)


def canonicalize_warning_lines(warning_lines: Iterable[str]) -> list[str]:
    """Canonicalizes warning lines... somewhat.

    At the moment, this just replaces `/build/${board_name}` with
    `/build/BOARD`, since multiple builders are likely to report the same
    warnings, and it's generally a nicer UX to merge them all into one.
    """
    build_board_re = re.compile(r"^/build/[^/]+/")
    fixed_lines = {
        build_board_re.sub("/build/BOARD/", line.strip())
        for line in warning_lines
    }
    return sorted(fixed_lines)


def create_yaml_file(
    go_file_name: str,
    per_package_warnings: dict[
        warning_exemption.Package,
        tuple[FatalWarningGroup, set[warning_exemption.Builder]],
    ],
) -> str:
    """Returns the contents of a YAML file generated from the arg."""
    all_warning_names = set()
    yaml_per_package_warnings = []
    for package, (warning_group, builders) in per_package_warnings.items():
        all_warning_names.update(warning_group.warning_names)
        yaml_per_package_warnings.append(
            warning_exemption.YamlPackageWarnings(
                package=package,
                warning_lines=canonicalize_warning_lines(
                    warning_group.warning_lines
                ),
                warning_names=sorted(warning_group.warning_names),
                observed_on=sorted(builders),
            )
        )
    yaml_per_package_warnings.sort()

    return warning_exemption.YamlFile(
        exemption_go_file_name=go_file_name,
        severe_warnings=sorted(all_warning_names),
        per_package_warnings=yaml_per_package_warnings,
        frozen_per_package_warnings=yaml_per_package_warnings,
    ).as_raw_yaml()


def cmd_local(
    opts: argparse.Namespace,
) -> dict[
    Optional[warning_exemption.Builder],
    dict[warning_exemption.Package, FatalWarningGroup],
]:
    """Implements the `local` subcommand."""
    builder = None
    if opts.builder_name:
        assert opts.builder_url
        builder = warning_exemption.Builder(
            name=opts.builder_name, url=opts.builder_url
        )

    fatal_warnings = parse_all_fatal_warnings(opts.warning_reports)
    return {builder: fatal_warnings}


def resolve_builder_artifacts(
    build_ids: list[int],
) -> list[tuple[warning_exemption.Builder, str]]:
    """Resolves build_ids into tuples of (builder, artifacts_gs_link).

    If any of the `build_ids` are cq-orchestrators, this will find their
    children and return the Builder/artifacts tuples for those instead.

    Raises:
        ValueError if any of the given build_ids had no associated artifacts.
        (That is, for cq-orchestrators, if _none_ of their children had
        artifacts that could be found).
    """
    results = []
    for build_id in build_ids:
        name, output = cros_cls.fetch_cq_orchestrator_or_board_builder(build_id)
        if isinstance(output, cros_cls.CQBoardBuilderOutput):
            logging.info("Finding artifacts for %d...", build_id)
            named_builders = [(name, build_id, output.artifacts_link)]
        else:
            logging.info("Finding child builders for %d...", build_id)
            child_builders = sorted(output.child_builders.items())
            sub_builders = cros_cls.CQBoardBuilderOutput.fetch_many(
                bid for _, bid in child_builders
            )
            named_builders = [
                (name, bid, output.artifacts_link)
                for (name, bid), output in zip(child_builders, sub_builders)
            ]

        found_any_artifacts = False
        for name, build_id, artifacts_link in named_builders:
            build_url = cros_cls.builder_url(build_id)
            if not artifacts_link:
                logging.warning("Ignoring %s; it had no artifacts", build_url)
                continue

            found_any_artifacts = True
            builder = warning_exemption.Builder(name=name, url=build_url)
            results.append((builder, artifacts_link))

        if not found_any_artifacts:
            raise ValueError(f"No artifacts found for {build_id} (or children)")
    return results


def fetch_and_unpack_fatal_warnings_tarballs(
    tmpdir: Path, builder_artifacts: str
) -> list[Path]:
    tmpdir.mkdir(parents=True, exist_ok=True)

    tarball_suffix = "fatal_clang_warnings.tar.xz"
    results = gs.ls(os.path.join(builder_artifacts, f"*.{tarball_suffix}"))

    unpack_dirs = []
    for i, result in enumerate(results):
        gs_path = result.gs_path
        tarball_target = tmpdir / f"{i}_{tarball_suffix}"
        logging.info(
            "Fetching fatal warnings from %s into %s...",
            gs_path,
            tarball_target,
        )
        gs_result = subprocess.run(
            (gs.GSUTIL, "cp", gs_path, tarball_target),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
        )

        if gs_result.returncode:
            logging.error(
                "Failed fetching %s; gs stderr: %r", gs_path, gs_result.stderr
            )
            gs_result.check_returncode()

        unpack_dir = tmpdir / f"{i}_unpack"
        unpack_dir.mkdir()
        subprocess.run(
            ("tar", "xaf", tarball_target),
            check=True,
            cwd=unpack_dir,
            stdin=subprocess.DEVNULL,
        )
        unpack_dirs.append(unpack_dir)
    return unpack_dirs


def cmd_builders(
    opts: argparse.Namespace,
) -> dict[
    Optional[warning_exemption.Builder],
    dict[warning_exemption.Package, FatalWarningGroup],
]:
    """Implements the `builders` subcommand."""
    builder_artifacts = resolve_builder_artifacts(opts.builder_id)

    tmpdir = Path(tempfile.mkdtemp(prefix="generate_warning_exemption_files"))
    cleanup_tmpdir = False
    try:
        unpack_actions = []
        with multiprocessing.pool.ThreadPool(opts.jobs) as pool:
            tasks = []
            for i, (builder, artifacts_url) in enumerate(builder_artifacts):
                subdir = tmpdir / str(i)
                tasks.append(
                    (
                        builder,
                        artifacts_url,
                        pool.apply_async(
                            fetch_and_unpack_fatal_warnings_tarballs,
                            (subdir, artifacts_url),
                        ),
                    )
                )

            for builder, artifacts_url, task in tasks:
                unpack_dirs = task.get()
                if not unpack_dirs:
                    logging.info(
                        "Builder %s had no fatal-warnings artifacts; skip",
                        builder.url,
                    )
                    continue
                unpack_actions.append((builder, artifacts_url, unpack_dirs))

        results: dict[
            Optional[warning_exemption.Builder],
            dict[warning_exemption.Package, FatalWarningGroup],
        ] = {}
        for builder, artifacts_url, unpack_dir_list in unpack_actions:
            builder_results: dict[
                warning_exemption.Package, FatalWarningGroup
            ] = collections.defaultdict(FatalWarningGroup)
            for unpack_dir in unpack_dir_list:
                for package, grp in parse_all_fatal_warnings(
                    unpack_dir
                ).items():
                    builder_results[package].add(grp)
            results[builder] = builder_results

        cleanup_tmpdir = not opts.keep_tempdir
    finally:
        if cleanup_tmpdir:
            logging.info(
                "Removing tempdir with builder artifacts at %s...", tmpdir
            )
            shutil.rmtree(tmpdir)
        else:
            logging.info("Leaving tempdir at %s to aid in debugging", tmpdir)

    return results


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parses flags for this program."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--debug", action="store_true", help="Enable debug logging"
    )
    parser.add_argument(
        "--llvm-revision",
        type=int,
        default=llvm_next.LLVM_NEXT_REV,
        help="""
        LLVM Revision to start exempting the warnings from. If unspecified,
        defaults to the llvm-next revision specified in llvm_tools/llvm_next.py.
        """,
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="`.go` file to output."
    )
    parser.add_argument(
        "--yaml-output",
        type=Path,
        help="""
        `.yaml` file to output, used as a later input to
        `file_warning_exemption_bugs.py`. If you plan to file bugs for people to
        fix the exempted warnings, **please** make sure to put this file in a
        safe place.
        """,
    )
    subparsers = parser.add_subparsers(required=True)

    # 'local' subcommand, for generating from local build logs.
    subp = subparsers.add_parser("local", help="Generate from local logs.")
    subp.set_defaults(func=cmd_local)
    subp.add_argument(
        "--warning-reports",
        type=Path,
        required=True,
        help="""
        Path to the root directory to scan for warning reports. This can be a
        board build directory (e.g., /build/amd64-generic), or the root of a
        place where a werror-logs tarball is unpacked.
        """,
    )
    subp.add_argument(
        "--builder-name",
        help="""
        Name of the builder that produced the report, e.g., amd64-generic-cq.
        Must be specified if --builder-url is.
        """,
    )
    subp.add_argument(
        "--builder-url",
        help="""
        URL of the builder that produced the report, e.g.,
        https://ci.chromium.org/b/1234. Must be specified if --builder-name is.
        """,
    )

    # 'builders' subcommand, for fetching artifacts from builders.
    subp = subparsers.add_parser(
        "builders",
        help="Fetch -Werror artifacts from builders; generate from those.",
    )
    subp.set_defaults(func=cmd_builders)
    subp.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=multiprocessing.cpu_count(),
        help="""
        How many log tarballs to fetch concurrently. Defaults to %(default)s.
        """,
    )
    subp.add_argument(
        "--keep-tempdir",
        action="store_true",
        help="Don't delete the tempdir where artifacts get fetched on success.",
    )
    subp.add_argument(
        "--builder-id",
        action="append",
        type=int,
        required=True,
        help="""
        Build ID of a builder to grab artifacts from. This can _either_ be a
        cq-orchestrator (for which all children will be scanned), or a single
        builder (e.g., amd64-generic-cq). This may be specified multiple times.
        """,
    )

    opts = parser.parse_args(argv)
    if opts.func is cmd_local:
        if bool(opts.builder_name) != bool(opts.builder_url):
            parser.error(
                "--builder-name must be specified if --builder-url is "
                "specified, and vice versa."
            )

    return opts


def go_fmt_file(contents: str) -> str:
    """Runs `gofmt` on the given Go file contents."""
    return subprocess.run(
        ("gofmt", "-s"),
        check=True,
        encoding="utf-8",
        input=contents,
        stdout=subprocess.PIPE,
    ).stdout


def main(argv: list[str]) -> None:
    opts = parse_args(argv)

    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.DEBUG if opts.debug else logging.INFO,
    )

    output: Path = opts.output
    fatal_warnings_per_package = group_warnings_per_package(opts.func(opts))
    go_file_contents = create_go_file(
        llvm_revision=opts.llvm_revision,
        per_package_warnings=fatal_warnings_per_package,
    )
    formatted_go_file = go_fmt_file(go_file_contents)
    output.write_text(formatted_go_file, encoding="utf-8")
    logging.info("Generated Go file to %s.", output)

    yaml_output: Optional[Path] = opts.yaml_output
    if not yaml_output:
        return

    yaml_file_contents = create_yaml_file(
        output.name, fatal_warnings_per_package
    )
    yaml_output.write_text(yaml_file_contents, encoding="utf-8")
    logging.info("Generated yaml file to %s.", yaml_output)
