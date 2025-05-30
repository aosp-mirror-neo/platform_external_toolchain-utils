# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tools for interacting with CrOS CLs, and the CQ in particular."""

import dataclasses
import enum
import json
import logging
import re
import shlex
import subprocess
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union


BuildID = int


def _run_bb_decoding_output(command: List[str], multiline: bool = False) -> Any:
    """Runs `bb` with the `json` flag, and decodes the command's output.

    Args:
        command: Command to run
        multiline: If True, this function will parse each line of bb's output
            as a separate JSON object, and a return a list of all parsed
            objects.
    """
    # `bb` always parses argv[1] as a command, so put `-json` after the first
    # arg to `bb`.
    run_command = ["bb", command[0], "-json"] + command[1:]
    stdout = subprocess.run(
        run_command,
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        encoding="utf-8",
    ).stdout

    def parse_or_log(text: str) -> Any:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logging.error(
                "Error parsing JSON from command %r; bubbling up. Tried to "
                "parse: %r",
                run_command,
                text,
            )
            raise

    if multiline:
        return [
            parse_or_log(line)
            for line in stdout.splitlines()
            if line and not line.isspace()
        ]
    return parse_or_log(stdout)


@dataclasses.dataclass(frozen=True, eq=True)
class ChangeListURL:
    """A consistent representation of a CL URL.

    The __str__s always converts to a crrev.com URL.
    """

    cl_id: int
    patch_set: Optional[int] = None
    internal: bool = False

    _URL_PARSE_RE = re.compile(
        # Match an optional https:// header.
        r"(?:https?://)?"
        # Leaving the CL number and patch set as the next parts, match either
        # crrev...
        r"(crrev\.com/[ci]/"
        # ...or chromium-review URLs. Note that chromium-review can either be
        # served by googlesource or git.corp.google hosts.
        r"|(?:chromium|chrome-internal)-review\."
        r"(?:git\.corp\.google|googlesource)\.com/.*/\+/)"
        # Match the CL number...
        r"(\d+)"
        # and (optionally) the patch-set, as well as consuming any of the
        # path after the patch-set.
        r"(?:/(\d+)?(?:/.*)?)?"
        # Validate any sort of GET params for completeness.
        r"(?:$|[?&].*)"
    )

    @classmethod
    def parse(cls, url: str) -> "ChangeListURL":
        m = cls._URL_PARSE_RE.fullmatch(url)
        if not m:
            raise ValueError(
                f"URL {url!r} was not recognized. Supported URL formats are "
                "crrev.com/c/${cl_number}/${patch_set_number}, and "
                "chromium-review.googlesource.com/c/project/path/+/"
                "${cl_number}/${patch_set_number}. The patch-set number is "
                "optional, and there may be a preceding http:// or https://. "
                "Internal CL links are also supported."
            )
        host, cl_id, maybe_patch_set = m.groups()
        internal = host.startswith("chrome-internal-review") or host.startswith(
            "crrev.com/i/"
        )
        if maybe_patch_set is not None:
            maybe_patch_set = int(maybe_patch_set)
        return cls(int(cl_id), maybe_patch_set, internal)

    @classmethod
    def parse_with_patch_set(cls, url: str) -> "ChangeListURL":
        """parse(), but raises a ValueError if no patchset is specified."""
        result = cls.parse(url)
        if result.patch_set is None:
            raise ValueError("A patchset number must be specified.")
        return result

    def crrev_url_without_http(self) -> str:
        namespace = "i" if self.internal else "c"
        result = f"crrev.com/{namespace}/{self.cl_id}"
        if self.patch_set is not None:
            result += f"/{self.patch_set}"
        return result

    @property
    def gerrit_tool_id(self) -> str:
        """Returns an identifier for this CL for use with the 'gerrit' tool."""
        return f"*{self.cl_id}" if self.internal else f"{self.cl_id}"

    def __str__(self):
        return f"https://{self.crrev_url_without_http()}"


class BuilderStatus(enum.StrEnum):
    """Statuses from builders."""

    SCHEDULED = "SCHEDULED"
    STARTED = "STARTED"
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    INFRA_FAILURE = "INFRA_FAILURE"
    CANCELED = "CANCELED"

    @classmethod
    def parse(cls, s: str) -> "BuilderStatus":
        try:
            # Some statuses come in lower-case, others come in upper-case. The
            # latter is by far the dominant style, so normalize to that.
            return cls[s.upper()]
        except KeyError:
            raise ValueError(f"Unknown builder status: {s}") from None

    @property
    def is_running(self) -> bool:
        return self in (self.SCHEDULED, self.STARTED)

    @property
    def is_success(self):
        return self == self.SUCCESS

    @property
    def is_failure(self):
        return not (self.is_running or self.is_success)


def builder_url(build_id: BuildID) -> str:
    """Returns a builder URL given a build ID."""
    return f"https://ci.chromium.org/b/{build_id}"


# Used to parse the build ID from a `bb add` invocation.
_BOT_SPAWN_BUILD_ID_RE = re.compile(r"http://ci\.chromium\.org/b/(\d+)\b")


def spawn_bot(
    bot_name: str,
    cls: Iterable[ChangeListURL] = (),
) -> BuildID:
    """Uses `bb add` to spawn a builder with the given params."""
    cmd = ["bb", "add"]
    for cl in cls:
        cmd += ("--cl", str(cl))
    cmd.append(bot_name)

    logging.debug("Running builder with %s", shlex.join(cmd))
    run_stdout = subprocess.run(
        cmd,
        check=True,
        encoding="utf-8",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
    ).stdout

    build_ids = _BOT_SPAWN_BUILD_ID_RE.findall(run_stdout)
    if len(build_ids) != 1:
        logging.error("Unexpected stdout from `bb add`; got %r", run_stdout)
        raise ValueError("Expected one build-id from stdout; got {build_ids}")

    build_id = BuildID(build_ids[0])
    logging.info("Spawned bot: %s", builder_url(build_id))
    return build_id


def wait_for_bot_to_finish(
    build_id: BuildID, timeout_hours: int
) -> BuilderStatus:
    """Waits for the given build to finish, returning its final status.

    Args:
        build_id: Builder ID
        timeout_hours: Hours before giving up

    Raises:
        ValueError if the timeout expires
    """
    timeout_at_secs = time.time() + timeout_hours * 60 * 60
    check_frequency_secs = 10 * 60
    while True:
        out = _run_bb_decoding_output(
            ["get", "-json", "-fields=status", str(build_id)],
        )
        assert len(out) == 1, out
        status = BuilderStatus(out[0])
        if not status.is_running:
            return status

        if time.time() > timeout_at_secs:
            raise ValueError(
                f"Bot hit timeout after {timeout_hours} hours; "
                f"last status was {status!r}"
            )

        logging.info("Bot is still running; sleeping for a bit...")
        time.sleep(check_frequency_secs)


def fetch_builder_steps(build_id: BuildID) -> List[Any]:
    """Returns the JSON dict of the given builder's steps."""
    result = _run_bb_decoding_output(["get", "-steps", str(build_id)])
    # A build with no steps is functionally equivalent to a build with an empty
    # steps list.
    return result.get("steps", [])


def fetch_cq_orchestrator_ids(
    cl: ChangeListURL,
) -> List[BuildID]:
    """Returns the BuildID of completed cq-orchestrator runs on a CL.

    Newer runs are sorted later in the list.
    """
    results: List[Dict[str, Any]] = _run_bb_decoding_output(
        [
            "ls",
            "-cl",
            str(cl),
            "chromeos/cq/cq-orchestrator",
        ],
        multiline=True,
    )

    # We can theoretically filter on a status flag, but it seems to only accept
    # at most one value. Filter here instead; parsing one or two extra JSON
    # objects is cheap.
    finished_results = [
        x for x in results if not BuilderStatus(x["status"]).is_running
    ]

    # Sort by createTime. Fall back to build ID if a tie needs to be broken.
    # While `createTime` is a string, it's formatted so it can be sorted
    # correctly without parsing.
    finished_results.sort(key=lambda x: (x["createTime"], x["id"]))
    return [int(x["id"]) for x in finished_results]


@dataclasses.dataclass(frozen=True)
class CQOrchestratorOutput:
    """A class representing the output of a cq-orchestrator builder."""

    # The status of the CQ builder.
    status: BuilderStatus
    # A dict of builders that this CQ builder spawned.
    child_builders: Dict[str, BuildID]

    @classmethod
    def fetch(cls, bot_id: BuildID) -> "CQOrchestratorOutput":
        decoded: Dict[str, Any] = _run_bb_decoding_output(
            ["get", "-steps", str(bot_id)]
        )
        results = {}

        # cq-orchestrator spawns builders in a series of steps. Each step has a
        # markdownified link to the builder in the summaryMarkdown for each
        # step. This loop parses those out.
        build_url_re = re.compile(
            re.escape("https://cr-buildbucket.appspot.com/build/") + r"(\d+)"
        )
        # Example step name containing a build URL:
        # "run builds|schedule new builds|${builder_name}". `builder_name`
        # contains no spaces, though follow-up steps with the same prefix might
        # include spaces.
        step_name_re = re.compile(
            re.escape("run builds|schedule new builds|") + "([^ ]+)"
        )
        for step in decoded["steps"]:
            step_name = step["name"]
            m = step_name_re.fullmatch(step_name)
            if not m:
                continue

            builder = m.group(1)
            summary = step["summaryMarkdown"]
            ids = build_url_re.findall(summary)
            if len(ids) != 1:
                raise ValueError(
                    f"Parsing summary of builder {builder} failed: wanted one "
                    f"match for {build_url_re}; got {ids}. Full summary: "
                    f"{summary!r}"
                )
            if builder in results:
                raise ValueError(f"Builder {builder} spawned multiple times?")
            results[builder] = int(ids[0])
        status = BuilderStatus.parse(decoded["status"])
        return cls(child_builders=results, status=status)


@dataclasses.dataclass(frozen=True)
class CQBoardBuilderOutput:
    """A class representing the output of a *-cq builder (e.g., brya-cq)."""

    # The status of the CQ builder.
    status: BuilderStatus
    # Link to artifacts produced by this builder. Not available if the builder
    # isn't yet finished, and not available if the builder failed in a weird
    # way (e.g., INFRA_ERROR)
    artifacts_link: Optional[str]

    @classmethod
    def fetch_many(
        cls, bot_ids: Iterable[BuildID]
    ) -> List["CQBoardBuilderOutput"]:
        """Fetches CQBoardBuilderOutput for the given bots."""
        bb_output = _run_bb_decoding_output(
            ["get", "-p"] + [str(x) for x in bot_ids], multiline=True
        )
        results = []
        for result in bb_output:
            status = BuilderStatus.parse(result["status"])
            output = result.get("output")
            if output is None:
                artifacts_link = None
            else:
                artifacts_link = output["properties"].get("artifact_link")
            results.append(cls(status=status, artifacts_link=artifacts_link))
        return results


def fetch_cq_orchestrator_or_board_builder(
    bot_id: BuildID,
) -> Tuple[str, Union[CQOrchestratorOutput, CQBoardBuilderOutput]]:
    """Figures out the builder type of bot_id, then fetches it."""
    result = _run_bb_decoding_output(["get", str(bot_id)])
    builder_name = result["builder"]["builder"]
    if builder_name == "cq-orchestrator":
        return builder_name, CQOrchestratorOutput.fetch(bot_id)
    return builder_name, CQBoardBuilderOutput.fetch_many((bot_id,))[0]


def parse_release_from_builder_artifacts_link(artifacts_link: str) -> str:
    """Parses the release version from a builder artifacts link.

    >>> parse_release_from_builder_artifacts_link(
        "gs://chromeos-image-archive/amd64-generic-asan-cq/"
        "R122-15711.0.0-59730-8761718482083052481")
    "R122-15711.0.0"
    """
    results = re.findall(r"/(R\d+-\d+\.\d+\.\d+)-", artifacts_link)
    if len(results) != 1:
        raise ValueError(
            f"Expected one release version in {artifacts_link}; got: {results}"
        )
    return results[0]
