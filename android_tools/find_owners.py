# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Implements logic to find OWNERS using Gerrit's OWNERS plugin."""

# Gerrit API docs:
# https://gerrit.googlesource.com/plugins/code-owners/+/refs/heads \
# /master/resources/Documentation/rest-api.md#code-owner-info # nocheck
# (^ Note this has to be split on two lines to placate both `cros lint` and the
#    discouraged-words checker)

import argparse
import collections
import dataclasses
import json
import logging
import multiprocessing.pool
from pathlib import Path
import random
import subprocess
import urllib.parse

from android_tools import android_paths
from llvm_tools import manifest_utils


ANDROID_MANIFEST_XML_FROM_ROOT = Path(".repo") / "manifests" / "default.xml"
INTERNAL_GERRIT_HOST = "https://googleplex-android-review.git.corp.google.com"


@dataclasses.dataclass(frozen=True)
class RepoCache:
    """Cache of repo metadata."""

    # Mapping from relative path in the Android tree to repo project name.
    repos_to_names: dict[str, str]

    @classmethod
    def create_from_manifest(cls, manifest_file: Path) -> "RepoCache":
        repos_to_names = {
            project.project_path: project.project_name
            for project in manifest_utils.read_manifest_project_mappings(
                manifest_file
            )
        }
        return cls(repos_to_names=repos_to_names)


def _fetch_gob_curl_body_with_retries(url: str) -> str:
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


@dataclasses.dataclass(frozen=True)
class OwnersSuggestion:
    """A suggestion for an OWNER for a file."""

    username: str
    distance: int | None
    is_explicitly_mentioned: bool
    is_last_resort: bool


def _parse_suggestions_for_googlers(
    url: str, response_body: str
) -> list[OwnersSuggestion]:
    # When responding with JSON, Gerrit always responds starting with this.
    json_response_pre = ")]}'"
    if not response_body.startswith(json_response_pre):
        raise ValueError(
            f"Unexpected non-JSON Gerrit response: {response_body!r}"
        )

    response_body = response_body[len(json_response_pre) :]

    # We should get back a JSON object that looks like:
    # {
    #   "code_owners": [
    #     {
    #       "account": {
    #         "_account_id": 1234,
    #         "name": "Alex Doe",
    #         "email": "a@google.com"
    #       },
    #       "scorings": {
    #         "DISTANCE": 1,
    #         "IS_EXPLICITLY_MENTIONED": 1,
    #         "LAST_RESORT_SUGGESTION": 1,
    #       }
    #     }
    #   ],
    # }
    owners_response = json.loads(response_body)
    results = []
    at_google = "@google.com"
    for owner_info in owners_response.get("code_owners", ()):
        email = owner_info.get("account", {}).get("email")
        if not email:
            logging.warning(
                "OWNER info for %s missing email account; skipping", url
            )
            continue

        if not email.endswith(at_google):
            logging.debug(
                "Skipping OWNER suggestion for %s; they aren't a Googler", email
            )
            continue

        username = email[: -len(at_google)]
        scorings = owner_info.get("scorings")
        if scorings is None:
            logging.warning(
                "OWNER info for %s missing scoring for account %s; skipping",
                url,
                email,
            )
            continue

        results.append(
            OwnersSuggestion(
                username=username,
                distance=scorings.get("DISTANCE"),
                is_explicitly_mentioned=scorings.get(
                    "IS_EXPLICITLY_MENTIONED", False
                ),
                is_last_resort=scorings.get("LAST_RESORT_SUGGESTION", False),
            )
        )
    return results


def _fetch_suggested_googler_owners_for_file(
    *,
    gerrit_host: str,
    project_name: str,
    file_in_repo: str,
) -> list[OwnersSuggestion]:
    """Fetches suggested OWNERS for reviewing a file.

    Returns:
        A series of `OwnersSuggestion`s describing potential OWNERS for the
        given file, in no particular order.
    """
    # Gerrit wants a URL with a path like
    # /projects/platform%2Fbionic/branches/main/code_owners/libc%2FAndroid.bp
    #
    # Where %2F is a url-encoded '/'
    encoded_project_name = urllib.parse.quote(project_name, safe="")
    encoded_file_in_repo = urllib.parse.quote(file_in_repo, safe="")
    encoded_params = urllib.parse.urlencode(
        {
            # `DETAILS` requests email information, which isn't returned by
            # default.
            "o": "DETAILS",
            # The default limit of owners is 10, 25 seems more likely to return
            # fuller lists. Determined by [human] vibes rather than
            # experimentation though.
            "limit": 25,
            # Gerrit's find_owners plugin randomizes results internally; a
            # consistent seed should lead to mostly consistent
            # orderings/results.
            "seed": 0,
        }
    )
    url = (
        f"{gerrit_host}/projects/{encoded_project_name}/branches/main"
        f"/code_owners/{encoded_file_in_repo}?{encoded_params}"
    )
    response_body = _fetch_gob_curl_body_with_retries(url)
    return _parse_suggestions_for_googlers(url, response_body.lstrip())


@dataclasses.dataclass(eq=True)
class OwnershipScore:
    """Represents an OWNERS score for `_find_best_owners_from`."""

    num_owned_files: int = 0
    total_owned_distance: int = 0
    num_last_resort: int = 0
    num_explicit_mentions: int = 0


def _find_best_owners_from(
    per_file_candidates: list[list[OwnersSuggestion]],
) -> list[str]:
    """Finds the best OWNERS candidates, given `candidates`.

    Args:
        per_file_candidates: a list of lists returned by
            _fetch_suggested_googler_owners_for_file.

    Returns:
        A list of candidates, all of whom are equally good to select from, in
        `sorted()` order. May be empty.
    """
    # Implement a simple scoring system, and try to pick the maximum scores.
    #
    # The idea here is, in order of "strongest preference" -> "weakest
    # preference":
    # 1. It's best to avoid last resort owners if possible (these people are
    #    sometimes global OWNERS).
    # 2. It's always better to select someone with OWNERS over as many relevant
    #    files as possible.
    # 4. It's good to prefer people who are directly named in OWNERS files,
    #    instead of people who get included through groups.
    # 3. It's good to differentiate between people with different levels of
    #    last-resort-ness by their distance from the files.
    owner_scores: dict[str, OwnershipScore] = collections.defaultdict(
        OwnershipScore
    )
    for file_candidates in per_file_candidates:
        candidate_distances = (x.distance for x in file_candidates)
        # In case people have unknown distances, pick one that's one greater
        # than all known distances for this file.
        default_distance = 1 + max(
            (x for x in candidate_distances if x is not None), default=0
        )

        for candidate in file_candidates:
            scores = owner_scores[candidate.username]
            scores.num_owned_files += 1
            if candidate.is_last_resort:
                scores.num_last_resort += 1
            if candidate.is_explicitly_mentioned:
                scores.num_explicit_mentions += 1
            scores.total_owned_distance += (
                default_distance
                if candidate.distance is None
                else candidate.distance
            )

    # If everything had no eligible OWNERS, we simply have to give up.
    if not owner_scores:
        return []

    best_owner_score = max(
        owner_scores.values(),
        key=lambda s: (
            -s.num_last_resort,
            s.num_owned_files,
            s.num_explicit_mentions,
            -s.total_owned_distance,
        ),
    )

    return sorted(
        owner
        for owner, score in owner_scores.items()
        if score == best_owner_score
    )


def fetch_likely_relevant_code_owner(
    repo_cache: RepoCache,
    gerrit_host: str,
    git_repo: str,
    files_to_check: list[str],
) -> str | None:
    """Fetches OWNERS for the given files.

    Args:
        repo_cache: Cache of repo metadata.
        gerrit_host: Gerrit host to perform lookups against.
        git_repo: The git repo to query, relative to the root of the Android
            repo.
        files_to_check: A list of files to check OWNERShip of, relative to
            `git_repo`.

    Returns:
        Ideally, a username of a specific Googler who has OWNERS over the given
        files. Note that this is not always possible, so this function falls
        back to OWNERShip-based heuristics to determine a Googler who is likely
        appropriate to handle a review of the given files (even if they need to
        loop someone in).

        If it returns None, either an appropriate OWNER couldn't be determined,
        or the files are all globally owned.
    """
    project_name = repo_cache.repos_to_names.get(git_repo)
    if not project_name:
        logging.error(
            "Could not find project mapping in manifest.xml, can't look up "
            "OWNERs for files in %s",
            git_repo,
        )
        return None

    suggested_owners = [
        _fetch_suggested_googler_owners_for_file(
            gerrit_host=gerrit_host,
            project_name=project_name,
            file_in_repo=x,
        )
        for x in files_to_check
    ]

    best_owners = _find_best_owners_from(suggested_owners)
    if not best_owners:
        logging.warning(
            "Could not determine a good OWNER for all of %s in %s",
            files_to_check,
            git_repo,
        )
        return None

    logging.debug(
        "Found %d OWNERS options for all of %s in %s: %s",
        len(best_owners),
        files_to_check,
        git_repo,
        best_owners,
    )
    # Since all of `best_owners` are equally good, pick one at random.
    return random.choice(best_owners)


def fetch_all_likely_relevant_code_owners(
    repo_cache: RepoCache,
    gerrit_host: str,
    per_repo_files_to_check: dict[str, list[str]],
) -> dict[str, str | None]:
    """A batch version of `fetch_likely_relevant_code_owner`.

    Functionally similar to:
    ```
    {
        fetch_likely_relevant_code_owner(repo_cache, gerrit_host,
                                         git_repo, files)
        for git_repo, files in per_repo_files_to_check.items()
    }
    ```
    """

    def fetch_one(
        git_repo: str, files_to_check: list[str]
    ) -> tuple[str, str | None]:
        return git_repo, fetch_likely_relevant_code_owner(
            repo_cache, gerrit_host, git_repo, files_to_check
        )

    if len(per_repo_files_to_check) <= 1:
        return dict(fetch_one(*x) for x in per_repo_files_to_check.items())

    # The Gerrit docs recommend querying with up to 10 parallel workers.
    workers = min(10, len(per_repo_files_to_check))
    with multiprocessing.pool.ThreadPool(workers) as pool:
        return dict(pool.starmap(fetch_one, per_repo_files_to_check.items()))


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--debug", action="store_true", help="Enable debug logging"
    )
    parser.add_argument(
        "--android-tree",
        type=Path,
        required=True,
        help="Path to the Android source tree",
    )
    parser.add_argument(
        "--gerrit-host",
        default=INTERNAL_GERRIT_HOST,
        help="Gerrit host to query",
    )
    parser.add_argument(
        "--git-repo",
        required=True,
        help="Git repository path relative to the Android source tree",
    )
    parser.add_argument(
        "files",
        nargs="+",
        help="Files to check ownership for, relative to the git repository",
    )
    opts = parser.parse_args(argv)

    android_paths.assert_is_valid_android_tree_root(parser, opts.android_tree)

    return opts


def main(argv: list[str]) -> None:
    opts = parse_args(argv)

    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.DEBUG if opts.debug else logging.INFO,
    )

    repo_cache = RepoCache.create_from_manifest(
        opts.android_tree / ANDROID_MANIFEST_XML_FROM_ROOT
    )

    owner = fetch_likely_relevant_code_owner(
        repo_cache=repo_cache,
        gerrit_host=opts.gerrit_host,
        git_repo=opts.git_repo,
        files_to_check=opts.files,
    )
    if not owner:
        logging.info("Couldn't find a likely owner")
    else:
        logging.info("Likely owner: %s", owner)
