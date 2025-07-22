# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Common code for exempting compiler warnings and filing bugs about them.

Want to see what the YAML looks like in practice? Run this as a script; no flags
necessary.
"""

import dataclasses
from typing import Any, Dict, List

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
    def from_yaml(cls, s: Dict[str, str]) -> "Builder":
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
    warning_lines: List[str]
    warning_names: List[str]
    observed_on: List[Builder]

    def as_yaml(self) -> Any:
        """Convert to a YAML-compatible structure."""
        return {
            "package": self.package.as_yaml(),
            "warning_lines": self.warning_lines,
            "warning_names": self.warning_names,
            "observed_on": [x.as_yaml() for x in self.observed_on],
        }

    @classmethod
    def from_yaml(cls, s: Dict[str, Any]) -> "YamlPackageWarnings":
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
    severe_warnings: List[str]

    # Entries that the Mage is meant to modify.
    per_package_warnings: List[YamlPackageWarnings]

    # What the `per_package_warnings` were on the initial write of the file.
    # When setting this up, this should be set to `per_package_warnings`; when
    # _reading_, this information is used to figure out where the Mage made
    # changes & file bugs appropriately.
    frozen_per_package_warnings: List[YamlPackageWarnings]

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
    def from_yaml(cls, s: Dict[str, Any]) -> "YamlFile":
        """Create an instance of this class from an `as_raw_yaml()`."""
        s = s.copy()
        for k in ("per_package_warnings", "frozen_per_package_warnings"):
            s[k] = [YamlPackageWarnings.from_yaml(x) for x in s.get(k, ())]
        # Similar to other `from_yaml`, yaml will sometimes deserialize these as
        # None if they're empty; normalize to an empty list.
        s["severe_warnings"] = s["severe_warnings"] or []
        return cls(**s)


def main():
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
