# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Fetches the size diff between two images on gs://.

If given a CL, this will autodetect a passing CQ builder on that CL and find
a corresponding release build for said CQ builder. The sizes of these images
will be compared.

**Please note** that there's often version skew between release builds and CQ
builds. While this skew shouldn't result in _huge_ binary size differences,
it can still account for a few MB of diff in an average case.
"""

import abc
import argparse
import dataclasses
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from cros_utils import cros_image_tools
from cros_utils import cros_paths
from cros_utils import gerrit_utils
from llvm_tools import cros_cls


@dataclasses.dataclass(frozen=True)
class SizeDiffInfo:
    """Holds information about a size difference."""

    baseline_size_bytes: int
    new_size_bytes: int


class ComparableArtifact(abc.ABC):
    """Artifacts from CQ runs that can be compared."""

    @property
    @abc.abstractmethod
    def artifact_name(self) -> str:
        """Returns the name of the artifact in gs:// e.g., "image.zip"."""

    @abc.abstractmethod
    def _measure_artifact_size(self, file: Path) -> int:
        """Given a path to the artifact, extract the relevant size info.

        The directory that `file` is in may be mutated by this function. No
        guarantees are made about the state of said directory after execution
        finishes, except that `file` should remain unmodified.
        """

    def _download_and_measure_size(self, gs_url: str) -> int:
        with tempfile.TemporaryDirectory(
            prefix="fetch_size_diff_"
        ) as tempdir_str:
            into = Path(tempdir_str)
            local_file = into / os.path.basename(gs_url)
            subprocess.run(
                ["gsutil", "cp", gs_url, local_file],
                check=True,
                stdin=subprocess.DEVNULL,
            )
            return self._measure_artifact_size(local_file)

    def compare_size_from_gs(self, baseline: str, new: str) -> SizeDiffInfo:
        return SizeDiffInfo(
            baseline_size_bytes=self._download_and_measure_size(baseline),
            new_size_bytes=self._download_and_measure_size(new),
        )


class DebugInfoArtifact(ComparableArtifact):
    """ComparableArtifact instance for debuginfo."""

    @property
    def artifact_name(self) -> str:
        return "debug.tgz"

    def _measure_artifact_size(self, file: Path) -> int:
        chrome_debug = "./opt/google/chrome/chrome.debug"
        logging.info("Unpacking debuginfo...")
        subprocess.run(
            ["tar", "xaf", file, chrome_debug],
            check=True,
            cwd=file.parent,
            stdin=subprocess.DEVNULL,
        )
        return os.path.getsize(file.parent / chrome_debug)


def _calculate_image_size(mount_point: Path) -> int:
    """Returns the size of the FS mounted at mount_point, in bytes."""
    df_stdout = subprocess.run(
        ("df", "--block-size=1", mount_point),
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        encoding="utf-8",
    ).stdout

    # There's a row of header, then a row with the info we want with the
    # columns:
    # - filesystem_name
    # - total_blocks
    # - used_blocks
    # - available_blocks
    # - use%
    # - mount_point
    #
    # Since `--block-size=1` above, all 'blocks' will be 1 byte.
    rows = df_stdout.strip().splitlines()
    header = rows[0]
    if not header.startswith("Filesystem"):
        raise ValueError(f"`df` header doesn't look as expected: {header!r}")

    data = rows[1]
    used_blocks = data.split()[2]
    return int(used_blocks)


class ImageSizeArtifact(ComparableArtifact):
    """ComparableArtifact instance for image files."""

    def __init__(self, chromeos_root: Path):
        self._chromeos_root = chromeos_root

    @property
    def artifact_name(self) -> str:
        return "chromiumos_base_image.tar.xz"

    def _measure_artifact_size(self, file: Path) -> int:
        tmpdir = file.parent
        base_image_name = "chromiumos_base_image.bin"
        subprocess.run(
            [
                "tar",
                "-xaf",
                file,
            ],
            check=True,
            cwd=tmpdir,
            stdin=subprocess.DEVNULL,
        )
        mount_dir = tmpdir / "mount"
        mount_dir.mkdir()
        image_file = tmpdir / base_image_name
        with cros_image_tools.mount_image(
            self._chromeos_root, image_file, mount_dir
        ):
            return _calculate_image_size(mount_dir)


def is_probably_non_production_builder(builder_name: str) -> bool:
    """Quickly determine if a builder doesn't represent a board in production.

    Note that this is a heuristic; results should be taken as mostly accurate.
    """
    return any(
        x in builder_name
        for x in (
            "-asan-",
            "-buildtest-",
            "-fuzzer-",
            "-kernelnext-",
            "-kernel-",
            "-sdknext-",
            "-ubsan-",
            "-vmtest-",
            "-vm-",
        )
    )


def try_gsutil_ls(paths: list[str]) -> list[str]:
    """Returns all of the paths `gsutil` matches from `paths`.

    Ignores errors from gsutil about paths not existing.
    """
    result = subprocess.run(
        ["gsutil", "-m", "ls"] + paths,
        # If any URI doesn't exist, gsutil will fail. Ignore the failure.
        check=False,
        encoding="utf-8",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode:
        # Ensure the error message is what's expected, rather than e.g.,
        # invalid credentials.
        err_msg = "CommandException: One or more URLs matched no objects"
        if err_msg not in result.stderr:
            logging.error(
                "gsutil had unexpected output; stderr: %r", result.stderr
            )
            result.check_returncode()
    return [x.strip() for x in result.stdout.splitlines()]


def find_size_diffable_cq_artifacts(
    cq_build_ids: list[cros_cls.BuildID],
    artifact_name: str,
) -> list[tuple[str, str]]:
    """Searches the cq-orchestrator builds for candidates for size comparison.

    Returns:
        None if no candidates are found. Otherwise, returns a two-tuple: index
        0 is the baseline (release) artifact, index 1 is the corresponding
        artifact generated by the CQ.
    """
    for cq_build_id in cq_build_ids:
        logging.info("Inspecting CQ build %d...", cq_build_id)
        orch_output = cros_cls.CQOrchestratorOutput.fetch(cq_build_id)
        production_child_builders = [
            (name, val)
            for name, val in orch_output.child_builders.items()
            if not is_probably_non_production_builder(name)
        ]
        child_builder_values = cros_cls.CQBoardBuilderOutput.fetch_many(
            [x for _, x in production_child_builders]
        )
        artifact_dir_links = [
            (builder_name, output.artifacts_link)
            for (builder_name, _), output in zip(
                production_child_builders, child_builder_values
            )
            # Only choose successful builders, since failing builders may have
            # incomplete artifacts (e.g., debug.tgz is "the set of all debuginfo
            # of successfully built packages," and the build might not have
            # gotten far enough to produce chromeos-chrome's debug.tgz)
            if output.artifacts_link is not None
            and output.status == cros_cls.BuilderStatus.SUCCESS
        ]

        if not artifact_dir_links:
            logging.info("No children of CQ run %d had artifacts", cq_build_id)
            continue

        available_artifacts = try_gsutil_ls(
            [
                os.path.join(artifacts_link, artifact_name)
                for _, artifacts_link in artifact_dir_links
            ]
        )
        if not available_artifacts:
            logging.info(
                "No children of CQ run %d produced a(n) %s",
                cq_build_id,
                artifact_name,
            )
            continue

        logging.debug(
            "Found candidate %s artifacts: %s",
            artifact_name,
            available_artifacts,
        )

        # Match the artifacts up with their builders. `try_gsutil_ls` will
        # return _some subset_ of the requested artifacts in _some order_.
        builder_dirs = {
            gs_path: cq_builder for cq_builder, gs_path in artifact_dir_links
        }
        results = []
        for artifact_link in available_artifacts:
            artifact_dir = os.path.dirname(artifact_link)
            builder = builder_dirs.get(artifact_dir)
            assert builder, (
                f"Couldn't match artifact in {artifact_dir} with a builder "
                f"in {builder_dirs.keys()}"
            )
            results.append((builder, artifact_link))

        # Sort for consistent output.
        results.sort()
        return results
    return []


def inspect_gs_impl(
    baseline_gs_url: str, new_gs_url: str, artifact: ComparableArtifact
) -> None:
    """Compares the `image.zip`s at the given URLs, logging the results."""
    size_diff = artifact.compare_size_from_gs(baseline_gs_url, new_gs_url)
    # `%d` doesn't support `,` as a modifier, and commas make these numbers
    # much easier to read. Prefer to keep strings interpreted as format strings
    # constant.
    logging.info("Baseline size: %s", f"{size_diff.baseline_size_bytes:,}")
    logging.info("New size: %s", f"{size_diff.new_size_bytes:,}")

    diff_pct = abs(size_diff.new_size_bytes / size_diff.baseline_size_bytes) - 1
    logging.info("Diff: %.2f%%", diff_pct * 100)


def inspect_single_cl(
    cl: gerrit_utils.ChangeListURL, artifact: ComparableArtifact
) -> list[tuple[str, str]]:
    """Inspects a single CL for artifacts.

    Returns:
        A list of tuples of:
        - CQ builder name
        - gs:// path to artifact produced by that builder

        If no artifacts are found, the list is empty.
    """
    cq_build_ids = cros_cls.fetch_cq_orchestrator_ids(cl)
    if not cq_build_ids:
        logging.error("No completed cq-orchestrators found for %s", cl)
        return []

    return find_size_diffable_cq_artifacts(cq_build_ids, artifact.artifact_name)


def find_common_artifact(
    a: list[tuple[str, str]], b: list[tuple[str, str]]
) -> tuple[str, str, str] | None:
    """Finds an artifact that can be compared between the given artifact lists.

    The artifact lists should be in the form [(cq_builder_name, artifact_path)].

    Returns:
        None if none can be found; otherwise, a tuple of:
            - The selected CQ builder name
            - The artifact path from 'a'
            - The artifact path from 'b'
    """
    b_map = dict(b)
    shared_keys = ((k, a_value) for k, a_value in sorted(a) if k in b_map)
    return next(((k, a_value, b_map[k]) for k, a_value in shared_keys), None)


def inspect_cl(opts: argparse.Namespace, artifact: ComparableArtifact) -> None:
    """Implements the `cl` subcommand of this script."""
    logging.info("Finding artifacts for baseline CL...")
    baseline_artifacts = inspect_single_cl(opts.baseline_cl, artifact)
    logging.debug("Artifacts for baseline CL: %s", baseline_artifacts)
    logging.info("Finding artifacts for new CL...")
    new_artifacts = inspect_single_cl(opts.new_cl, artifact)
    logging.debug("Artifacts for new CL: %s", new_artifacts)

    common_artifact = find_common_artifact(baseline_artifacts, new_artifacts)
    if common_artifact is None:
        sys.exit(
            "Could not find a builder with a common artifact between CLs; "
            "maybe try CQ+1 again and wait for it to finish on each?"
        )

    cq_builder, baseline, new = common_artifact
    logging.info("Selected CQ builder %s for artifact diffing", cq_builder)
    logging.info("Comparing %s (baseline) to %s (new)", baseline, new)
    inspect_gs_impl(baseline, new, artifact)
    logging.warning(
        "Friendly reminder: CL inspection diffs between two CQ runs. Depending "
        "on how these were run, there may be some source skew. If a "
        "significant, unexpected difference is observed, you can always try "
        "CQ+1 again (preferably after adding the "
        "`Disallow-Recycled-Builds: all` footer to your commit messages)."
    )


def inspect_gs(opts: argparse.Namespace, artifact: ComparableArtifact) -> None:
    """Implements the `gs` subcommand of this script."""
    inspect_gs_impl(opts.baseline, opts.new, artifact)


def main(argv: list[str]) -> None:
    cros_root = cros_paths.script_chromiumos_checkout_or_exit()

    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    what_to_compare = parser.add_mutually_exclusive_group(required=True)
    what_to_compare.add_argument(
        "--image", action="store_true", help="Compare image.zip sizes."
    )
    what_to_compare.add_argument(
        "--debuginfo", action="store_true", help="Compare debuginfo sizes."
    )

    parser.add_argument(
        "--debug", action="store_true", help="Enable debug logging"
    )
    subparsers = parser.add_subparsers(required=True)

    cl_parser = subparsers.add_parser(
        "cl", help="Inspect a CL's CQ runs to find artifacts to compare."
    )
    cl_parser.set_defaults(func=inspect_cl)
    cl_parser.add_argument(
        "--baseline-cl",
        type=gerrit_utils.ChangeListURL.parse_with_patch_set,
        help="""
        Baseline CL to inspect CQ runs of. This must contain a patchset number.
        """,
    )
    cl_parser.add_argument(
        "--new-cl",
        type=gerrit_utils.ChangeListURL.parse_with_patch_set,
        help="""
        New CL to inspect CQ runs of. This must contain a patchset number. Any
        regressions or wins are reported against this CL (so if you're using
        this for llvm-next, the llvm-next CL should be passed as this flag).
        """,
    )

    gs_parser = subparsers.add_parser(
        "gs", help="Directly compare two zip files from gs://."
    )
    gs_parser.add_argument("baseline", help="Baseline file to compare.")
    gs_parser.add_argument("new", help="New file to compare.")
    gs_parser.set_defaults(func=inspect_gs)
    opts = parser.parse_args(argv)

    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.DEBUG if opts.debug else logging.INFO,
    )

    assert getattr(opts, "func", None), "Unknown subcommand?"
    if opts.image:
        artifact: ComparableArtifact = ImageSizeArtifact(cros_root)
    else:
        assert opts.debuginfo
        artifact = DebugInfoArtifact()

    opts.func(opts, artifact)
