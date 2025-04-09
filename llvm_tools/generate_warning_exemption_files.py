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
import os
from pathlib import Path
import re
import subprocess
import textwrap
from typing import Generator, List

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


@dataclasses.dataclass(frozen=True)
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
    ^(?:[^:]*:\d+:\d+|clang-\d+)  # clang-N or the file location
    :\serror:\s                   # Nonfatal warnings need not apply.
    .*?\s+                        # Diagnostic message.
    \[(-W[^\][]+)\]\s*$           # List of warnings (likely incl. -Werror)
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


def create_go_file(
    builder: Builder,
    llvm_revision: int,
    fatal_warnings: List[FatalWarning],
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

    grouped_warnings = collections.defaultdict(list)
    for warning in fatal_warnings:
        grouped_warnings[(warning.category, warning.package_name)].append(
            warning.warning_name
        )

    for (category, package_name), warnings in sorted(grouped_warnings.items()):
        warnings.sort()

        wno_flags = ", ".join(f'"-Wno-{x}"' for x in warnings)
        case_stmt = textwrap.dedent(
            f"""\
            // Observed and suppressed on 1 builder during testing.
            // {builder.name}: {builder.url}
            case "{category}/{package_name}":
                return []string{{ {wno_flags} }}
            """
        )
        indented_case = textwrap.indent(case_stmt, "    ")
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
        "--warning-reports",
        type=Path,
        required=True,
        help="""
        Path to the root directory to scan for warning reports. This can be a
        board build directory (e.g., /build/amd64-generic), or the root of a
        place where a werror-logs tarball is unpacked.
        """,
    )
    parser.add_argument(
        "--builder-name",
        required=True,
        help="""
        Name of the builder that produced the report, e.g., amd64-generic-cq.
        """,
    )
    parser.add_argument(
        "--builder-url",
        required=True,
        help="""
        URL of the builder that produced the report, e.g.,
        https://ci.chromium.org/b/1234
        """,
    )
    opts = parser.parse_args(argv)

    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.DEBUG if opts.debug else logging.INFO,
    )

    builder = Builder(
        name=opts.builder_name,
        url=opts.builder_url,
    )
    fatal_warnings = parse_all_fatal_warnings(opts.warning_reports)
    file_contents = create_go_file(
        builder=builder,
        llvm_revision=opts.llvm_revision,
        fatal_warnings=fatal_warnings,
    )
    formatted_file = go_fmt_file(file_contents)
    opts.output.write_text(formatted_file, encoding="utf-8")
    logging.info("Generated file to %s.", opts.output)
