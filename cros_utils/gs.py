# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Utilities for interacting with gs://."""

from collections.abc import Generator
import contextlib
import dataclasses
import datetime
import io
import logging
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from typing import IO, TypeVar


# Determine which gsutil to use.
# In some environments (host machines), `gsutil` is provided on $PATH. If that
# fails and depot_tools is on $PATH, gsutil.py also works.
GSUTIL = "gsutil" if shutil.which("gsutil") else "gsutil.py"

PathOrStr = str | os.PathLike
_PathOrStrT = TypeVar("_PathOrStrT", bound=PathOrStr)

_LS_LINE_RE = re.compile(
    # Entries may start with a size and mod date before the gs:// URL, or
    # consist solely of a gs:// URL.
    r"(?:\d+\s+(\S+T\S+)\s+)?(gs://.+)"
)


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
    if not stdout_lines:
        return []
    # `gsutil ls -l` ends with a "TOTAL:" summary line when any files are
    # listed, but omits it when only subdirectories are listed.
    if stdout_lines[-1].strip().startswith("TOTAL:"):
        stdout_lines.pop()

    results = []

    for line in stdout_lines:
        # If the line starts with gs://, it's a header for a directory's
        # contents. Skip it.
        if line.startswith("gs://"):
            continue

        line = line.strip()
        if not line:
            continue
        m = _LS_LINE_RE.fullmatch(line)
        if m is None:
            raise ValueError(f"Unexpected line from gs: {line!r}")
        timestamp_str, gs_path = m.groups()
        if timestamp_str:
            last_modified = _datetime_from_gs_time(timestamp_str)
        else:
            last_modified = None
        results.append(GsEntry(last_modified=last_modified, gs_path=gs_path))
    return results


def ls(gs_url: str, *args: str) -> list[GsEntry]:
    """Runs `gsutil ls` on the given URL(s).

    Globs are forwarded to gs://

    Args:
        gs_url: The first URL to list.
        *args: Additional URLs to list.

    Returns:
        A list of GsEntrys matching the given URL(s). If the list is empty, no
        paths matched the URL(s).
    """
    cmd = (
        GSUTIL,
        *(("-m",) if args else ()),
        "ls",
        "-l",
        gs_url,
        *args,
    )
    result = subprocess.run(
        cmd,
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
    )

    if result.returncode:
        # If one or more URLs could not be found, gsutil will exit 1 after
        # printing this to stderr (while still writing any matched objects to
        # stdout when multiple URLs are passed).
        if "One or more URLs matched no objects." not in result.stderr:
            logging.error(
                "%s failed; stderr:\n%s", shlex.join(cmd), result.stderr
            )
            result.check_returncode()
    return _parse_ls_output(result.stdout)


def build_cp_many_command(
    sources: list[_PathOrStrT] | tuple[PathOrStr, ...],
    dest: PathOrStr,
    *,
    recursive: bool = False,
    overwrite: bool = False,
    public_read: bool = False,
) -> list[str]:
    """Builds a command to copy `sources` to `dest`."""
    if not sources:
        raise ValueError("sources must not be empty")

    cmd = [GSUTIL]
    if recursive or len(sources) > 1:
        cmd.append("-m")
    cmd.append("cp")
    if recursive:
        cmd.append("-r")
    if not overwrite:
        cmd.append("-n")
    if public_read:
        cmd.extend(("-a", "public-read"))
    cmd.extend(os.fspath(x) for x in sources)
    cmd.append(os.fspath(dest))
    return cmd


def build_cp_command(
    source: PathOrStr,
    dest: PathOrStr,
    *,
    recursive: bool = False,
    overwrite: bool = False,
    public_read: bool = False,
) -> list[str]:
    """Builds a command to copy `source` to `dest`."""
    return build_cp_many_command(
        (source,),
        dest,
        recursive=recursive,
        overwrite=overwrite,
        public_read=public_read,
    )


def cp_many(
    sources: list[_PathOrStrT] | tuple[PathOrStr, ...],
    dest: PathOrStr,
    *,
    recursive: bool = False,
    overwrite: bool = False,
    public_read: bool = False,
) -> None:
    """Copies `sources` to `dest` using `gsutil cp`."""
    cmd = build_cp_many_command(
        sources,
        dest,
        recursive=recursive,
        overwrite=overwrite,
        public_read=public_read,
    )
    try:
        subprocess.run(
            cmd,
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.CalledProcessError as e:
        logging.error(
            "%s failed; stdout:\n%s\nstderr:\n%s",
            shlex.join(cmd),
            e.stdout,
            e.stderr,
        )
        raise


def cp(
    source: PathOrStr,
    dest: PathOrStr,
    *,
    recursive: bool = False,
    overwrite: bool = False,
    public_read: bool = False,
) -> None:
    """Copies `source` to `dest` using `gsutil cp`."""
    cp_many(
        (source,),
        dest,
        recursive=recursive,
        overwrite=overwrite,
        public_read=public_read,
    )


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
