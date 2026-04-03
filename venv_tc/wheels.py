#!/usr/bin/env python3
# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Manages the `wheels/` subdir.

This tool is a one-stop-shop to manage `wheels/`. That is, it can:
- Populate the `wheels/` subdir from gs://.
- Validate the hashes of all currently-downloaded wheels.
- Regenerate the `wheels/` manifest, and ensure all currently-needed wheels are
  available in gs://.
"""

# Since this script helps establish the virtual environment on the current
# machine, it needs to be able to run independently of that. Please keep to
# imports from Python's stdlib.
import argparse
import dataclasses
import functools
import hashlib
import json
import logging
import multiprocessing.pool
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
from typing import Any, Iterable


# Add `v1` here in case format changes happen in the future. These aren't
# _planned_; the current architecture of this should be able to use 'v1'
# forever, but no harm in explicit versioning.
_GS_WHEEL_BASE = "chromeos-localmirror/crostc/python-wheels/v1"
_GS_WHEEL_LOCATION = f"gs://{_GS_WHEEL_BASE}"
_GS_WHEEL_LOCATION_HTTPS = f"https://storage.googleapis.com/{_GS_WHEEL_BASE}"

_SUPPORTED_PYTHON_VERSIONS = (
    # For the chroot.
    "3.11",
    # For Debian.
    "3.13",
)

_SUPPORTED_PLATFORMS = (
    # For chroot/Debian. This is built against glibc 2.17, which encompasses
    # everything we care to run on.
    "manylinux_2_17_x86_64",
)


@dataclasses.dataclass(frozen=True)
class WheelManifest:
    """A manifest for wheels we use.

    Contains a mapping from wheel file name to a SHA512 hash of the contents.
    """

    wheel_hashes: dict[str, str]

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> "WheelManifest":
        return cls(wheel_hashes=obj["wheel_hashes"])

    def to_json(self) -> dict[str, Any]:
        """Convert this to a representation that can be serialized as JSON."""
        return dataclasses.asdict(self)


@functools.cache
def get_gs_executable() -> str:
    options = ("gsutil", "gsutil.py")
    for o in options:
        if shutil.which(o):
            return o
    raise ValueError(f"No gsutil could be found on $PATH; tried {options}")


def get_wheel_dir(venv_dir: Path) -> Path:
    return venv_dir / "wheels"


def get_manifest_location(venv_dir: Path) -> Path:
    return venv_dir / "wheel-manifest.json"


def read_wheel_manifest(venv_dir: Path) -> WheelManifest:
    with get_manifest_location(venv_dir).open(encoding="utf-8") as f:
        return WheelManifest.from_json(json.load(f))


def write_wheel_manifest(venv_dir: Path, manifest: WheelManifest) -> None:
    with get_manifest_location(venv_dir).open("w", encoding="utf-8") as f:
        json.dump(
            manifest.to_json(),
            f,
            indent=2,
            separators=(",", ": "),
            sort_keys=True,
        )


def populate_wheels_subdir_with_pip(venv_dir: Path, wheel_dir: Path) -> None:
    """Populates wheels/ using `pip`."""
    # No `exist_ok=True`, since this function is expected to leave the wheel dir
    # populated with **only** the wheels that are currently needed. By requiring
    # callers to remove the wheel dir, we know we're starting fresh.
    wheel_dir.mkdir()
    for py_version in _SUPPORTED_PYTHON_VERSIONS:
        for platform in _SUPPORTED_PLATFORMS:
            logging.info(
                "Downloading wheels for %s with python %s", platform, py_version
            )
            pip_cmd = (
                "python3",
                "-m",
                "pip",
                "download",
                # Sometimes, local configuration may lead to fetching from other
                # package repositories. We always want to use Python's default
                # here.
                "--index-url=https://pypi.org/simple/",
                # Require binary downloads for all packages. That is, don't
                # allow `tgz` packages that require build-time tools like
                # `setuptools`, `cpython`, `wheel`, etc.
                #
                # This means we need to be very specific about the platforms
                # and python versions we support, but it also speeds up venv
                # unpack/setup times, and means we don't need to worry about
                # glibc version dependencies/etc creeping in: all of these
                # packages should be runnable on regular, semi-up-to-date
                # linux systems.
                "--only-binary=:all:",
                f"--dest={wheel_dir}",
                f"--python-version={py_version}",
                f"--platform={platform}",
                "-r",
                "requirements.txt",
            )
            logging.debug("pip command: %s", shlex.join(pip_cmd))
            subprocess.run(
                pip_cmd,
                check=True,
                cwd=venv_dir,
                stdin=subprocess.DEVNULL,
            )


def list_wheels_in_gs() -> list[str]:
    logging.info("Finding wheels in gs://...")
    gs_run = subprocess.run(
        (get_gs_executable(), "ls", _GS_WHEEL_LOCATION),
        check=False,
        encoding="utf-8",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    if gs_run.returncode:
        # `gsutil ls` exits unsuccessfully if nothing matches.
        # Turn that into an empty list.
        if "One or more URLs matched no objects" in gs_run.stderr:
            return []
        logging.error("gs failed; stderr: %s", gs_run.stderr)
        gs_run.check_returncode()

    trimmed_lines = (x.strip() for x in gs_run.stdout)
    return [os.path.basename(x) for x in trimmed_lines if x]


def upload_new_wheels_to_gs(wheel_dir: Path) -> None:
    subprocess.run(
        (
            get_gs_executable(),
            # Use multiple threads.
            "-m",
            "rsync",
            # Make them readable by everyone.
            "-a",
            "public-read",
            # Never overwrite files that exist in the bucket.
            "-i",
            wheel_dir,
            _GS_WHEEL_LOCATION,
        ),
        check=True,
        stdin=subprocess.DEVNULL,
    )


def fetch_wheels_from_gs_overwriting(
    wheel_dir: Path, wheels_to_fetch: list[str]
) -> None:
    """Replaces files in `wheel_dir` with ones that exist in gs://."""
    logging.info("Fetching %d wheels from gs://...", len(wheels_to_fetch))

    def fetch_one(wheel: str) -> None:
        url = f"{_GS_WHEEL_LOCATION_HTTPS}/{wheel}"
        out_file = wheel_dir / wheel
        cmd = (
            "curl",
            "--fail",
            "--location",
            "--silent",
            "--show-error",
            "--output",
            str(out_file),
            url,
        )
        logging.debug(
            "Downloading %s with curl command: %s", wheel, shlex.join(cmd)
        )
        max_attempts = 4
        for i in range(max_attempts):
            try:
                subprocess.run(
                    cmd,
                    check=True,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    encoding="utf-8",
                    errors="replace",
                )
                logging.debug("Download of %s succeeded!", wheel)
                return
            except subprocess.CalledProcessError as e:
                if i == max_attempts - 1:
                    logging.error(
                        "Final download attempt of %s failed; output: %s",
                        wheel,
                        e.stdout,
                    )
                    raise

                sleep_time = 2**i
                logging.warning(
                    "Download of %s failed (attempt %d/%d); "
                    "retrying in %ds...\noutput: %s",
                    wheel,
                    i + 1,
                    max_attempts,
                    sleep_time,
                    e.stdout,
                )
                time.sleep(sleep_time)

    # gs:// ratelimits are generally pretty high, so use a generous threadpool
    # here.
    pool_size = min(len(wheels_to_fetch), 32)
    with multiprocessing.pool.ThreadPool(pool_size) as pool:
        pool.map(fetch_one, wheels_to_fetch)


def calculate_wheel_hash(wheel_file: Path) -> str | None:
    """Returns the SHA512 hexdigest of the given wheel file.

    Returns None if the file does not exist.

    Use of this function in a thread is encouraged, as it does not monopolize
    the GIL.
    """
    try:
        with wheel_file.open("rb") as f:
            hasher = hashlib.sha512()
            # Arbitrarily hash files 512KB at a time.
            chunk_size = 512 * 1024
            for chunk in iter(lambda: f.read(chunk_size), b""):
                # Python docs on hashlib state:
                # ```
                # To allow multithreading, the Python GIL is released while
                # computing a hash supplied more than 2047 bytes of data at once
                # in its constructor or `.update` method.
                # ```
                hasher.update(chunk)
    except FileNotFoundError:
        return None
    return hasher.hexdigest()


def generate_wheel_manifest(
    wheel_dir: Path, pool: multiprocessing.pool.ThreadPool
) -> WheelManifest:
    files = [x.name for x in wheel_dir.iterdir()]
    hashes = pool.map(lambda x: calculate_wheel_hash(wheel_dir / x), files)

    manifest_dict: dict[str, str] = {}
    for f, h in zip(files, hashes):
        assert h is not None, f"File {f} disappeared during hash generation"
        manifest_dict[f] = h
    return WheelManifest(manifest_dict)


def update_wheels_and_manifest(
    venv_dir: Path, upload: bool, upload_certified: bool
) -> None:
    wheel_dir = get_wheel_dir(venv_dir)

    # Always start fresh, since we'll generate the Manifest based on the files
    # in the `wheels/` subdir.
    if wheel_dir.exists():
        shutil.rmtree(wheel_dir)

    populate_wheels_subdir_with_pip(venv_dir, wheel_dir)
    wheels_in_gs = list_wheels_in_gs()
    # If pip has published newer versions of any of these packages under the
    # same name, prefer the older ones that already exist on gs://. We can't
    # easily overwrite the old names.
    needed_wheel_set = {x.name for x in wheel_dir.iterdir()}
    local_and_gs_wheels = [x for x in wheels_in_gs if x in needed_wheel_set]
    if local_and_gs_wheels:
        fetch_wheels_from_gs_overwriting(wheel_dir, local_and_gs_wheels)

    logging.info("Generating wheel manifest...")
    with multiprocessing.pool.ThreadPool() as pool:
        new_manifest = generate_wheel_manifest(wheel_dir, pool)

    write_wheel_manifest(venv_dir, new_manifest)
    if not upload:
        logging.warning("--upload not specified; not uploading wheels")
        return

    # This could be checked way earlier, but by failing _after_ setting up the
    # local wheel environment, the user can immediately start on
    # go/crostc-venv-updates rather than having to amend their `--upload`
    # command & try again.
    if not upload_certified:
        sys.exit(
            "--i-have-verified-the-wheels not passed; upload aborted. Please "
            "be sure to follow the steps at go/crostc-venv-updates."
        )

    logging.info("Uploading wheels to gs://...")
    upload_new_wheels_to_gs(wheel_dir)


def validate_one_wheel_file(
    wheel_name: str, wheel_dir: Path, manifest: WheelManifest
) -> str | None:
    """Checks a single wheel. Returns the name if invalid, else None."""
    wheel_file = wheel_dir / wheel_name
    wheel_hash = calculate_wheel_hash(wheel_file)
    if wheel_hash is None:
        logging.info(
            "Wheel %s is not present locally; will download.", wheel_name
        )
        return wheel_name

    if wheel_hash == manifest.wheel_hashes[wheel_name]:
        return None

    logging.warning(
        "Hash of wheel %s didn't match manifest hash; will refetch.", wheel_name
    )
    wheel_file.unlink()
    return wheel_name


def validate_files_against_manifest(
    wheel_dir: Path,
    manifest: WheelManifest,
    pool: multiprocessing.pool.ThreadPool,
    subset: list[str] | None = None,
) -> list[str]:
    """Checks files against the manifest, returning a list of invalid files."""
    files_to_check: Iterable[str] = manifest.wheel_hashes.keys()
    if subset:
        files_to_check = (x for x in files_to_check if x in subset)

    results = pool.starmap(
        validate_one_wheel_file,
        [(x, wheel_dir, manifest) for x in files_to_check],
    )
    return [res for res in results if res]


def ensure_downloaded(venv_dir: Path, clean: bool) -> None:
    """Ensures that wheels/ contains all wheels in the wheel-manifest.

    Also verifies that hashes match the manifest; if not, the local files get
    replaced. All replacements are from gs://.
    """
    wheel_dir = get_wheel_dir(venv_dir)
    if clean and wheel_dir.exists():
        logging.info("`--clean` passed; removing %s", wheel_dir)
        shutil.rmtree(wheel_dir)

    manifest = read_wheel_manifest(venv_dir)

    with multiprocessing.pool.ThreadPool() as pool:
        logging.info("Validating local wheels...")
        broken_files = validate_files_against_manifest(
            wheel_dir, manifest, pool
        )
        if not broken_files:
            logging.info(
                "All local wheel files present and passed checksum validation."
            )
            return

        # gs errors out if asked to download to a directory that does not exist.
        wheel_dir.mkdir(exist_ok=True)
        fetch_wheels_from_gs_overwriting(wheel_dir, broken_files)
        still_broken_files = validate_files_against_manifest(
            wheel_dir, manifest, pool, subset=broken_files
        )
        if still_broken_files:
            raise ValueError(
                f"Files from gs:// failed checksum validation: "
                f"{still_broken_files}"
            )
        logging.info("Download and checksum success.")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--debug", action="store_true", help="Enable debug logging"
    )

    subparsers = parser.add_subparsers(dest="subcommand", required=True)
    ensure_parser = subparsers.add_parser(
        "ensure-downloaded",
        help="Ensure that all wheels are downloaded and valid.",
    )
    ensure_parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove the wheels dir before downloading.",
    )
    update_parser = subparsers.add_parser(
        "update-wheels-and-manifest",
        help="Update the wheel manifest and upload any new wheels.",
    )
    update_parser.add_argument(
        "--upload",
        action="store_true",
        help="Upload any new wheels to gs://.",
    )
    update_parser.add_argument(
        "--i-have-verified-the-wheels",
        dest="upload_certified",
        action="store_true",
        help="""
        Pass this to certify that you've followed the verification steps at
        go/crostc-venv-updates .
        """,
    )

    return parser.parse_args(argv)


def main(argv: list[str]) -> None:
    opts = parse_args(argv)
    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.DEBUG if opts.debug else logging.INFO,
    )

    # The venv dir is the one that contains this script.
    venv_dir = Path(__file__).parent.resolve()

    if opts.subcommand == "ensure-downloaded":
        ensure_downloaded(venv_dir, clean=opts.clean)
    elif opts.subcommand == "update-wheels-and-manifest":
        update_wheels_and_manifest(
            venv_dir,
            upload=opts.upload,
            upload_certified=opts.upload_certified,
        )
    else:
        # This should be unreachable.
        raise ValueError(f"Unknown subcommand {opts.subcommand}")


if __name__ == "__main__":
    main(sys.argv[1:])
