# Copyright 2020 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Gets info about completed chromiumos-sdk runs.

Moreover, this script exists to get versions of published sdk tarballs in
gs://chromiumos-sdk/. The hope is that it'll help answer the question "when did
the toolchain ebuild ${x} go live?"
"""

import argparse
import json
import logging
import os
from pathlib import Path
import shutil
import sys
import tempfile

from cros_utils import gs


def fetch_all_sdk_manifest_paths() -> list[str]:
    """Fetches all paths of SDK manifests; newer = later in the return value."""
    # These are named so that sorted order == newest last.
    return sorted(
        x.gs_path for x in gs.ls("gs://chromiumos-sdk/cros-sdk-20??.*.Manifest")
    )


def load_manifest_versions(manifest: Path) -> dict[str, str]:
    with manifest.open(encoding="utf-8") as f:
        raw_versions = json.load(f)

    # We get a dict of list of lists of versions and some other metadata, e.g.
    # {"foo/bar": [["1.2.3", {}]]}
    # Trim out the metadata.
    return {k: v[0][0] for k, v in raw_versions["packages"].items()}


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-d", "--debug", action="store_true", help="Emit debugging output"
    )
    parser.add_argument(
        "-n",
        "--number",
        type=int,
        default=20,
        help="Number of recent manifests to fetch info about. 0 means "
        "unlimited.",
    )
    args = parser.parse_args(argv)

    is_debug = args.debug
    logging.basicConfig(level=logging.DEBUG if is_debug else logging.INFO)

    logging.debug("Fetching SDK manifests")
    manifest_paths = fetch_all_sdk_manifest_paths()
    logging.debug("%d SDK manifests fetched", len(manifest_paths))
    if not manifest_paths:
        sys.exit("No SDK manifests found")

    number = args.number
    if number:
        manifest_paths = manifest_paths[-number:]

    tempdir = Path(tempfile.mkdtemp(prefix="cros-sdk-rolls"))
    try:
        logging.debug("Working in tempdir %r", tempdir)
        gs.cp_many(manifest_paths, tempdir)

        for path in manifest_paths:
            basename = os.path.basename(path)
            versions = load_manifest_versions(tempdir.joinpath(basename))
            print(f'{basename}: {versions["sys-devel/llvm"]}')
    finally:
        if is_debug:
            logging.debug("Keeping around tempdir %r to aid debugging", tempdir)
        else:
            shutil.rmtree(tempdir)
