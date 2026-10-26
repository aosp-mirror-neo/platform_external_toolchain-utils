# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Mirrors or verifies upstream tarballs for dev-go/staticcheck and its deps.

Queries https://proxy.golang.org for the latest tagged versions of
dev-go/staticcheck and its versioned dev-go/* dependencies in
chromiumos-overlay, and fetches their upstream source tarballs (normalizing
*.googlesource.com archives so their checksums are deterministic).

Why this script targets a fixed subset and fetches @latest for each:
In ChromiumOS, cros-go.eclass builds packages in GOPATH mode (GO111MODULE=off)
and installs a single version (SLOT="0") of each dev-go/* package's source tree
directly into /usr/lib/gopath/src/<importpath>/. Because all Go packages in the
SDK and board overlays share that single source tree without Go module version
selection, upgrading a dev-go/* dependency upgrades it for every reverse
dependency in chromiumos-overlay simultaneously.

Consequently, this script is intentionally scoped to dev-go/staticcheck and its
transitive dependencies (golang.org/x/{tools,mod,sys,sync} and
github.com/BurntSushi/toml) rather than acting as a general Go dependency graph
upgrader. Both the golang.org/x/* repositories and BurntSushi/toml maintain
strong backwards compatibility across minor releases, so pulling @latest for
these packages is expected to satisfy both the newest staticcheck and any older
reverse dependencies in chromiumos-overlay.

Requires one of:
- --download-only: Fetch canonical tarballs and print Manifest lines without
  uploading.
- --upload: Upload any missing tarballs to gs://chromeos-localmirror/distfiles/.
- --verify: Download the tarballs already present on
  gs://chromeos-localmirror/distfiles/ and verify their contents and checksums
  against fresh canonical upstream downloads.
"""

import argparse
import concurrent.futures
import dataclasses
import gzip
import hashlib
import io
import json
import logging
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
from typing import Sequence
import urllib.request
import zlib

from cros_utils import gs


LOCALMIRROR_DISTFILES_ROOT = "gs://chromeos-localmirror/distfiles"
GO_PROXY_BASE_URL = "https://proxy.golang.org"

_UPPERCASE_LETTER_RE = re.compile(r"[A-Z]")
_GO_VERSION_RE = re.compile(r"v[0-9A-Za-z._+-]+")


@dataclasses.dataclass(frozen=True)
class GoPackageSpec:
    """Specification for a CrOS dev-go package mirrored to localmirror."""

    ebuild_name: str
    go_module: str
    cros_go_repopath: str

    def distfile_name(self, version: str) -> str:
        """Returns the localmirror distfile name per cros-go.eclass."""
        repo_dash = self.cros_go_repopath.replace("/", "-")
        return f"{repo_dash}-{version}.tar.gz"

    def gs_url(self, version: str) -> str:
        """Returns the gs:// URL in chromeos-localmirror/distfiles."""
        return f"{LOCALMIRROR_DISTFILES_ROOT}/{self.distfile_name(version)}"

    def upstream_archive_url(self, version: str) -> str:
        """Returns the upstream archive URL per cros-go.eclass."""
        host = self.cros_go_repopath.split("/", 1)[0]
        if host == "github.com":
            return f"https://{self.cros_go_repopath}/archive/{version}.tar.gz"
        if self.is_googlesource:
            return f"https://{self.cros_go_repopath}/+archive/{version}.tar.gz"
        raise ValueError(
            f"Unsupported host in cros_go_repopath: {self.cros_go_repopath!r}"
        )

    @property
    def is_googlesource(self) -> bool:
        """Returns True if this package is hosted on *.googlesource.com."""
        host = self.cros_go_repopath.split("/", 1)[0]
        return host.endswith(".googlesource.com")


# Note: Keep this default list limited to staticcheck and dependencies that
# maintain strong backwards compatibility (golang.org/x/*, BurntSushi/toml).
# Because cros-go.eclass installs a single SLOT="0" version of each package into
# /usr/lib/gopath (GO111MODULE=off), every reverse dependency in
# chromiumos-overlay must build against the single version installed.
STATICCHECK_PACKAGES: tuple[GoPackageSpec, ...] = (
    GoPackageSpec(
        ebuild_name="dev-go/staticcheck",
        go_module="honnef.co/go/tools",
        cros_go_repopath="github.com/dominikh/go-tools",
    ),
    GoPackageSpec(
        ebuild_name="dev-go/go-tools",
        go_module="golang.org/x/tools",
        cros_go_repopath="go.googlesource.com/tools",
    ),
    GoPackageSpec(
        ebuild_name="dev-go/mod",
        go_module="golang.org/x/mod",
        cros_go_repopath="go.googlesource.com/mod",
    ),
    GoPackageSpec(
        ebuild_name="dev-go/go-sys",
        go_module="golang.org/x/sys",
        cros_go_repopath="go.googlesource.com/sys",
    ),
    GoPackageSpec(
        ebuild_name="dev-go/sync",
        go_module="golang.org/x/sync",
        cros_go_repopath="go.googlesource.com/sync",
    ),
    GoPackageSpec(
        ebuild_name="dev-go/toml",
        go_module="github.com/BurntSushi/toml",
        cros_go_repopath="github.com/BurntSushi/toml",
    ),
)


def escape_go_module_path(module_path: str) -> str:
    """Escapes uppercase letters in a Go module path per GOPROXY protocol."""
    return _UPPERCASE_LETTER_RE.sub(
        lambda m: "!" + m.group(0).lower(), module_path
    )


def fetch_latest_version(go_module: str) -> str:
    """Queries proxy.golang.org for the latest tagged version of `go_module`."""
    escaped = escape_go_module_path(go_module)
    url = f"{GO_PROXY_BASE_URL}/{escaped}/@latest"
    logging.debug("Querying %s", url)
    with urllib.request.urlopen(url, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    version = payload.get("Version")
    if not isinstance(version, str) or not _GO_VERSION_RE.fullmatch(version):
        raise ValueError(f"Unexpected version {version!r} returned from {url}")
    return version


def normalize_tar_info(member: tarfile.TarInfo) -> tarfile.TarInfo:
    """Returns a copy of `member` with normalized metadata for determinism."""
    info = tarfile.TarInfo(name=member.name)
    info.type = member.type
    if member.issym():
        info.mode = 0o777
        info.size = 0
    else:
        info.mode = 0o755 if (member.mode & 0o111) else 0o644
        info.size = member.size
    info.linkname = member.linkname
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    return info


def normalize_tar_to_deterministic_tar_gz(src_tar: tarfile.TarFile) -> bytes:
    """Repacks `src_tar` into a deterministic .tar.gz byte string.

    Gitiles (+archive/*.tar.gz) embeds the current server timestamp into every
    tar entry's PAX mtime header, causing raw checksums to differ across
    fetches. Normalizing entries to mtime=0, uid/gid=0, standard modes, and
    sorted order produces identical .tar.gz bytes whether fetched from Gitiles
    or produced by `git archive`.
    """
    # Even the largest package here (golang.org/x/tools) is ~10 MB compressed
    # (~35 MB uncompressed) at most, so repacking in memory is fine.
    out_buf = io.BytesIO()
    written_entries = 0
    with gzip.GzipFile(
        filename="", mode="wb", fileobj=out_buf, mtime=0
    ) as gz_out:
        with tarfile.open(
            fileobj=gz_out, mode="w", format=tarfile.PAX_FORMAT
        ) as dst_tar:
            members = sorted(src_tar.getmembers(), key=lambda m: m.name)
            for member in members:
                if member.isdir():
                    continue
                if not (member.isfile() or member.issym()):
                    raise ValueError(
                        f"Unsupported tar entry type for {member.name!r}"
                    )
                info = normalize_tar_info(member)
                extracted = (
                    src_tar.extractfile(member) if member.isfile() else None
                )
                dst_tar.addfile(info, extracted)
                written_entries += 1
    if not written_entries:
        raise ValueError("Archive contains no files or symlinks")
    return out_buf.getvalue()


def normalize_tar_gz_bytes(raw_tar_gz: bytes) -> bytes:
    """Normalizes a .tar.gz archive in memory into a deterministic .tar.gz."""
    with tarfile.open(fileobj=io.BytesIO(raw_tar_gz), mode="r:gz") as src_tar:
        return normalize_tar_to_deterministic_tar_gz(src_tar)


def _fetch_googlesource_via_git_archive(repo_url: str, version: str) -> bytes:
    """Clones `repo_url` at `version` and produces a normalized .tar.gz."""
    with tempfile.TemporaryDirectory(prefix="cros_go_archive_") as tmp_dir:
        subprocess.run(
            (
                "git",
                "clone",
                "--quiet",
                "--bare",
                "--depth=1",
                f"--branch={version}",
                repo_url,
                tmp_dir,
            ),
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
        )
        archive_proc = subprocess.run(
            ("git", "-C", tmp_dir, "archive", "--format=tar", version),
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
        )
    with tarfile.open(
        fileobj=io.BytesIO(archive_proc.stdout), mode="r:"
    ) as src_tar:
        return normalize_tar_to_deterministic_tar_gz(src_tar)


def fetch_canonical_tarball(spec: GoPackageSpec, version: str) -> bytes:
    """Fetches the canonical tarball for `spec` at `version`.

    For github.com packages, downloads the deterministic archive directly.
    For *.googlesource.com packages, clones the tag via `git clone` + `git
    archive` and normalizes the tar/gzip metadata so the resulting .tar.gz bytes
    are deterministic.
    """
    if spec.is_googlesource:
        repo_url = f"https://{spec.cros_go_repopath}"
        logging.info("Archiving %s@%s via git ...", repo_url, version)
        return _fetch_googlesource_via_git_archive(repo_url, version)

    url = spec.upstream_archive_url(version)
    logging.info("Fetching %s ...", url)
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def format_manifest_line(distfile_name: str, data: bytes) -> str:
    """Formats a Portage Manifest DIST line for `data`."""
    blake2b_hex = hashlib.blake2b(data).hexdigest()
    sha512_hex = hashlib.sha512(data).hexdigest()
    return (
        f"DIST {distfile_name} {len(data)} "
        f"BLAKE2B {blake2b_hex} SHA512 {sha512_hex}"
    )


def try_download_from_gs(gs_url: str) -> bytes | None:
    """Downloads `gs_url` and returns its raw bytes, or None if not present."""
    if not gs.ls(gs_url):
        return None
    with tempfile.TemporaryDirectory(prefix="cros_go_gs_") as tmp_dir:
        dst = Path(tmp_dir) / "archive"
        gs.cp(gs_url, dst)
        return dst.read_bytes()


def upload_to_gs(data: bytes, gs_url: str) -> None:
    """Uploads `data` to `gs_url` with public-read ACL and no-clobber."""
    with tempfile.TemporaryDirectory(prefix="cros_go_upload_") as tmp_dir:
        src = Path(tmp_dir) / "archive"
        src.write_bytes(data)
        gs.cp(src, gs_url, public_read=True)


def verify_tarball_bytes(
    spec: GoPackageSpec,
    version: str,
    gs_bytes: bytes,
    canonical_bytes: bytes,
) -> str:
    """Verifies that `gs_bytes` matches `canonical_bytes`.

    Returns:
        The Portage Manifest DIST line for `gs_bytes`.
    """
    distfile_name = spec.distfile_name(version)
    if gs_bytes == canonical_bytes:
        logging.info(
            "Verified %s: exact byte-for-byte SHA512 match with canonical "
            "download.",
            distfile_name,
        )
        return format_manifest_line(distfile_name, gs_bytes)

    if spec.is_googlesource:
        try:
            normalized_gs_bytes = normalize_tar_gz_bytes(gs_bytes)
        except (tarfile.TarError, EOFError, zlib.error, ValueError):
            normalized_gs_bytes = None
        if normalized_gs_bytes == canonical_bytes:
            logging.info(
                "Verified %s: unpacked archive contents/modes match canonical "
                "download (raw tar headers differ only in Gitiles mtime).",
                distfile_name,
            )
            return format_manifest_line(distfile_name, gs_bytes)

    gs_sha512 = hashlib.sha512(gs_bytes).hexdigest()
    canonical_sha512 = hashlib.sha512(canonical_bytes).hexdigest()
    raise RuntimeError(
        f"Verification failed for {spec.gs_url(version)}: "
        f"gs SHA512 ({gs_sha512}) != canonical SHA512 ({canonical_sha512})"
    )


def verify_package_on_gs(spec: GoPackageSpec, version: str) -> str:
    """Verifies that `spec` at `version` on gs:// matches upstream.

    Returns:
        The Portage Manifest DIST line for the verified tarball on gs://.
    """
    gs_url = spec.gs_url(version)
    logging.info(
        "Verifying %s (%s) at %s ...", spec.ebuild_name, version, gs_url
    )
    gs_bytes = try_download_from_gs(gs_url)
    if gs_bytes is None:
        raise RuntimeError(
            f"Missing {gs_url} on localmirror; run with --upload to upload."
        )

    canonical_bytes = fetch_canonical_tarball(spec, version)
    return verify_tarball_bytes(spec, version, gs_bytes, canonical_bytes)


def mirror_package_to_gs(
    spec: GoPackageSpec, version: str, canonical_bytes: bytes
) -> str:
    """Mirrors `spec` at `version` to gs:// if not already present.

    Returns:
        The Portage Manifest DIST line for the tarball.
    """
    gs_url = spec.gs_url(version)
    distfile_name = spec.distfile_name(version)
    gs_bytes = try_download_from_gs(gs_url)
    if gs_bytes is not None:
        logging.info(
            "%s already exists at %s; verifying existing tarball and skipping "
            "upload.",
            distfile_name,
            gs_url,
        )
        return verify_tarball_bytes(spec, version, gs_bytes, canonical_bytes)

    logging.info("Uploading %s -> %s ...", distfile_name, gs_url)
    upload_to_gs(canonical_bytes, gs_url)
    return format_manifest_line(distfile_name, canonical_bytes)


def select_packages(
    requested_names: Sequence[str] | None,
) -> list[GoPackageSpec]:
    """Filters `STATICCHECK_PACKAGES` by ebuild or short package name."""
    if not requested_names:
        return list(STATICCHECK_PACKAGES)

    by_name: dict[str, GoPackageSpec] = {}
    for spec in STATICCHECK_PACKAGES:
        by_name[spec.ebuild_name] = spec
        short_name = spec.ebuild_name.split("/", 1)[-1]
        by_name[short_name] = spec

    selected: list[GoPackageSpec] = []
    for name in requested_names:
        matched_spec = by_name.get(name)
        if matched_spec is None:
            valid = ", ".join(s.ebuild_name for s in STATICCHECK_PACKAGES)
            raise ValueError(
                f"Unknown package {name!r}; expected one of: {valid}"
            )
        if matched_spec not in selected:
            selected.append(matched_spec)
    return selected


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parses command-line arguments."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        "--download-only",
        action="store_true",
        help="""
        Fetch canonical tarballs and print Manifest lines without uploading to
        gs://.
        """,
    )
    mode_group.add_argument(
        "--upload",
        action="store_true",
        help="""
        Fetch canonical tarballs and upload any missing ones to
        gs://chromeos-localmirror/distfiles/.
        """,
    )
    mode_group.add_argument(
        "--verify",
        action="store_true",
        help="""
        Download existing tarballs from gs://chromeos-localmirror and verify
        them against fresh canonical upstream downloads.
        """,
    )
    parser.add_argument(
        "-p",
        "--package",
        dest="packages",
        action="append",
        help="""
        Optional package(s) to process (e.g. 'dev-go/staticcheck' or
        'staticcheck'). Can be specified multiple times. Defaults to all
        staticcheck packages.
        """,
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging.",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> None:
    """Entry point."""
    opts = parse_args(argv)
    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.DEBUG if opts.debug else logging.INFO,
    )

    specs = select_packages(opts.packages)

    def process_package(spec: GoPackageSpec) -> tuple[GoPackageSpec, str, str]:
        version = fetch_latest_version(spec.go_module)
        logging.info(
            "Latest version for %s (%s): %s",
            spec.ebuild_name,
            spec.go_module,
            version,
        )
        if opts.verify:
            line = verify_package_on_gs(spec, version)
        else:
            canonical_bytes = fetch_canonical_tarball(spec, version)
            if opts.upload:
                line = mirror_package_to_gs(spec, version, canonical_bytes)
            else:
                assert (
                    opts.download_only
                ), "Unhandled mode flag; add a branch for the new flag here."
                line = format_manifest_line(
                    spec.distfile_name(version), canonical_bytes
                )
        return spec, version, line

    if opts.verify:
        # Verification is mostly blocked on network I/O (querying
        # proxy.golang.org, fetching from gs://, and cloning/downloading
        # upstream archives); cap workers at 8 so we do not overload remote
        # services.
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            manifest_lines = list(executor.map(process_package, specs))
    else:
        manifest_lines = [process_package(spec) for spec in specs]

    print("\nManifest entries:")
    for spec, version, line in manifest_lines:
        print(f"# {spec.ebuild_name} ({version})")
        print(line)

    if opts.verify:
        logging.info(
            "All requested packages fetched from localmirror match hashes with "
            "their upstreams. If you'd like, you can also double-check the "
            "Manifest entries above."
        )
