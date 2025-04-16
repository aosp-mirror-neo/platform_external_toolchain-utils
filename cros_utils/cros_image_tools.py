# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tools to work with ChromeOS images."""

import contextlib
import json
import logging
from pathlib import Path
import subprocess
from typing import Generator


def _find_losetup(chromeos_root: Path) -> Path:
    """Returns the path to the cros_losetup script."""
    return chromeos_root / "chromite" / "scripts" / "cros_losetup"


@contextlib.contextmanager
def _mount_image_loopback(
    chromeos_root: Path, image_path: Path
) -> Generator[Path, None, None]:
    """Mounts loopback for an image, yielding the /dev/loop${N} result.

    Cleans up the loopback on exit.
    """
    cros_losetup = _find_losetup(chromeos_root)
    losetup_stdout = subprocess.run(
        (cros_losetup, "attach", image_path),
        check=True,
        # Don't set stdin to DEVNULL, since sudo may prompt for a password.
        stdout=subprocess.PIPE,
        encoding="utf-8",
    ).stdout

    # The stdout of losetup is simply a JSON object containing
    # {"path": "/dev/loop${N}"}
    lo_object: Path = json.loads(losetup_stdout)["path"]
    try:
        yield lo_object
    finally:
        returncode = subprocess.run(
            (cros_losetup, "detach", lo_object),
            check=False,
            # Don't set stdin to DEVNULL, since sudo may prompt for a password.
        ).returncode
        # There's not much to do here, especially if we're already handling
        # an exception.
        if returncode:
            logging.error("Detaching %s unexpectedly failed", lo_object)


def _find_root_partition(loop_device: Path) -> Path:
    """Given a path to a loopback device, returns the partition of the rootfs.

    >>> _find_root_partition(Path("/dev/loop1"))
    Path("/dev/loop1p3")
    """
    fdisk_stdout = subprocess.run(
        ("sudo", "fdisk", "-x", loop_device),
        check=True,
        # Don't set stdin to DEVNULL, since sudo may prompt for a password.
        stdout=subprocess.PIPE,
        encoding="utf-8",
    ).stdout

    # The output format here, broadly, is:
    # ```
    # A bunch of metadata that
    # this function does not
    # care about
    #
    # Device Start End Sectors Type-UUID UUID Name Attrs
    # row1
    # row2
    # row3
    # ...
    # ```
    #
    # The goal is to extract the row named ROOT-A.
    loop_device_str = str(loop_device)
    for line in fdisk_stdout.splitlines():
        if not line.startswith(loop_device_str):
            continue

        columns = line.split()
        if len(columns) <= 6:
            continue

        device_name = columns[6]
        if device_name == "ROOT-A":
            device_partition = Path(columns[0])
            return device_partition
    raise ValueError(f"Could not find rootfs in {fdisk_stdout!r}")


@contextlib.contextmanager
def mount_image(
    chromeos_root: Path, image_path: Path, mount_dir: Path
) -> Generator[None, None, None]:
    """Mounts a ChromeOS image rootfs, like chromiumos_base_image.bin.

    On exit of the context manager, the image is cleaned up.

    Args:
        chromeos_root: Root of a ChromeOS checkout.
        image_path: The path to the image to mount.
        mount_dir: The directory to mount the image on.
    """
    with _mount_image_loopback(chromeos_root, image_path) as lo_device:
        loop_partition = _find_root_partition(lo_device)
        logging.info(
            "Mounting root partition %s at %s", loop_partition, mount_dir
        )
        subprocess.run(
            (
                "sudo",
                "mount",
                "-o",
                "ro",
                loop_partition,
                mount_dir,
            ),
            check=True,
            # Don't set stdin to DEVNULL, since sudo may prompt for a password.
        )

        try:
            yield
        finally:
            returncode = subprocess.run(
                ("sudo", "umount", mount_dir),
                check=False,
                # Don't set stdin to DEVNULL, since sudo may prompt.
            ).returncode
            # There's not much to do here, especially if we're already handling
            # an exception. Just log it and continue.
            if returncode:
                logging.error("Umounting %s unexpectedly failed", mount_dir)
