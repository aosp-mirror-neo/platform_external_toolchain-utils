# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Utilities for interacting with Gerrit."""

import concurrent.futures
import dataclasses
import enum
import json
import logging
import re
import subprocess
import threading
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


@dataclasses.dataclass(frozen=True)
class CLDetails:
    """CL details fetched from Gerrit."""

    project: str
    cl_url: ChangeListURL
    status: CLStatus

    @property
    def cl_number(self) -> int:
        return self.cl_url.cl_id


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


def fetch_related_changes(gerrit_host: str, change_id: int) -> list[CLDetails]:
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

        cl_url = ChangeListURL.parse(f"{gerrit_host}/{cl_number}")
        status = CLStatus.parse(status_str)
        results.append(CLDetails(project=project, cl_url=cl_url, status=status))
    return results


def resolve_and_sort_cl_dependencies(
    cls: list[CLDetails],
    gerrit_host: str,
    executor: concurrent.futures.ThreadPoolExecutor,
) -> list[CLDetails]:
    """Resolves and sorts all CL dependencies."""
    # So Gerrit's relation chains list every CL with some sort of parent-child
    # relationship to any other CL in the same relation chain.
    #
    # That means if we have a tree of CLs:
    #   - A is the parent of B
    #   - A is the parent of C
    #   - C is the parent of D
    #
    # Then getting the relation chain for _any_ of these CLs will get the
    # relation chain for _all_ of these CLs. The ordering in this list of B and
    # C will be indeterminate, _but_ since A is the central parent, it is
    # guaranteed to be before all of the other CLs (and C is guaranteed to be
    # before D).
    #
    # The idea here is then pretty simple: grab all unique relation chains
    # (where an empty relation chain for CL E is just a relation chain of E),
    # chop out obviously unnecessary entries, and then return a flattened list
    # of relation chains.
    #
    # The "unnecessary entries" are elements that extend past the end of any
    # element in `cls`. So going back to the above example, if `cls` just
    # contained C, we would capture either [A, B, C], or [A, C], depending on
    # how Gerrit sorted it.
    #
    # TODO: This _does_ mean that B _may_ be included when it shouldn't, but
    # scanning to figure that out is a bit of a pain.

    # All of this state is protected by `lock`.
    cl_map = {cl.cl_number: cl for cl in cls}
    processed_cl_numbers = set()
    all_chains: list[list[CLDetails]] = []

    lock = threading.Lock()

    def _fetch_dep_chain(cl_detail: CLDetails) -> None:
        """Fetches the dependency chain for the given CL.

        Updates captured state above appropriately.
        """
        with lock:
            # As mentioned above, multiple CLs in the same chain will return the
            # same chain. Skip this request if we've seen this CL during another
            # request.
            if cl_detail.cl_number in processed_cl_numbers:
                return

        chain_info = fetch_related_changes(gerrit_host, cl_detail.cl_number)

        # Note that chains are returned in order from children to parents. For
        # simplicity later, make it parents-first.
        chain_info.reverse()

        with lock:
            # It could be that we had racing `fetch_related_changes` invocations
            # for the same chain; bail if we've already processed this chain.
            if cl_detail.cl_number in processed_cl_numbers:
                return

            if not chain_info:
                processed_cl_numbers.add(cl_detail.cl_number)
                all_chains.append([cl_detail])
                return

            # Truncate the chain to the child-most CL that was actually
            # requested.
            child_most_cl_idx = next(
                (
                    i
                    for i in reversed(range(len(chain_info)))
                    if chain_info[i].cl_number in cl_map
                ),
                None,
            )
            assert child_most_cl_idx is not None, (
                "Could not find any of the requested CLs in the relation "
                f"chain for {cl_detail.cl_number}."
            )
            del chain_info[child_most_cl_idx + 1 :]

            processed_cl_numbers.update(c.cl_number for c in chain_info)

            current_chain: list[CLDetails] = []
            for related_cl_info in chain_info:
                status = related_cl_info.status
                if not status.is_open():
                    logging.info(
                        "Skipping CL %d with status %s from a relation chain.",
                        related_cl_info.cl_number,
                        status.value,
                    )
                    continue

                cl_to_add = cl_map.get(related_cl_info.cl_number)
                if not cl_to_add:
                    logging.info(
                        "Discovered new, unmerged CL %d from relation chain "
                        "of %d",
                        related_cl_info.cl_number,
                        cl_detail.cl_number,
                    )
                    cl_to_add = related_cl_info
                    cl_map[related_cl_info.cl_number] = cl_to_add

                current_chain.append(cl_to_add)

            if current_chain:
                all_chains.append(current_chain)

    logging.info("Resolving CL dependencies using relation chains...")
    futures = [executor.submit(_fetch_dep_chain, cl) for cl in cls]
    for f in futures:
        # `f.result()` reraises any exception the future encountered.
        f.result()

    # The chain ordering will be deterministic (sourced from Gerrit), but
    # threads will race to add to this list. Sort by the CL number for
    # determinism.
    all_chains.sort(key=lambda chain: chain[0].cl_number)
    logging.debug("Final CL chains after parent resolution: %s", all_chains)

    seen = set()
    deduped_cls = []
    for chain in all_chains:
        for cl in chain:
            if cl.cl_number not in seen:
                seen.add(cl.cl_number)
                deduped_cls.append(cl)
    return deduped_cls


def fetch_cls_for_topic(gerrit_host: str, topic: str) -> list[CLDetails]:
    """Fetches CL details for a given topic."""
    # https://gerrit-review.googlesource.com/Documentation/rest-api-changes.html#list-changes
    # Include `is:open` under the assumption that merged/abandoned things are
    # undesirable to cherrypick.
    encoded_query = urllib.parse.urlencode({"q": f'topic:"{topic}" is:open'})
    url = f"{gerrit_host}/changes/?{encoded_query}"
    response_body = fetch_gob_curl_body_with_retries(url)

    changes = parse_gerrit_response(response_body)
    results = []
    for change in changes:
        project = change.get("project")
        cl_number = change.get("_number")
        if not project or not cl_number:
            logging.warning("Change %s is missing project or number", change)
            continue
        status_str = change.get("status")
        status = CLStatus.parse(status_str) if status_str else CLStatus.NEW
        cl_url = ChangeListURL.parse(f"{gerrit_host}/{cl_number}")
        results.append(CLDetails(project=project, cl_url=cl_url, status=status))
    return results


def _get_cherry_pick_command(fetch_info: dict) -> str | None:
    """Gets the cherry-pick command from the fetch_info dictionary."""

    def try_get_cmd(key: str) -> str | None:
        if obj := fetch_info.get(key):
            if commands := obj.get("commands"):
                if cherry_pick := commands.get("Cherry Pick"):
                    return cherry_pick
        return None

    # Prefer sso if possible, since that's git.
    if cmd := try_get_cmd("sso"):
        return cmd

    # Otherwise, take what we can get.
    for key in fetch_info:
        if cmd := try_get_cmd(key):
            return cmd

    return None


def fetch_cherry_pick_command(gerrit_host: str, change_id: str) -> str | None:
    """Fetches the cherry-pick command for a given change."""
    logging.info("Fetching cherry-pick command for %s", change_id)
    encoded_params = urllib.parse.urlencode({"o": "DOWNLOAD_COMMANDS"})
    url = (
        f"{gerrit_host}/a/changes/{change_id}/revisions/current"
        f"?{encoded_params}"
    )
    response_body = fetch_gob_curl_body_with_retries(url)

    revision_details = parse_gerrit_response(response_body)

    fetch_info = revision_details.get("fetch")
    if not fetch_info:
        logging.warning("No fetch_info for %s", change_id)
        return None

    command = _get_cherry_pick_command(fetch_info)
    if command:
        logging.info(
            "Successfully fetched cherry-pick command for %s", change_id
        )
        return command

    logging.warning("No Cherry-Pick command found for %s", change_id)
    return None
