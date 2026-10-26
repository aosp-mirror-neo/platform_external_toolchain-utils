# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for mirror_latest_staticcheck_deps_to_localmirror."""

import gzip
import io
from pathlib import Path
import tarfile
import unittest
from unittest import mock

from cros_utils import gs
from go_tools import mirror_latest_staticcheck_deps_to_localmirror as mirror_sc


def _make_tar_gz(
    entries: list[tuple[str, bytes, int, int, dict[str, str]]],
    gzip_mtime: int = 123456,
) -> bytes:
    """Builds a .tar.gz archive with custom metadata for testing."""
    buf = io.BytesIO()
    with gzip.GzipFile(
        filename="test.tar", mode="wb", fileobj=buf, mtime=gzip_mtime
    ) as gz_out:
        with tarfile.open(
            fileobj=gz_out, mode="w", format=tarfile.PAX_FORMAT
        ) as tar:
            for name, content, mode, mtime, pax_headers in entries:
                info = tarfile.TarInfo(name=name)
                info.size = len(content)
                info.mode = mode
                info.mtime = mtime
                info.uid = 1000
                info.gid = 1000
                info.uname = "testuser"
                info.gname = "testgroup"
                info.pax_headers = pax_headers
                tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


class Test(unittest.TestCase):
    """Tests for mirror_latest_staticcheck_deps_to_localmirror."""

    def test_escape_go_module_path(self) -> None:
        self.assertEqual(
            mirror_sc.escape_go_module_path("github.com/BurntSushi/toml"),
            "github.com/!burnt!sushi/toml",
        )
        self.assertEqual(
            mirror_sc.escape_go_module_path("golang.org/x/tools"),
            "golang.org/x/tools",
        )

    def test_go_package_spec_urls(self) -> None:
        gh_spec = mirror_sc.GoPackageSpec(
            ebuild_name="dev-go/staticcheck",
            go_module="honnef.co/go/tools",
            cros_go_repopath="github.com/dominikh/go-tools",
        )
        self.assertFalse(gh_spec.is_googlesource)
        self.assertEqual(
            gh_spec.distfile_name("v0.8.1"),
            "github.com-dominikh-go-tools-v0.8.1.tar.gz",
        )
        self.assertEqual(
            gh_spec.gs_url("v0.8.1"),
            "gs://chromeos-localmirror/distfiles/"
            "github.com-dominikh-go-tools-v0.8.1.tar.gz",
        )
        self.assertEqual(
            gh_spec.upstream_archive_url("v0.8.1"),
            "https://github.com/dominikh/go-tools/archive/v0.8.1.tar.gz",
        )

        gob_spec = mirror_sc.GoPackageSpec(
            ebuild_name="dev-go/go-tools",
            go_module="golang.org/x/tools",
            cros_go_repopath="go.googlesource.com/tools",
        )
        self.assertTrue(gob_spec.is_googlesource)
        self.assertEqual(
            gob_spec.distfile_name("v0.50.0"),
            "go.googlesource.com-tools-v0.50.0.tar.gz",
        )
        self.assertEqual(
            gob_spec.upstream_archive_url("v0.50.0"),
            "https://go.googlesource.com/tools/+archive/v0.50.0.tar.gz",
        )

    def test_normalize_tar_gz_bytes_is_deterministic(self) -> None:
        tar1 = _make_tar_gz(
            [
                ("b.go", b"package b\n", 0o664, 1700000001, {"mtime": "1.1"}),
                ("a.sh", b"#!/bin/sh\n", 0o775, 1700000002, {"mtime": "2.2"}),
            ],
            gzip_mtime=1700000001,
        )
        tar2 = _make_tar_gz(
            [
                ("a.sh", b"#!/bin/sh\n", 0o755, 1800000999, {"mtime": "9.9"}),
                ("b.go", b"package b\n", 0o644, 1800000888, {"mtime": "8.8"}),
            ],
            gzip_mtime=1800000999,
        )
        self.assertNotEqual(tar1, tar2)
        norm1 = mirror_sc.normalize_tar_gz_bytes(tar1)
        norm2 = mirror_sc.normalize_tar_gz_bytes(tar2)
        self.assertEqual(norm1, norm2)

    def test_normalize_tar_gz_bytes_rejects_empty_or_unsupported_entries(
        self,
    ) -> None:
        empty_tar = _make_tar_gz([])
        with self.assertRaisesRegex(ValueError, "contains no files"):
            mirror_sc.normalize_tar_gz_bytes(empty_tar)

        buf = io.BytesIO()
        with tarfile.open(
            fileobj=buf, mode="w:gz", format=tarfile.PAX_FORMAT
        ) as tar:
            fifo = tarfile.TarInfo(name="pipe")
            fifo.type = tarfile.FIFOTYPE
            tar.addfile(fifo)
        with self.assertRaisesRegex(ValueError, "Unsupported tar entry type"):
            mirror_sc.normalize_tar_gz_bytes(buf.getvalue())

    @mock.patch.object(mirror_sc, "upload_to_gs", autospec=True)
    @mock.patch.object(mirror_sc, "try_download_from_gs", autospec=True)
    def test_mirror_package_to_gs_uploads_when_missing(
        self,
        mock_try_download: mock.MagicMock,
        mock_upload: mock.MagicMock,
    ) -> None:
        spec = mirror_sc.STATICCHECK_PACKAGES[0]
        mock_try_download.return_value = None

        line = mirror_sc.mirror_package_to_gs(spec, "v0.8.1", b"tarball-bytes")
        self.assertTrue(
            line.startswith(
                "DIST github.com-dominikh-go-tools-v0.8.1.tar.gz 13 BLAKE2B "
            )
        )
        mock_upload.assert_called_once_with(
            b"tarball-bytes",
            "gs://chromeos-localmirror/distfiles/"
            "github.com-dominikh-go-tools-v0.8.1.tar.gz",
        )

    @mock.patch.object(mirror_sc, "upload_to_gs", autospec=True)
    @mock.patch.object(mirror_sc, "try_download_from_gs", autospec=True)
    def test_mirror_package_to_gs_verifies_and_skips_when_already_present(
        self,
        mock_try_download: mock.MagicMock,
        mock_upload: mock.MagicMock,
    ) -> None:
        spec = mirror_sc.STATICCHECK_PACKAGES[2]  # dev-go/mod
        unnormalized_gs = _make_tar_gz(
            [("go.mod", b"module golang.org/x/mod\n", 0o644, 1799999999, {})],
            gzip_mtime=1799999999,
        )
        canonical = mirror_sc.normalize_tar_gz_bytes(unnormalized_gs)
        mock_try_download.return_value = unnormalized_gs

        line = mirror_sc.mirror_package_to_gs(spec, "v0.41.0", canonical)
        mock_upload.assert_not_called()
        self.assertEqual(
            line,
            mirror_sc.format_manifest_line(
                "go.googlesource.com-mod-v0.41.0.tar.gz", unnormalized_gs
            ),
        )

    def test_verify_tarball_bytes_accepts_normalized_googlesource_tarball(
        self,
    ) -> None:
        spec = mirror_sc.STATICCHECK_PACKAGES[2]  # dev-go/mod
        unnormalized_gs = _make_tar_gz(
            [("go.mod", b"module golang.org/x/mod\n", 0o644, 1799999999, {})],
            gzip_mtime=1799999999,
        )
        canonical = mirror_sc.normalize_tar_gz_bytes(unnormalized_gs)

        line = mirror_sc.verify_tarball_bytes(
            spec, "v0.41.0", unnormalized_gs, canonical
        )
        self.assertEqual(
            line,
            mirror_sc.format_manifest_line(
                "go.googlesource.com-mod-v0.41.0.tar.gz", unnormalized_gs
            ),
        )

    def test_verify_tarball_bytes_fails_on_content_mismatch_or_corruption(
        self,
    ) -> None:
        gh_spec = mirror_sc.STATICCHECK_PACKAGES[0]
        with self.assertRaisesRegex(RuntimeError, "Verification failed"):
            mirror_sc.verify_tarball_bytes(
                gh_spec, "v0.8.1", b"corrupted-bytes", b"canonical-bytes"
            )

        gob_spec = mirror_sc.STATICCHECK_PACKAGES[2]  # dev-go/mod
        with self.assertRaisesRegex(RuntimeError, "Verification failed"):
            mirror_sc.verify_tarball_bytes(
                gob_spec, "v0.41.0", b"not-a-valid-tar-gz", b"canonical-bytes"
            )

    def test_select_packages(self) -> None:
        self.assertEqual(
            mirror_sc.select_packages(None),
            list(mirror_sc.STATICCHECK_PACKAGES),
        )
        selected = mirror_sc.select_packages(["staticcheck", "dev-go/mod"])
        self.assertEqual(
            [s.ebuild_name for s in selected],
            ["dev-go/staticcheck", "dev-go/mod"],
        )
        with self.assertRaisesRegex(ValueError, "Unknown package"):
            mirror_sc.select_packages(["nonexistent"])

    @mock.patch.object(gs, "cp", autospec=True)
    @mock.patch.object(gs, "ls", autospec=True)
    def test_try_download_from_gs_returns_none_when_missing(
        self,
        mock_ls: mock.MagicMock,
        mock_cp: mock.MagicMock,
    ) -> None:
        mock_ls.return_value = []
        self.assertIsNone(
            mirror_sc.try_download_from_gs("gs://bucket/missing.tar.gz")
        )
        mock_ls.assert_called_once_with("gs://bucket/missing.tar.gz")
        mock_cp.assert_not_called()

    @mock.patch.object(gs, "cp", autospec=True)
    @mock.patch.object(gs, "ls", autospec=True)
    def test_try_download_from_gs_downloads_when_present(
        self,
        mock_ls: mock.MagicMock,
        mock_cp: mock.MagicMock,
    ) -> None:
        mock_ls.return_value = [
            gs.GsEntry(
                last_modified=None,
                gs_path="gs://bucket/present.tar.gz",
            )
        ]

        def fake_cp(src: gs.PathOrStr, dst: gs.PathOrStr) -> None:
            self.assertEqual(src, "gs://bucket/present.tar.gz")
            Path(dst).write_bytes(b"downloaded-bytes")

        mock_cp.side_effect = fake_cp
        self.assertEqual(
            mirror_sc.try_download_from_gs("gs://bucket/present.tar.gz"),
            b"downloaded-bytes",
        )
        mock_cp.assert_called_once()

    @mock.patch.object(gs, "cp", autospec=True)
    def test_upload_to_gs_writes_bytes_and_uploads_public_read(
        self,
        mock_cp: mock.MagicMock,
    ) -> None:
        uploaded_bytes: list[bytes] = []

        def fake_cp(
            src: gs.PathOrStr,
            dst: gs.PathOrStr,
            *,
            public_read: bool = False,
        ) -> None:
            self.assertEqual(dst, "gs://bucket/out.tar.gz")
            self.assertTrue(public_read)
            uploaded_bytes.append(Path(src).read_bytes())

        mock_cp.side_effect = fake_cp
        mirror_sc.upload_to_gs(b"upload-payload", "gs://bucket/out.tar.gz")
        self.assertEqual(uploaded_bytes, [b"upload-payload"])
        mock_cp.assert_called_once()

    def test_parse_args_requires_mode(self) -> None:
        with self.assertRaises(SystemExit):
            mirror_sc.parse_args([])
        with self.assertRaises(SystemExit):
            mirror_sc.parse_args(["--download-only", "--upload"])
        with self.assertRaises(SystemExit):
            mirror_sc.parse_args(["--download-only", "--verify"])
        self.assertTrue(mirror_sc.parse_args(["--download-only"]).download_only)
        self.assertTrue(mirror_sc.parse_args(["--upload"]).upload)
        self.assertTrue(mirror_sc.parse_args(["--verify"]).verify)
