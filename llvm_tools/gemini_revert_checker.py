# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Utilities for using Gemini with our nightly revert checker."""

import dataclasses
import datetime
import json
import logging
from pathlib import Path
import shlex
import subprocess
import tempfile
from typing import Any, Sequence

from cros_utils import cros_paths
from cros_utils import git_utils


# How long to wait before discarding inference results for SHAs that are no
# longer referenced.
#
# 30 days was arbitrarily selected, but should be plenty.
INFERENCE_COLLECTION_THRESHOLD = datetime.timedelta(days=30)


@dataclasses.dataclass(frozen=True, eq=True)
class GeminiRevertInference:
    """Stores the results of Gemini inference on one upstream commit.

    These can easily be converted to/from JSON. Since the common case is that
    Gemini outputs nothing for a commit (as most commits are neither reverts nor
    relands), `null`/`None` is a valid JSON encoding for an empty
    object.
    """

    reverted_shas: tuple[str, ...] = dataclasses.field(
        default_factory=lambda: ()
    )
    reverted_prs: tuple[int, ...] = dataclasses.field(
        default_factory=lambda: ()
    )
    is_revert: bool = False
    is_reland: bool = False

    def to_json(self) -> dict[str, Any] | None:
        # The vast majority of answers Gemini gives will be `empty`. To reduce
        # JSON clutter somewhat, `null` represents that.
        if self.is_empty():
            return None
        return dataclasses.asdict(self)

    @classmethod
    def from_json(
        cls, json_object: dict[str, Any] | None
    ) -> "GeminiRevertInference":
        if json_object is None:
            return _EMPTY_INFERENCE
        return cls(
            reverted_shas=tuple(json_object["reverted_shas"]),
            reverted_prs=tuple(json_object["reverted_prs"]),
            is_revert=json_object["is_revert"],
            is_reland=json_object["is_reland"],
        )

    def is_empty(self):
        return not (
            self.is_revert
            or self.is_reland
            or self.reverted_shas
            or self.reverted_prs
        )


# Most Gemini results are empty, and GeminiRevertInference is immutable.
# Keep a single 'empty' value around, instead of thousands of instances
# describing the same thing.
_EMPTY_INFERENCE = GeminiRevertInference()


# NOTE: This should ultimately call the Gemini Python API directly, but that is
# going to take some venv effort. Until that's done, this invokes a python
# script.
class GeminiEndpoint:
    """Used to interact with Gemini."""

    def __init__(self, gemini_api_key: str):
        """Creates a new GeminiEndpoint

        Args:
            gemini_api_key: The API key to pass to the endpoint.
        """
        self._gemini_api_key = gemini_api_key

    def get_api_key(self) -> str:
        return self._gemini_api_key


@dataclasses.dataclass(eq=True)
class GeminiState:
    """Caches all of Gemini's inference results."""

    # Keep the 'entire' history of revert statuses, since new HEADs may be added
    # at any time. e.g., LLVM might be at r600000 at ToT, stable might be
    # at r590000, and we may add a testing at r595000. We'll want to
    # surface the exact same commits for r595000 to r600000 on the new
    # testing branch as we did on stable.
    revert_status: dict[str, GeminiRevertInference] = dataclasses.field(
        default_factory=dict
    )

    # Mapping of SHA to the last UNIX timestamp they were considered 'needed'.
    # Used for collecting old inference results.
    #
    # Discarding old SHAs uses timeouts for two reasons:
    # 1. Multiple separate revert checkers may use this single state file (at
    #    different times); we need to take the SHA needs of both into account.
    # 2. Users may stop caring about a SHA, then start caring about it again
    #    shortly afterward (e.g., landing llvm-next, then reverting it).
    #    Immediately dropping everything related to an old SHA causes churn in
    #    these cases.
    important_shas: dict[str, int] = dataclasses.field(default_factory=dict)

    @classmethod
    def from_json(cls, json_object: Any) -> "GeminiState":
        return cls(
            revert_status={
                k: GeminiRevertInference.from_json(v)
                for k, v in json_object.get("revert_status", {}).items()
            },
            important_shas=json_object.get("important_shas", {}),
        )

    def to_json(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    def cached_inference_result_for(
        self, sha: str
    ) -> GeminiRevertInference | None:
        return self.revert_status.get(sha)


def read_gemini_state_or_default(state_file: Path) -> GeminiState:
    try:
        with state_file.open(encoding="utf-8") as f:
            return GeminiState.from_json(json.load(f))
    except FileNotFoundError:
        return GeminiState()


def write_gemini_state(state_file: Path, state: GeminiState):
    tmp_file = state_file.with_suffix(".new")
    with tmp_file.open("w", encoding="utf-8") as f:
        json.dump(
            state.to_json(), f, sort_keys=True, indent=2, separators=(",", ": ")
        )
    tmp_file.rename(state_file)


def _list_shas_between_all_of(
    llvm_dir: Path, main_ref: str, shas: Sequence[str]
) -> list[str]:
    """Finds all SHAs between the merge-base of `shas` and `main_ref`.

    SHAs are sorted oldest first.

    Raises:
        ValueError if the SHAs don't share a common history.
    """
    merge_base = git_utils.merge_base(llvm_dir, shas)
    if not merge_base:
        # It's an error if the parents don't share a common history.
        raise ValueError(f"SHAs share no merge-base: {shas}")

    return git_utils.list_shas_between(llvm_dir, merge_base, main_ref)


def discard_old_shas(
    state: GeminiState,
    currently_important_shas: Sequence[str],
    now: datetime.datetime,
    llvm_dir: Path,
    main_ref: str,
):
    """Discards cache entries associated with SHAs deemed 'old' in `state`.

    Args:
        state: GeminiState to modify
        currently_important_shas: SHAs to treat as 'roots'. Note that these are
          not necessarily the _only_ roots used.
        now: Current time
        llvm_dir: LLVM directory containing a .git director
        main_ref: The main ref that all `currently_important_shas` track
    """
    now_timestamp = int(now.timestamp())
    for sha in currently_important_shas:
        state.important_shas[sha] = now_timestamp

    collect_threshold = (now - INFERENCE_COLLECTION_THRESHOLD).timestamp()
    any_sha_expired = False
    for sha, last_seen in list(state.important_shas.items()):
        if last_seen < collect_threshold:
            any_sha_expired = True
            del state.important_shas[sha]
            logging.info(
                "Gemini SHA %s is no longer important; will collect related "
                "inference results",
                sha,
            )

    # If no SHAs expired, it's exceedingly unlikely that we'll GC anything,
    # and anything we _do_ GC would've just gotten GC'ed when a SHA expired
    # anyway.
    if not any_sha_expired:
        logging.info("Skipping SHA collection; no important SHAs were dropped")
        return

    gc_roots = list(state.important_shas)
    pre_drop_len = len(state.revert_status)
    shas_to_keep = set(
        _list_shas_between_all_of(llvm_dir, main_ref, shas=gc_roots)
    )
    state.revert_status = {
        k: v for k, v in state.revert_status.items() if k in shas_to_keep
    }
    logging.info(
        "Dropped %d Gemini cache entries",
        pre_drop_len - len(state.revert_status),
    )


def _normalize_gemini_result(
    inference: GeminiRevertInference, sha: str, llvm_dir: Path
) -> GeminiRevertInference:
    """Applies normalization to the given Gemini result.

    Gemini may hand back results that can be simplified. For example:
    - SHA shorthands (e.g., 'abc1234' instead of a 40-char SHA)
    - SHA/PR lists with multiple of the same entry

    And others that are difficult to reason about, like "this commit isn't a
    revert, but here are the PRs that were reverted."

    This normalizes these results from Gemini, so code later doesn't have to be
    defensive about them.

    Args:
        inference: The result of gemini inference.
        sha: The SHA that inference was run on.
        llvm_dir: The LLVM directory that inference was run with.
    """
    # If this isn't a revert, a nonempty value doesn't really make sense; ensure
    # to always return a fully empty result.
    if not inference.is_revert:
        if not inference.is_empty():
            logging.warning(
                "Normalizing Gemini results on SHA %s to 'not a revert'; "
                "got %s",
                sha,
                inference,
            )
        return _EMPTY_INFERENCE

    # is_reland, at this point, can be either True or False without issue.
    # The only remaining bit is to dedupe PRs and SHAs, as well as validating
    # SHAs.
    dedup_prs = sorted(set(inference.reverted_prs))

    expanded_shas = []
    for reverted_sha in inference.reverted_shas:
        try:
            # Do this even for full SHAs. If a SHA is invalid in upstream LLVM,
            # we don't want to preserve it.
            expanded_shas.append(git_utils.resolve_ref(llvm_dir, reverted_sha))
        except subprocess.CalledProcessError:
            logging.warning(
                "Failed to resolve LLVM SHA %s in inference result for %s; "
                "skipping",
                reverted_sha,
                sha,
            )

    return GeminiRevertInference(
        reverted_shas=tuple(sorted(set(expanded_shas))),
        reverted_prs=tuple(dedup_prs),
        is_revert=True,
        is_reland=inference.is_reland,
    )


def _find_commits_reverted_by(
    gemini_endpoint: GeminiEndpoint,
    llvm_dir: Path,
    commit_shas: list[str],
) -> dict[str, GeminiRevertInference]:
    if not commit_shas:
        return {}

    # As mentioned in the gemini_api/README, this implementation is sketchy for
    # the moment due to `pip` dependencies. In short, that has a
    # `check_reverts.py` script that has to be invoked as a subprocess, since it
    # sits in a specialized venv.
    #
    # The usage of it is, essentially:
    # - Write SHAs, one per line, on stdin.
    # - Get **unordered** `GeminiRevertInference` results as JSON. Each result
    #   has the format {"sha": "${SHA}", "result": GeminiInferenceResult}.
    #   There is one JSON object output per line. Lines are output to a temp
    #   file.
    gemini_api_dir = (
        cros_paths.script_toolchain_utils_root() / "llvm_tools" / "gemini_api"
    )

    ensure_venv_script = gemini_api_dir / "establish_venv.sh"
    venv_location = subprocess.run(
        (ensure_venv_script,),
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        encoding="utf-8",
    ).stdout.strip()

    check_reverts_command: list[Path | str] = [
        Path(venv_location) / "bin" / "python3",
        gemini_api_dir / "check_reverts.py",
        f"--gemini-api-key={gemini_endpoint.get_api_key()}",
        f"--llvm-dir={llvm_dir}",
    ]

    if logging.getLogger().getEffectiveLevel() <= logging.DEBUG:
        check_reverts_command.append("--debug")

    # N.B., `raw_tempfile` is deleted on `close`; our Python isn't new enough to
    # toggle this behavior. Just treat it as though it only provides a file
    # name.
    with tempfile.NamedTemporaryFile(
        prefix="gemini_revert_checker_"
    ) as raw_tempfile:
        gemini_revert_output = Path(raw_tempfile.name)
        check_reverts_command += (
            "-o",
            gemini_revert_output,
        )
        logging.debug(
            "Running gemini command: %s",
            shlex.join(str(x) for x in check_reverts_command),
        )
        subprocess.run(
            check_reverts_command,
            check=True,
            input="\n".join(commit_shas),
            encoding="utf-8",
        )

        results = {}
        with gemini_revert_output.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                single_result = json.loads(line)
                sha = single_result["sha"]
                gemini_result = GeminiRevertInference.from_json(
                    single_result["result"]
                )
                results[sha] = _normalize_gemini_result(
                    gemini_result, sha, llvm_dir
                )

    return results


def ensure_state_populated_for(
    gemini_endpoint: GeminiEndpoint,
    gemini_state: GeminiState,
    llvm_dir: Path,
    main_ref: str,
    prepopulate_parent_shas: Sequence[str],
):
    """Ensures `gemini_state` has entries for the given SHAs.

    Note that the SHAs are intended to be _parents_, so they all must be
    ancestors of `main_sha`. All SHAs between `prepopulate_parent_shas` and
    `main` are populated.

    Args:
        gemini_endpoint: The GeminiEndpoint to use.
        gemini_state: State to populate.
        llvm_dir: Path to an LLVM dir containing .git/.
        main_ref: SHA or ref describing LLVM's main branch.
        prepopulate_parent_shas: A series of parents of `main_sha` for which all
          results between the parent and `main_sha` should exist in the Gemini
          state.

    Returns:
        Nothing; mutates the GeminiState in place.
    """
    need_entries_for_shas = [
        x
        for x in _list_shas_between_all_of(
            llvm_dir, main_ref, shas=prepopulate_parent_shas
        )
        if x not in gemini_state.revert_status
    ]

    if not need_entries_for_shas:
        logging.info("All Gemini SHA information already prepopulated")
        return

    logging.info(
        "Prepopulating Gemini entries for %d SHAs",
        len(need_entries_for_shas),
    )
    gemini_state.revert_status.update(
        _find_commits_reverted_by(
            gemini_endpoint, llvm_dir, commit_shas=need_entries_for_shas
        )
    )
