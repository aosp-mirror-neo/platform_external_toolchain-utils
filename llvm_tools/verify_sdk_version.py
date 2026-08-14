#!/usr/bin/env python3
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Verifies that all toolchains in sdk_version.conf exist in GS.

Primarily intended to verify manual SDK uprevs on branches, see
go/crostc-sdk-updates.
"""

import argparse
from collections.abc import Iterable
import concurrent.futures
import logging
from pathlib import Path
import re
import subprocess

from cros_utils import cros_paths
from cros_utils import gs


TARGETS: tuple[str, ...] = (
    "x86_64-cros-linux-gnu",
    "aarch64-cros-linux-gnu",
    "armv7a-cros-linux-gnueabihf",
    "i686-cros-linux-gnu",
    "arm-none-eabi",
)

SDK_BUCKET = "chromiumos-sdk"

# Path to sdk_version.conf relative to chromiumos-overlay
SDK_VERSION_CONF_REL = (
    Path("chromeos") / "binhost" / "host" / "sdk_version.conf"
)

_SDK_BUCKET_RE = re.compile(r'^\s*SDK_BUCKET=["\']?([^"\'\s#]+)', re.MULTILINE)
_TC_PATH_RE = re.compile(r'^\s*TC_PATH=["\']?([^"\'\s#]+)', re.MULTILINE)


def parse_sdk_version_conf_contents(conf_path: Path, content: str) -> str:
    """Parses TC_PATH from sdk_version.conf."""
    if bucket_match := _SDK_BUCKET_RE.search(content):
        bucket = bucket_match.group(1).removeprefix("gs://")
        if bucket != SDK_BUCKET:
            raise ValueError(
                f"Unexpected SDK_BUCKET in {conf_path}: {bucket!r} "
                f"(expected {SDK_BUCKET!r})"
            )

    tc_path_match = _TC_PATH_RE.search(content)
    if not tc_path_match:
        raise ValueError(f"TC_PATH not found in {conf_path}")

    tc_path = tc_path_match.group(1)
    if "%(target)s" not in tc_path:
        raise ValueError(f"TC_PATH does not contain '%(target)s': {tc_path}")

    return tc_path


def gs_url_exists(gs_url: str, gsutil_bin: str = gs.GSUTIL) -> bool:
    """Checks if a Google Storage URL exists."""
    cmd = (gsutil_bin, "ls", gs_url)
    result = subprocess.run(
        cmd,
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def _check_target_url(
    target: str,
    tc_path: str,
    gsutil_bin: str,
) -> str | None:
    """Checks if the toolchain URL for a single target exists in GS."""
    rel_path = tc_path.replace("%(target)s", target)
    gs_url = f"gs://{SDK_BUCKET}/{rel_path}"
    logging.info("Checking %s ...", gs_url)
    if not gs_url_exists(gs_url, gsutil_bin=gsutil_bin):
        return f"Missing toolchain tarball for target {target!r}: {gs_url}"
    return None


def verify_toolchain_urls(
    tc_path: str,
    targets: Iterable[str] = TARGETS,
    gsutil_bin: str = gs.GSUTIL,
) -> list[str]:
    """Verifies that toolchain tarballs exist for all specified targets."""
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = [
            executor.submit(
                _check_target_url,
                target,
                tc_path,
                gsutil_bin,
            )
            for target in targets
        ]
        results = (f.result() for f in futures)
        return [err for err in results if err is not None]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--sdk-version-file",
        type=Path,
        help="Path to sdk_version.conf (default: deduced from CrOS checkout)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    opts = parser.parse_args(argv)
    if opts.sdk_version_file is None:
        opts.sdk_version_file = (
            cros_paths.script_chromiumos_checkout_or_exit()
            / cros_paths.CHROMIUMOS_OVERLAY
            / SDK_VERSION_CONF_REL
        )
    return opts


def main(argv: list[str]) -> int:
    opts = parse_args(argv)
    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.DEBUG if opts.debug else logging.INFO,
    )

    conf_path: Path = opts.sdk_version_file
    if not conf_path.is_file():
        logging.error("sdk_version.conf not found at %s", conf_path)
        return 1

    try:
        tc_path = parse_sdk_version_conf_contents(
            conf_path, conf_path.read_text(encoding="utf-8")
        )
    except ValueError as e:
        logging.error("Failed to parse %s: %s", conf_path, e)
        return 1

    logging.info("Parsed TC_PATH: %s", tc_path)
    logging.info("Using GS bucket: %s", SDK_BUCKET)

    if errors := verify_toolchain_urls(tc_path):
        logging.error("Toolchain verification FAILED:")
        for error in errors:
            logging.error("  %s", error)
        logging.error("Is `TC_PATH` in `%s` correct?", conf_path)
        return 1

    logging.info(
        "All %d toolchain tarballs successfully verified in GS :)",
        len(TARGETS),
    )
    return 0
