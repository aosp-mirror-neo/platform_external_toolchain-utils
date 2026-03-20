# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Utilities for interacting with gs://."""

import contextlib
import dataclasses
import datetime
import io
import logging
import re
import shlex
import shutil
import subprocess
import tempfile
from typing import Generator, IO


# Determine which gsutil to use.
# In some environments (host machines), `gsutil` is provided on $PATH. If that
# fails and depot_tools is on $PATH, gsutil.py also works.
GSUTIL = "gsutil" if shutil.which("gsutil") else "gsutil.py"


@dataclasses.dataclass(frozen=True)
class GsEntry:
    """An entry of `gsutil ls -l` output."""

    # When this was last modified (or created). `None` if the entry is a
    # directory.
    last_modified: datetime.datetime | None
    # The full gs:// path to the artifact.
    gs_path: str


def _datetime_from_gs_time(timestamp_str: str) -> datetime.datetime:
    """Parses a datetime from gs."""
    return datetime.datetime.strptime(
        timestamp_str, "%Y-%m-%dT%H:%M:%SZ"
    ).replace(tzinfo=datetime.timezone.utc)


def _parse_ls_output(stdout: str) -> list[GsEntry]:
    """Parses output of `gsutil ls`."""
    stdout_lines = stdout.splitlines()
    # Ignore the last line, since that's always "TOTAL:"
    stdout_lines.pop()

    line_re = re.compile(
        # Entries can take one of two forms:
        r"(?:"
        # 1. The entry has a size, mod date, and name
        r"\d+\s+(\S+T\S+)\s+(gs://.+)"
        r"|"
        # 2. The entry has none of those, and is just a gs URL.
        r"(gs://.+)"
        r")"
    )
    results = []

    for line in stdout_lines:
        # If the line starts with gs://, it's a header for a directory's
        # contents. Skip it.
        if line.startswith("gs://"):
            continue

        line = line.strip()
        if not line:
            continue
        m = line_re.fullmatch(line)
        if m is None:
            raise ValueError(f"Unexpected line from gs: {line!r}")
        timestamp_str, gs_url, alt_gs_url = m.groups()
        if timestamp_str:
            last_modified = _datetime_from_gs_time(timestamp_str)
            gs_path = gs_url
        else:
            last_modified = None
            gs_path = alt_gs_url
        results.append(GsEntry(last_modified=last_modified, gs_path=gs_path))
    return results


def ls(gs_url: str) -> list[GsEntry]:
    """Runs `gsutil ls` on the given `path`.

    Globs are forwarded to gs://

    Returns:
        A list of GsEntrys matching `path`. If the list is entry, no paths
        matched the URL.
    """
    cmd = (
        GSUTIL,
        "ls",
        "-l",
        gs_url,
    )
    result = subprocess.run(
        cmd,
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
    )

    if result.returncode:
        # If nothing could be found, gsutil will exit after printing this.
        if "One or more URLs matched no objects." in result.stderr:
            return []
        logging.error("%s failed; stderr:\n%s", shlex.join(cmd), result.stderr)
        result.check_returncode()
        assert False, "unreachable"
    return _parse_ls_output(result.stdout)


# Please keep this subclass around even if nothing matches on it; its name
# is much more clear and actionable than the error that `gsutil` writes to
# stderr if it refuses to overwrite a file.
class GsutilOverwriteExistingFileError(subprocess.CalledProcessError):
    """Raised if gsutil refused to overwrite a specific file."""


@contextlib.contextmanager
def streaming_upload_to(
    destination: str, overwrite: bool = False
) -> Generator[IO[bytes], None, None]:
    """Allows you to stream a file to gs at `destination`.

    If an exception is thrown while in the contextmanager, the upload is
    aborted.

    The upload is both all-or-nothing and atomic: no intermediate state is
    ever exposed, and if the upload is aborted, clients never observe the
    attempt to upload.

    Examples:
        >>> with streaming_upload_to("gs://bucket/does/not/exist") as stream:
        ...   json.dump(my_object, stream)

    Raises:
        subprocess.CalledProcessError if the `gsutil` upload failed.
        GsutilOverwriteExistingFileError if the upload failed because
          `overwrite` is False, and the file already exists on gs://.
    """
    cmd = [GSUTIL]
    if not overwrite:
        # This flag has the command fail if we attempt to overwrite an existing
        # file. This is in contrast to `gsutil cp -n`, which just logs a message
        # about skipping the file & exits successfully.
        cmd += ("-h", "x-goog-if-generation-match: 0")
    cmd += ("cp", "-", destination)

    # gs' stdout/stderr is piped to a temp file, so we don't have to worry about
    # spawning a thread or something to buffer it.
    with tempfile.TemporaryFile(prefix="gs_streaming_upload") as stdstreams:
        with subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=stdstreams,
            stderr=stdstreams,
        ) as proc:
            stdin = proc.stdin
            assert stdin  # assures mypy that stdin cannot be None

            try:
                yield stdin
            except:
                logging.warning(
                    "Exception caught during upload to %s; aborting upload...",
                    destination,
                )
                # Kill `gs` if something was raised; we are very likely to not
                # want to commit what was uploaded, and gs commits the upload
                # when `stdin` gets closed.
                proc.kill()
                # Wait before closing stdin. If killing takes more than a few
                # seconds, something went wrong.
                proc.wait(timeout=5)
                # Since there's some other error that motivated this, don't
                # print `stdstreams`.
                raise

            stdin.close()
            returncode = proc.wait()

            # Seek is required because the child wrote to this file, which
            # advanced our fd's file offset.
            stdstreams.seek(0)
            stdout_and_stderr = stdstreams.read().decode(
                encoding="utf-8", errors="replace"
            )

            if not returncode:
                logging.info(
                    "gsutil upload to %s output: %s",
                    destination,
                    stdout_and_stderr,
                )
                return

            if (
                overwrite
                # `ResumableUploadAbortException: 412` means that the
                # precondition we specified above via `-h` failed, AKA the file
                # exists.
                or "ResumableUploadAbortException: 412" not in stdout_and_stderr
            ):
                logging.error(
                    "gsutil upload to %s failed; output: %s",
                    destination,
                    stdout_and_stderr,
                )
                raise subprocess.CalledProcessError(
                    returncode, cmd, stdout_and_stderr
                )
            # Don't write stdout_and_stderr in this case; it's likely to just
            # add noise.
            raise GsutilOverwriteExistingFileError(
                returncode, cmd, stdout_and_stderr
            )


@contextlib.contextmanager
def streaming_encoded_upload_to(
    destination: str, encoding: str = "utf-8", overwrite: bool = False
) -> Generator[IO[str], None, None]:
    """`streaming_upload_to`, but wrapped to produce an IO[str]."""
    with streaming_upload_to(destination, overwrite=overwrite) as sink:
        # Subtle: this can't just be `yield io.TextIOWrapper(...)`
        # - `io.TextIOWrapper` will `close()` the underlying stream on `__del__`
        # - If the caller `raise`s and doesn't hold a reference to the wrapper,
        #   this means that stdin for gs may get closed right before we
        #   `kill()` it.
        # - This means there's a race between the `close()` committing the gs
        #   write, and the `kill()` aborting it.
        #
        # `detach()` solves these issues by keeping `close()`'s responsibility
        # with `streaming_upload_to()`
        w = io.TextIOWrapper(sink, encoding=encoding)
        try:
            yield w
        finally:
            w.detach()
