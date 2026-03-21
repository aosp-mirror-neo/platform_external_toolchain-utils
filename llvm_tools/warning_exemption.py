# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Common code for exempting compiler warnings and filing bugs about them.

Want to see what the YAML looks like in practice? Run this as a script; no flags
necessary.
"""

import collections
import dataclasses
import json
import logging
import os
from pathlib import Path
import re
from typing import Any, DefaultDict, Generator

import yaml  # pylint: disable=import-error


YAML_FILE_HEADER = """\
# META:
# This YAML file is made to be passed to `file_warning_exemption_bugs.py`. In
# this file, you have a lot of context about the warnings that have been
# exempted by the `generate_warning_exemption_files.py` script.
#
# Please look through the below entries and amend them (as necessary) per the
# instructions for each section.
"""

YAML_GO_FILE_SUBHEADER = """\
# This is the _name_ of the Go file that users should edit to remove exemptions.
# Please note that instructions already direct them to the
# sys-devel/llvm/files/compiler_wrapper directory.
"""

YAML_SEVERITY_SUBHEADER = """\
# INSTRUCTIONS: Please look at the warnings below and determine if they're
# high-severity. The ones that are _not_, please remove from the list. The ones
# that _are_, please leave there.
#
# High-severity loosely means "should be addressed quickly and at a high
# priority," rather than "would be nice to address, but likely isn't actively
# hurting anything." An example of a high-severity warning would be a warning
# that indicates a likely use-after-free, buffer overrun, etc.
"""

YAML_WARNING_LIST_SUBHEADER = """\
# INSTRUCTIONS: For each package below, please determine if the displayed
# warnings should be filed in a bug against the OWNERS of the packages.
#
# The bugs filed will contain:
# - the package name in which the fatal warnings were observed,
# - a log to an arbitrary builder where these were observed.
#
# They will _not_ contain the literal warning text you see in front of you, as
# those warnings may come from `#include`d internal headers.
"""

YAML_ORIGINAL_WARNING_LIST_SUBHEADER = """\
################################################################################
# Please, as a rule, **do not touch** the below. It's used for consistency
# checking by `file_warning_exemption_bugs.py`.
################################################################################
"""


@dataclasses.dataclass(frozen=True, eq=True, order=True)
class Builder:
    """Represents a concrete CQ builder invocation."""

    name: str
    url: str

    def as_yaml(self) -> Any:
        """Convert to a YAML-compatible structure."""
        return dataclasses.asdict(self)

    @classmethod
    def from_yaml(cls, s: dict[str, str]) -> "Builder":
        """Create an instance of this class from an `as_yaml()`."""
        return cls(**s)


@dataclasses.dataclass(frozen=True, eq=True, order=True)
class Package:
    """Portage package descriptor."""

    category: str
    package_name: str

    def __str__(self) -> str:
        return f"{self.category}/{self.package_name}"

    def as_yaml(self) -> Any:
        """Convert to a YAML-compatible structure."""
        return str(self)

    @classmethod
    def from_yaml(cls, s: str) -> "Package":
        """Create an instance of this class from an `as_yaml()`."""
        if "/" not in s:
            raise ValueError(f"Package should be $CATEGORY/$PN; got {s!r}")
        category, package_name = s.split("/", 1)
        return cls(category, package_name)


@dataclasses.dataclass(frozen=True, eq=True, order=True)
class YamlPackageWarnings:
    """Warnings for a package, formattable as YAML."""

    package: Package
    warning_lines: list[str]
    warning_names: list[str]
    observed_on: list[Builder]

    def as_yaml(self) -> Any:
        """Convert to a YAML-compatible structure."""
        return {
            "package": self.package.as_yaml(),
            "warning_lines": self.warning_lines,
            "warning_names": self.warning_names,
            "observed_on": [x.as_yaml() for x in self.observed_on],
        }

    @classmethod
    def from_yaml(cls, s: dict[str, Any]) -> "YamlPackageWarnings":
        """Create an instance of this class from an `as_yaml()`."""
        package = Package.from_yaml(s["package"])
        observed_on = [Builder.from_yaml(x) for x in s.get("observed_on", ())]
        # yaml will sometimes deserialize these as None if they're empty;
        # normalize to an empty list.
        warning_lines = s["warning_lines"] or []
        warning_names = s["warning_names"] or []
        return cls(
            package=package,
            observed_on=observed_on,
            warning_lines=warning_lines,
            warning_names=warning_names,
        )


@dataclasses.dataclass(frozen=True, eq=True, order=True)
class YamlFile:
    """YAML file describing warnings & bugs to file about them."""

    # Name of the file that the exemptions will be placed in. This is used in
    # bug-filing to give users a concrete place to edit.
    exemption_go_file_name: str

    # A list of bugs that should be considered 'severe' enough to file a
    # high-priority bug about.
    severe_warnings: list[str]

    # Entries that the Mage is meant to modify.
    per_package_warnings: list[YamlPackageWarnings]

    # What the `per_package_warnings` were on the initial write of the file.
    # When setting this up, this should be set to `per_package_warnings`; when
    # _reading_, this information is used to figure out where the Mage made
    # changes & file bugs appropriately.
    frozen_per_package_warnings: list[YamlPackageWarnings]

    # NOTE: No `as_yaml` is provided, since this is the top-level structure. Use
    # `as_raw_yaml()`, which is the serialized form of YAML.
    def as_raw_yaml(self) -> str:
        """Returns this class as serialized YAML."""
        yaml_file_parts = (
            YAML_FILE_HEADER,
            "\n\n",
            YAML_GO_FILE_SUBHEADER,
            yaml.dump({"exemption_go_file_name": self.exemption_go_file_name}),
            "\n\n",
            YAML_SEVERITY_SUBHEADER,
            "severe_warnings:\n",
            yaml.dump(self.severe_warnings),
            "\n\n",
            YAML_WARNING_LIST_SUBHEADER,
            "per_package_warnings:\n",
            # Add extra spacing between these, because there are likely many of
            # them, and the added visual separation is helpful.
            # Also, set the width to 300cols here, since visual similarity
            # between warning lines is much easier to spot when they're not
            # wrapped.
            "\n".join(
                yaml.dump([x.as_yaml()], width=300)
                for x in self.per_package_warnings
            ),
            "\n\n",
            YAML_ORIGINAL_WARNING_LIST_SUBHEADER,
            "frozen_per_package_warnings:\n",
            # This generally isn't meant for editing by humans; don't try to
            # make the formatting pretty or consistent.
            yaml.dump([x.as_yaml() for x in self.frozen_per_package_warnings]),
        )

        return "".join(yaml_file_parts)

    @classmethod
    def from_yaml(cls, s: dict[str, Any]) -> "YamlFile":
        """Create an instance of this class from an `as_raw_yaml()`."""
        s = s.copy()
        for k in ("per_package_warnings", "frozen_per_package_warnings"):
            s[k] = [YamlPackageWarnings.from_yaml(x) for x in s.get(k, ())]
        # Similar to other `from_yaml`, yaml will sometimes deserialize these as
        # None if they're empty; normalize to an empty list.
        s["severe_warnings"] = s["severe_warnings"] or []
        return cls(**s)


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


@dataclasses.dataclass(eq=True)
class FatalPackageWarning:
    """Represents a fatal warning recorded for a specific package."""

    # Package this happened in.
    package: "Package"
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
    stdout: str, absolutize_with_cwd: str | None = None
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

    def add(self, other: "FatalWarningGroup") -> None:
        self.warning_names |= other.warning_names
        self.warning_lines |= other.warning_lines


def parse_fatal_warnings_file(
    warnings_json_file: Path,
) -> tuple["Package", FatalWarningGroup] | None:
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

    package = Package(category, package_name)
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
) -> DefaultDict["Package", FatalWarningGroup]:
    logging.info("Parsing warning reports under %s", warning_reports)

    per_package_groups: DefaultDict["Package", FatalWarningGroup] = (
        collections.defaultdict(FatalWarningGroup)
    )
    for warning_report in find_all_warning_reports_in(warning_reports):
        parse_result = parse_fatal_warnings_file(warning_report)
        if not parse_result:
            continue
        package, warnings_group = parse_result
        per_package_groups[package].add(warnings_group)
    return per_package_groups


def main() -> None:
    """Main, just exists to print an example of the yaml."""
    print("## This module is meant to be imported, but has an executable so")
    print("## people can easily see what the YAML produced by it looks like:")
    print()
    print(
        YamlFile(
            exemption_go_file_name="some_file.go",
            severe_warnings=[
                "severe_warning",
            ],
            per_package_warnings=[
                YamlPackageWarnings(
                    package=Package("per-package", "warnings"),
                    warning_lines=[
                        "per-package warning line 1",
                        "per-package warning line 2",
                    ],
                    warning_names=["per-package-name-1", "per-package-name-2"],
                    observed_on=[
                        Builder(
                            name="per-package-builder-foo",
                            url="https://per-package-builder-foo",
                        ),
                    ],
                ),
            ],
            frozen_per_package_warnings=[
                YamlPackageWarnings(
                    package=Package("frozen", "warnings"),
                    warning_lines=[
                        "frozen warning line 1",
                        "frozen warning line 2",
                    ],
                    warning_names=["frozen-name-1", "frozen-name-2"],
                    observed_on=[
                        Builder(
                            name="frozen-builder-foo",
                            url="https://frozen-builder-foo",
                        ),
                    ],
                ),
            ],
        ).as_raw_yaml()
    )
