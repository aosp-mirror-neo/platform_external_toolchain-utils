# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Generate a Go file to exempt warnings from fatal_clang_warnings artifacts.

This is intended to be used to mass-exempt warnings for Mage rotations. The file
will contain one func like:

```
func getWarningsForLLVM_rNNN(packageNameAndCategory string) []string {
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
from typing import DefaultDict, Dict, Generator, List, Optional, Set, Tuple

from cros_utils import gs
from llvm_tools import cros_cls
from llvm_tools import llvm_next


# It's a bit iffy to have a constant that's not completely a constant, but for
# simplicity's sake (esp. with tests, ...)
GO_COPYRIGHT_HEADER = textwrap.dedent(
    f"""\
    // Copyright {datetime.datetime.now().year} The ChromiumOS Authors
    // Use of this source code is governed by a BSD-style license that can
    // be found in the LICENSE file.
    """
)


@dataclasses.dataclass(frozen=True, eq=True, order=True)
class Builder:
    """Represents a concrete CQ builder invocation."""

    name: str
    url: str


@dataclasses.dataclass(frozen=True, eq=True, order=True)
class FatalWarning:
    """Represents a fatal warning recorded for a specific package."""

    # Warning name, without `-W`. e.g., `all`, `extra`.
    warning_name: str
    # Package `${CATEGORY}` this was observed in.
    category: str
    # Package `${PN}` this was observed in.
    package_name: str


# This parses two kinds of errors:
# 1. `clang-17: error: foo [-W...]`
# 2. `/file/path:123:45: error: foo [-W...]"
_FATAL_WARNING_RE = re.compile(
    r"""
    ^(?:[^:]*:\d+:\d+:\s|clang-\d+:\s)?  # clang-N or the file location
    error:\s                             # Nonfatal warnings need not apply.
    .*?\s+                               # Diagnostic message.
    \[(-W[^\][]+)\]\s*$                  # List of warnings (likely incl.
                                         # -Werror)
    """,
    re.VERBOSE,
)


def scrape_fatal_warning_names_from_stdout(stdout: str) -> List[str]:
    warning_names = set()
    for line in stdout.splitlines():
        m = _FATAL_WARNING_RE.fullmatch(line)
        if not m:
            continue

        warning_flags = m.group(1)
        individual_warning_flags = warning_flags.split(",")
        if "-Werror" not in individual_warning_flags:
            continue

        warning_flags_no_werror = [
            x for x in individual_warning_flags if x != "-Werror"
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
        warning_names.add(warning_flag_without_w)
    return sorted(warning_names)


def parse_fatal_warnings_file(warnings_json_file: Path) -> List[FatalWarning]:
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
    warning_names = scrape_fatal_warning_names_from_stdout(
        warnings_json["stdout"]
    )
    if not warning_names:
        logging.warning(
            "Could not scrape any fatal warning reports from %s; ignoring file",
            warnings_json_file,
        )
        return []

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
        return []

    results = [
        FatalWarning(
            warning_name=x,
            category=category,
            package_name=package_name,
        )
        for x in warning_names
    ]
    logging.debug(
        "Parsed %d unique fatal warning(s) for %s",
        len(results),
        warnings_json_file,
    )
    return results


def find_all_warning_reports_in(root: Path) -> Generator[Path, None, None]:
    for dirpath_str, _, filenames in os.walk(root):
        dirpath = Path(dirpath_str)
        for filename in filenames:
            if filename.endswith(".json") and filename.startswith(
                "warnings_report"
            ):
                yield dirpath / filename


def parse_all_fatal_warnings(warning_reports: Path) -> List[FatalWarning]:
    logging.info("Parsing warning reports under %s", warning_reports)

    # Collect these in a set, since multiple reports may refer to the same
    # warning, and dedup'ing that is nice.
    fatal_warnings = {
        x
        for warning_report in find_all_warning_reports_in(warning_reports)
        for x in parse_fatal_warnings_file(warning_report)
    }
    return sorted(fatal_warnings)


# N.B., This is a shallow `frozen=True`.
@dataclasses.dataclass(frozen=True)
class PackageWarnings:
    """Grouped warnings from a package & builders they came from."""

    warning_names: List[str] = dataclasses.field(default_factory=list)
    builders: Set[Builder] = dataclasses.field(default_factory=set)


def create_exemption_comment_for_package(builders: List[Builder]) -> str:
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


def create_go_file(
    llvm_revision: int,
    fatal_warnings: Dict[FatalWarning, List[Builder]],
) -> str:
    """Creates a file that parses as Go to ignore the given warnings.

    Note that this file is not guaranteed to be well-formatted; use of `go fmt`
    or similar is recommended.
    """
    func_name = f"getWarningsForLLVM_r{llvm_revision}"
    header = textwrap.dedent(
        f"""\

        package main

        func {func_name}(packageNameAndCategory string) []string {{
        """
    )

    # List of pieces of the file to be "".join'ed. Keeps us from n^2 string
    # concat.
    file_pieces = [GO_COPYRIGHT_HEADER, header]
    if not fatal_warnings:
        file_pieces.append("    return nil\n}\n")
        return "".join(file_pieces)

    file_pieces.append("    switch packageNameAndCategory {\n")

    grouped_warnings: DefaultDict[Tuple[str, str], PackageWarnings] = (
        collections.defaultdict(PackageWarnings)
    )
    for warning, builders in fatal_warnings.items():
        w = grouped_warnings[(warning.category, warning.package_name)]
        w.builders.update(builders)
        w.warning_names.append(warning.warning_name)

    for (category, package_name), warnings in sorted(grouped_warnings.items()):
        wno_flags = ", ".join(
            f'"-Wno-{x}"' for x in sorted(warnings.warning_names)
        )
        comment = create_exemption_comment_for_package(
            sorted(warnings.builders)
        )
        case_stmt = textwrap.dedent(
            f"""\
            case "{category}/{package_name}":
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


def cmd_local(
    opts: argparse.Namespace,
) -> Dict[FatalWarning, List[Builder]]:
    """Implements the `local` subcommand."""
    builders = []
    if opts.builder_name:
        assert opts.builder_url
        builders.append(Builder(name=opts.builder_name, url=opts.builder_url))

    fatal_warnings = parse_all_fatal_warnings(opts.warning_reports)
    return {x: builders for x in fatal_warnings}


def resolve_builder_artifacts(
    build_ids: List[int],
) -> List[Tuple[Builder, str]]:
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
            builder = Builder(name=name, url=build_url)
            results.append((builder, artifacts_link))

        if not found_any_artifacts:
            raise ValueError(f"No artifacts found for {build_id} (or children)")
    return results


def fetch_and_unpack_fatal_warnings_tarball(
    tmpdir: Path, builder_artifacts: str
) -> Optional[Path]:
    tmpdir.mkdir(parents=True, exist_ok=True)

    tarball_suffix = "fatal_clang_warnings.tar.xz"
    results = gs.ls(os.path.join(builder_artifacts, f"*.{tarball_suffix}"))
    if not results:
        return None
    if len(results) > 1:
        raise ValueError(
            f"Builder at {builder_artifacts} had {len(results)} warnings "
            "tarballs; expected one"
        )

    gs_path = results[0].gs_path
    tarball_target = tmpdir / tarball_suffix
    logging.info("Fetching fatal warnings from %s...", gs_path)
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

    unpack_dir = tmpdir / "unpack"
    unpack_dir.mkdir()
    subprocess.run(
        ("tar", "xaf", tarball_target),
        check=True,
        cwd=unpack_dir,
        stdin=subprocess.DEVNULL,
    )
    return unpack_dir


def cmd_builders(
    opts: argparse.Namespace,
) -> Dict[FatalWarning, List[Builder]]:
    """Implements the `builders` subcommand."""
    builder_artifacts = resolve_builder_artifacts(opts.builder_id)

    tmpdir = Path(tempfile.mkdtemp(prefix="generate_warning_exemption_files"))
    cleanup_tmpdir = False
    warnings_dict = collections.defaultdict(list)
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
                            fetch_and_unpack_fatal_warnings_tarball,
                            (subdir, artifacts_url),
                        ),
                    )
                )

            for builder, artifacts_url, task in tasks:
                unpack_dir = task.get()
                if not unpack_dir:
                    logging.info(
                        "Builder %s had no fatal-warnings artifact; skip",
                        builder.url,
                    )
                    continue
                unpack_actions.append((builder, artifacts_url, unpack_dir))

        for builder, artifacts_url, unpack_dir in unpack_actions:
            for fatal_warning in parse_all_fatal_warnings(unpack_dir):
                warnings_dict[fatal_warning].append(builder)

        cleanup_tmpdir = not opts.keep_tempdir
    finally:
        if cleanup_tmpdir:
            logging.info(
                "Removing tempdir with builder artifacts at %s...", tmpdir
            )
            shutil.rmtree(tmpdir)
        else:
            logging.info("Leaving tempdir at %s to aid in debugging", tmpdir)

    # Sort the builders for consistency
    for builder_list in warnings_dict.values():
        builder_list.sort()

    return warnings_dict


def parse_args(argv: List[str]) -> argparse.Namespace:
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


def main(argv: List[str]) -> None:
    opts = parse_args(argv)

    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.DEBUG if opts.debug else logging.INFO,
    )

    fatal_warnings: Dict[FatalWarning, List[Builder]] = opts.func(opts)
    file_contents = create_go_file(
        llvm_revision=opts.llvm_revision,
        fatal_warnings=fatal_warnings,
    )
    formatted_file = go_fmt_file(file_contents)
    opts.output.write_text(formatted_file, encoding="utf-8")
    logging.info("Generated file to %s.", opts.output)
