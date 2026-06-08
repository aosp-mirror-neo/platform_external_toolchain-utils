# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Utilities for interacting with Gerrit."""

import dataclasses
import enum
import json
import logging
import re
import subprocess
from typing import Any, Self
import urllib.parse


ANDROID_INTERNAL_GERRIT_HOST = (
    "https://googleplex-android-review.git.corp.google.com"
)


class CLStatus(enum.Enum):
    """The status of a CL."""

    NEW = "NEW"
    MERGED = "MERGED"
    ABANDONED = "ABANDONED"

    def is_open(self) -> bool:
        """Returns whether the CL is open."""
        return self == self.NEW

    @classmethod
    def parse(cls, status: str) -> Self:
        """Parses a CL status from a string."""
        for member in cls:
            if member.value == status:
                return member
        raise ValueError(f"Unknown CL status: {status!r}")


@dataclasses.dataclass(frozen=True)
class RelatedChangeInfo:
    """Details of a related change from Gerrit."""

    project: str
    cl_number: int
    status: CLStatus


def fetch_gob_curl_body_with_retries(url: str) -> str:
    """Runs gob-curl, returning its output as a `str`.

    Retries to work around Gerrit flakes, if any.
    """
    max_tries = 5
    i = 1
    while True:
        result = subprocess.run(
            (
                "gob-curl",
                # Follow redirects.
                "--location",
                # Exit with nonzero code if the response code indicates failure.
                "--fail",
                url,
            ),
            check=False,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if not result.returncode:
            return result.stdout

        logging.warning(
            "Failed attempt %d/%d running gob-curl on %s; "
            "stdout:\n%s\n\nstderr:\n%s",
            i,
            max_tries,
            url,
            result.stdout,
            result.stderr,
        )
        # Reraise if we're at the limit, but make sure to log stdout/stderr
        # above so they're not dropped.
        if i == max_tries:
            result.check_returncode()
        i += 1


def parse_gerrit_response(response_body: str) -> Any:
    """Parses a JSON response from Gerrit, stripping the prefix."""
    json_response_pre = ")]}'"
    if not response_body.startswith(json_response_pre):
        raise ValueError(
            f"Unexpected non-JSON Gerrit response: {response_body!r}"
        )
    return json.loads(response_body[len(json_response_pre) :])


def fetch_related_changes(
    gerrit_host: str, change_id: int
) -> list[RelatedChangeInfo]:
    """Fetches related changes for a given change."""
    logging.info("Fetching related changes for %d", change_id)
    url = f"{gerrit_host}/changes/{change_id}/revisions/current/related"
    response_body = fetch_gob_curl_body_with_retries(url)
    related = parse_gerrit_response(response_body)

    changes = related.get("changes")
    if not changes:
        return []

    results = []
    for change in changes:
        project = change.get("project")
        cl_number = change.get("_change_number")
        status_str = change.get("status")
        if not all((project, cl_number, status_str)):
            logging.warning("Related change %s is missing fields", change)
            continue

        status = CLStatus.parse(status_str)
        results.append(
            RelatedChangeInfo(
                project=project, cl_number=cl_number, status=status
            )
        )
    return results


@dataclasses.dataclass(frozen=True, eq=True)
class ChangeListURL:
    """A consistent representation of a CL URL.

    The __str__ always converts to a crrev.com URL for ChromeOS,
    or ag/ URL for Android.
    """

    cl_id: int
    patch_set: int | None = None
    internal: bool = False
    android: bool = False

    # Matches `/+/CL_NUM/MAYBE_PATCHSET`; used for Gerrit's long-form URL paths.
    # Note that things like file paths may appear after these if the user copied
    # the URL while looking at a specific file in the patchset.
    _LONG_FORM_RE = re.compile(r"/\+/(\d+)(?:/(\d+))?(?:/|$)")
    # Matches shorter-form URL paths, including:
    #
    # - /c/CL_NUM/MAYBE_PATCHSET
    # - /i/CL_NUM/MAYBE_PATCHSET
    # - /CL_NUM/MAYBE_PATCHSET
    #
    # Same note about a file path potentially being present afterwards.
    _SHORT_FORM_RE = re.compile(r"^/(?:c/|i/)?(\d+)(?:/(\d+))?(?:/|$)")

    @classmethod
    def parse(cls, url: str) -> Self:
        orig_url = url
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        parsed = urllib.parse.urlparse(url)
        netloc = parsed.netloc
        path = parsed.path

        if parsed.fragment.startswith("/c/"):
            path = parsed.fragment

        # Clean path from GET params that leaked into path (e.g. split on &)
        path = path.split("&")[0]

        android = False
        internal = False

        if netloc == "ag":
            android = True
            internal = True
        elif netloc == "go" and path.startswith("/ag/"):
            android = True
            internal = True
            path = path.removeprefix("/ag")
        elif netloc == "googleplex-android-review.git.corp.google.com":
            android = True
            internal = True
        elif netloc == "crrev.com":
            if path.startswith("/i/"):
                internal = True
            elif not path.startswith("/c/"):
                raise ValueError(
                    f"URL {orig_url!r} was not recognized: "
                    f"invalid crrev.com path {path!r}"
                )
        elif netloc in (
            "chrome-internal-review.googlesource.com",
            "chrome-internal-review.git.corp.google.com",
        ):
            internal = True
        elif netloc in (
            "chromium-review.googlesource.com",
            "chromium-review.git.corp.google.com",
        ):
            internal = False
        else:
            raise ValueError(
                f"URL {orig_url!r} was not recognized: "
                f"unrecognized host {netloc!r}"
            )

        if "/+/" in path:
            match = cls._LONG_FORM_RE.search(path)
        else:
            match = cls._SHORT_FORM_RE.match(path)

        if match:
            cl_id, patch_set = match.groups()
        else:
            raise ValueError(
                f"URL {orig_url!r} was not recognized: "
                f"could not parse CL ID from path {path!r}"
            )

        return cls(
            cl_id=int(cl_id),
            patch_set=int(patch_set) if patch_set else None,
            internal=internal,
            android=android,
        )

    @classmethod
    def parse_with_patch_set(cls, url: str) -> Self:
        """parse(), but raises a ValueError if no patchset is specified."""
        result = cls.parse(url)
        if result.patch_set is None:
            raise ValueError("A patchset number must be specified.")
        return result

    def shorthand_url_without_http(self) -> str:
        if self.android:
            result = f"ag/{self.cl_id}"
        else:
            namespace = "i" if self.internal else "c"
            result = f"crrev.com/{namespace}/{self.cl_id}"
        if self.patch_set is not None:
            result += f"/{self.patch_set}"
        return result

    @property
    def gerrit_tool_id(self) -> str:
        """Returns an identifier for this CL for use with the 'gerrit' tool."""
        return f"*{self.cl_id}" if self.internal else f"{self.cl_id}"

    @property
    def gerrit_host(self) -> str:
        """Returns the Gerrit host URL for this CL."""
        if self.android:
            return ANDROID_INTERNAL_GERRIT_HOST
        if self.internal:
            return "https://chrome-internal-review.googlesource.com"
        return "https://chromium-review.googlesource.com"

    def __str__(self) -> str:
        return f"https://{self.shorthand_url_without_http()}"
