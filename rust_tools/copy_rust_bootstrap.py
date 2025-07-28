# Copyright 2022 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Copies rust-bootstrap artifacts from an SDK build to localmirror.

We use localmirror to host these artifacts, but they've changed a bit over
time, so simply `gsutil cp $FROM $TO` doesn't work. This script allows the
convenience of the old `cp` command.

Run this from outside of the chroot; it will enter the chroot as needed.
"""

import argparse
import logging
from pathlib import Path
import shlex
import subprocess
import tempfile
from typing import List, Sequence, Union

from cros_utils import cros_paths
from llvm_tools import chroot


_LOCALMIRROR_ROOT = "gs://chromeos-localmirror/distfiles/"


def _chroot_run(command: Sequence[Union[str, Path]], chromiumos_root: Path):
    run_command: List[Union[str, Path]] = ["cros_sdk", "--"]
    run_command += command
    subprocess.run(
        run_command,
        check=True,
        cwd=chromiumos_root,
        stdin=subprocess.DEVNULL,
    )


def _ensure_lbzip2_is_installed(chromiumos_checkout: Path):
    logging.info("Ensuring lbzip2 is installed...")
    # `--noreplace` could be used, but checking `which lbzip2 ||` is
    # significantly faster than invoking emerge, so prefer that.
    install_cmd = shlex.join(("sudo", "emerge", "-g", "-j", "app-arch/lbzip2"))
    update_script = f"which lbzip2 || {install_cmd}"
    _chroot_run(
        ("bash", "-cux", update_script),
        chromiumos_root=chromiumos_checkout,
    )


def determine_target_path(sdk_path: str) -> str:
    """Determine where `sdk_path` should sit in localmirror."""
    gs_prefix = "gs://"
    if not sdk_path.startswith(gs_prefix):
        raise ValueError(f"Invalid GS path: {sdk_path!r}")

    file_name = Path(sdk_path[len(gs_prefix) :]).name
    return _LOCALMIRROR_ROOT + file_name


def _download(remote_path: str, local_file: Path):
    """Downloads the given gs:// path to the given local file."""
    logging.info("Downloading %s -> %s", remote_path, local_file)
    subprocess.run(
        ["gsutil", "cp", remote_path, str(local_file)],
        check=True,
        stdin=subprocess.DEVNULL,
    )


def _debinpkgify_in_chroot(
    *,
    chromiumos_checkout: Path,
    chroot_binpkg_file: Path,
    chroot_workdir_path: Path,
) -> Path:
    """Converts a binpkg into the files it installs.

    Note that this function makes temporary files in the same directory as
    `binpkg_file`. It makes no attempt to clean them up.
    """
    logging.info("Converting %s from a binpkg...", chroot_binpkg_file)

    # The SDK builder produces binary packages:
    # https://wiki.gentoo.org/wiki/Binary_package_guide
    #
    # Which means that `chroot_binpkg_file` is in the XPAK format. We want to
    # split that out, and recompress it from zstd (which is the compression
    # format that CrOS uses) to bzip2 (which is what we've historically used,
    # and which is what our ebuild expects).

    # SUBTLE: Entering the chroot can lead to `cros_sdk` printing out
    # housekeeping messages around things like chroot upgrades; the sequence of
    # commands here is carefully written to keep all redirection _within the
    # bash executed by `cros_sdk`.

    tbz2_file = chroot_workdir_path / "recompressed.tbz2"
    pipeline = (
        f"qtbz2 --split --tarbz2 -O {shlex.quote(str(chroot_binpkg_file))}"
        f" | zstd -d - --stdout"
        f" | lbzip2 -z -9 --stdout"
        f" > {shlex.quote(str(tbz2_file))}"
    )
    _chroot_run(
        (
            "bash",
            "-o",
            "pipefail",
            "-ceux",
            pipeline,
        ),
        chromiumos_checkout,
    )
    return tbz2_file


def _upload(local_file: Path, remote_path: str, force: bool):
    """Uploads the local file to the given gs:// path."""
    logging.info("Uploading %s -> %s", local_file, remote_path)
    cmd_base = ["gsutil", "cp", "-a", "public-read"]
    if not force:
        cmd_base.append("-n")
    subprocess.run(
        cmd_base + [str(local_file), remote_path],
        check=True,
        stdin=subprocess.DEVNULL,
    )


def main(argv: List[str]):
    chromiumos_checkout = cros_paths.script_chromiumos_checkout_or_exit()
    chroot.VerifyOutsideChroot()

    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.INFO,
    )

    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "sdk_artifact",
        help="Path to the SDK rust-bootstrap artifact to copy. e.g., "
        "gs://chromeos-prebuilt/host/amd64/amd64-host/"
        "chroot-2022.07.12.134334/packages/dev-lang/"
        "rust-bootstrap-1.59.0.tbz2.",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Do everything except actually uploading the artifact.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Upload the artifact even if one exists in localmirror already.",
    )
    opts = parser.parse_args(argv)

    _ensure_lbzip2_is_installed(chromiumos_checkout=chromiumos_checkout)

    host_tmpdir_base = (
        chromiumos_checkout
        / cros_paths.DEFAULT_CHROOT_OUT_DIR
        / cros_paths.DEFAULT_CHROOT_TMPDIR_IN_OUT
    )
    chroot_tmpdir_base = Path("/tmp")

    target_path = determine_target_path(opts.sdk_artifact)
    with tempfile.TemporaryDirectory(dir=host_tmpdir_base) as raw_tempdir:
        host_tmpdir = Path(raw_tempdir)
        chroot_tmpdir = chroot_tmpdir_base / host_tmpdir.name

        def host_tmp_path_to_chroot(host_path: Path):
            return chroot_tmpdir / host_path.relative_to(host_tmpdir)

        def chroot_tmp_path_to_host(chroot_path: Path):
            return host_tmpdir / chroot_path.relative_to(chroot_tmpdir)

        download_path = host_tmpdir / "sdk_artifact"
        workdir_path = host_tmpdir / "workdir"
        workdir_path.mkdir(parents=True)
        _download(opts.sdk_artifact, download_path)
        chroot_download_path = host_tmp_path_to_chroot(download_path)
        chroot_workdir_path = host_tmp_path_to_chroot(workdir_path)
        chroot_file_to_upload = _debinpkgify_in_chroot(
            chromiumos_checkout=chromiumos_checkout,
            chroot_binpkg_file=chroot_download_path,
            chroot_workdir_path=chroot_workdir_path,
        )
        file_to_upload = chroot_tmp_path_to_host(chroot_file_to_upload)
        if opts.dry_run:
            logging.info(
                "--dry-run specified; skipping upload of %s to %s",
                file_to_upload,
                target_path,
            )
        else:
            _upload(file_to_upload, target_path, opts.force)
