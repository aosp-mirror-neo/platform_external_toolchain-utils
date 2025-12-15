# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Queries Gemini for information on upstream LLVM commits.

As input, this script takes a series of upstream LLVM SHAs, one per line. As
output, this provides a series of JSON objects, one per line.

As an example (ignoring flags like --gemini-api-key):

```
$ echo -e 'abc123\ndef456' | check_reverts.py
{"sha": "abc123", "result": {...}}
{"sha": "def456", "result": {...}}
```

Note that the results are _not_ guaranteed to be in the same order as the input
SHAs. There's also no guarantee of the coherency of results (e.g., Gemini may
hand back an object indicating that a SHA is not a revert, but list reverted
SHAs). There is, however, a guarantee that results will be typed properly - you
may assume that the "is_revert" field will always be present, and either `true`
or `false`.
"""

import argparse
import contextlib
import dataclasses
import json
import logging
import multiprocessing.pool
from pathlib import Path
import queue
import subprocess
import sys
import typing
from typing import Any, IO

# `pylint`, run by `cros lint`, is run in its own Python environment, which does
# not contain `google.genai`. `mypy` can _see_ the module, but complains that it
# doesn't have correct typing markers.
# pylint:disable=import-error
from google import genai
from google.genai import types


T = typing.TypeVar("T")


def get_dict_elem_with_type(
    obj: dict[str, Any], key: str, expect_type: type[T]
) -> T:
    """Extracts `key` from `obj`, verifying its type.

    At present, this only supports what types GeminiRevertInference needs to
    accept. That is, primitive types and lists thereof. Lists should always
    be parameterized properly.

    Raises:
        ValueError if `key` isn't present in `obj`, or if it's not of the given
        type.
    """
    if key not in obj:
        raise ValueError(f"No {key} key in {obj}")

    value = obj[key]
    origin = typing.get_origin(expect_type)
    if origin is None:
        # Don't use `isinstance` here, since `bool` is a subclass of int; we
        # don't want a `True` to pass when the user asks for `int`.
        # pylint:disable=unidiomatic-typecheck
        if type(value) is expect_type:
            return value
        raise ValueError(
            f"Key {key} is of type {type(value)}; wanted {expect_type} in {obj}"
        )

    # N.B., use `assert`s for errors with the form of `type[T]`, since a broken
    # input type is a programmer error.

    # Dict can be supported, but there're no users at present.
    assert origin is list, f"Only list validation is supported, not {origin}."

    args = typing.get_args(expect_type)
    assert len(args) == 1, "Lists must always be parameterized with one elem."

    arg_type = args[0]
    assert (
        typing.get_origin(arg_type) is None
    ), "Only primitive list elements are supported."

    if not isinstance(value, list):
        raise ValueError(
            f"Key {key} is of type {type(value)}, not {expect_type}"
        )

    for item in value:
        # Don't use `isinstance` here, since `bool` is a subclass of int; we
        # don't want a `True` to pass when the user asks for `int`.
        # pylint:disable=unidiomatic-typecheck
        if type(item) is not arg_type:
            raise ValueError(
                f"Element {item} of list is {type(item)}, not {arg_type}"
            )
    return typing.cast(T, value)


# NOTE: The class docstring and per-field docstrings are sent to Gemini, so
# they're very descriptive.
@dataclasses.dataclass(frozen=True, eq=True)
class GeminiRevertInference:
    """The results of inference on the given commit."""

    reverted_shas: list[str]
    """
    A list of, potentially partial, Git SHAs that are likely reverted by
    this specific commit.
    """

    reverted_prs: list[int]
    """
    A list of GitHub PR numbers that are likely reverted by this specific
    commit.
    """

    is_revert: bool
    """
    Indicates whether the commit is likely to be a revert, regardless of
    whether any SHAs or PRs can be identified for it.
    """

    is_reland: bool
    """
    Indicates whether the commit is likely to be a reland, regardless of
    whether any SHAs or PRs can be identified for it.
    """

    @classmethod
    def from_json_checked(
        cls, json_object: dict[str, Any]
    ) -> "GeminiRevertInference":
        """Parses 'untrusted' JSON into an instance of this class.

        Gemini can generally be trusted to produce JSON that matches this type's
        definition precisely, but this method double-checks that `json_object`
        has all of the correct fields, and each field has the correct type.

        Raises:
            ValueError if the JSON is poorly-formed.
        """
        reverted_shas = get_dict_elem_with_type(
            json_object, "reverted_shas", list[str]
        )
        reverted_prs = get_dict_elem_with_type(
            json_object, "reverted_prs", list[int]
        )

        # Sort these for consistency.
        reverted_shas.sort()
        reverted_prs.sort()
        return cls(
            reverted_shas=reverted_shas,
            reverted_prs=reverted_prs,
            is_revert=get_dict_elem_with_type(json_object, "is_revert", bool),
            is_reland=get_dict_elem_with_type(json_object, "is_reland", bool),
        )

    def to_json(self) -> Any:
        return dataclasses.asdict(self)


class GeminiResponseIsBrokenError(Exception):
    """Thrown when the Gemini response for a SHA is known to be broken."""


def parse_gemini_response(
    sha: str, response: types.GenerateContentResponse
) -> GeminiRevertInference:
    """Parses the given response from the Gemini API

    Raises:
        GeminiResponseIsBrokenError when the response's form doesn't match
        expectations.
    """
    candidates = response.candidates
    if not candidates:
        raise GeminiResponseIsBrokenError(
            f"Unexpected response with no candidates: {response}"
        )

    # The types say it's possible to have multiple candidates, but that should
    # be exceedingly rare.
    if len(candidates) > 1:
        logging.warning(
            "Unexpected: got %d candidates from Gemini: %s",
            len(candidates),
            candidates,
        )
    content = candidates[0].content
    if not content:
        raise GeminiResponseIsBrokenError(
            f"Unexpected response candidate with no content: {response}"
        )

    parts = content.parts
    if not parts:
        raise GeminiResponseIsBrokenError(
            f"Unexpected response candidate with empty parts: {response}"
        )

    thinking_part = next((x for x in parts if x.thought and x.text), None)
    if thinking_part:
        logging.debug("Thinking for SHA %s was %r", sha, thinking_part.text)
    else:
        logging.debug("No thinking emitted for SHA %s", sha)

    if usage := response.usage_metadata:
        # Note that the counts here may be None. If None, replace with the
        # default (0).
        logging.debug(
            "Commit %s tokens: prompt=%d, thinking=%d, tools=%d, total=%d",
            sha,
            usage.prompt_token_count or 0,
            usage.thoughts_token_count or 0,
            usage.tool_use_prompt_token_count or 0,
            usage.total_token_count or 0,
        )

    result_part = next((x for x in parts if not x.thought), None)
    if not result_part or not result_part.text:
        raise GeminiResponseIsBrokenError(
            f"Gemini returned no result for query on {sha}: {response}"
        )

    try:
        parsed_result = json.loads(result_part.text)
    except json.JSONDecodeError:
        raise GeminiResponseIsBrokenError(
            f"Gemini produced invalid JSON for query on {sha}: {response}"
        )

    try:
        return GeminiRevertInference.from_json_checked(parsed_result)
    except ValueError as e:
        raise GeminiResponseIsBrokenError(
            f"Gemini produced invalid JSON for query on {sha}: {e}"
        )


def process_one_sha(
    client: genai.Client, system_prompt: str, llvm_dir: Path, sha: str
) -> GeminiRevertInference:
    """Queries the given genai client for revert info"""
    commit_info = subprocess.run(
        ("git", "log", "-n1", sha),
        check=True,
        cwd=llvm_dir,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
    ).stdout

    logging.info("Processing commit %s...", sha)

    # It's rare for requests to use more than 2,000 tokens (thinking and output
    # combined). That said, setting _some kind of limit_, even if it's generous,
    # seems prudent.
    #
    # thinking_budget is set to `-1` to cause Gemini to determine how much
    # thinking is useful; there's an implicit limit of ~60K tokens, per
    # https://ai.google.dev/gemini-api/docs/thinking
    thinking_budget = -1

    # Output which is separate from thinking budgets, generally are just a few
    # hundred tokens.
    output_token_budget = 3_000

    # This is in a `range(_)` loop for an unfortunate reason: despite attempts
    # to bring randomness to zero, this API still seems to return slightly
    # random results. The docs also explicitly do _not_ promise determinism:
    # https://cloud.google.com/vertex-ai/generative-ai/docs/learn/prompts/adjust-parameter-values
    #
    # If you hit the API multiple times, you will **very often** get the same
    # result, but with low probability (seemingly <1/50), Gemini will return
    # an inconsistent response. Often, these responses are completely broken
    # (either no content, or invalid JSON), though sometimes they do parse.
    #
    # Just retry a few times to minimize the chance of brokenness bubbling up.
    retry_limit = 5
    i = 1

    # This loop is awkward to write with a `for range` due to how it exits, so
    # the increment is handled manually.
    while True:
        response = client.models.generate_content(
            # TODO(b/445908427): Maybe try gemini flash or flash-lite once the
            # revert checker is known to work well enough.
            model="gemini-2.5-pro",
            contents=commit_info,
            config=types.GenerateContentConfig(
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True,
                ),
                system_instruction=system_prompt,
                response_mime_type="application/json",
                tool_config=types.ToolConfig(),
                response_schema=GeminiRevertInference,
                thinking_config=types.ThinkingConfig(
                    include_thoughts=True,
                    thinking_budget=thinking_budget,
                ),
                max_output_tokens=output_token_budget,
                # Minimize randomness; just pick the best answer possible.
                # https://cloud.google.com/vertex-ai/generative-ai/docs/learn/prompts/adjust-parameter-values
                temperature=0,
                top_k=1,
                top_p=1,
                seed=0,
            ),
        )

        try:
            return parse_gemini_response(sha, response)
        except GeminiResponseIsBrokenError:
            if i >= retry_limit:
                raise
            logging.exception(
                "Failed attempt %d of running Gemini on SHA %s; retrying...",
                i,
                sha,
            )

        i += 1


def write_one_result(
    output: IO[str], sha: str, sha_result: GeminiRevertInference
) -> None:
    obj = {"sha": sha, "result": sha_result.to_json()}
    json.dump(obj, output)
    output.write("\n")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    auth_group = parser.add_mutually_exclusive_group()
    auth_group.add_argument(
        "--gemini-api-key",
        help="""
        Gemini API key to use. Either this must be specified, or both
        --gcp-project and --gcp-location must.
        """,
    )

    vertex_group = auth_group.add_argument_group()
    vertex_group.add_argument(
        "--gcp-project",
        help="""
        GCP project to use for Vertex AI. If specified, --gcp-location must also
        be specified.
        """,
    )
    vertex_group.add_argument(
        "--gcp-location",
        help="""
        GCP location to use for Vertex AI. If specified, --gcp-project must also
        be specified.
        """,
    )

    parser.add_argument(
        "-n",
        "--jobs",
        type=int,
        # Default was chosen arbitrarily. It gives a great speedup, hopefully
        # without running near ratelimits.
        default=16,
        help="Max number of concurrent Gemini queries to allow at a time.",
    )
    parser.add_argument(
        "-i",
        "--input",
        type=Path,
        help="Where to read input from, defaults to stdin.",
    )
    parser.add_argument(
        "-o",
        "--output",
        required=True,
        type=Path,
        help="A path to dump output to.",
    )
    parser.add_argument(
        "--llvm-dir",
        type=Path,
        required=True,
        help="Root of LLVM git directory to consult for git information.",
    )
    parser.add_argument(
        "--debug", action="store_true", help="Enable debug logging"
    )
    opts = parser.parse_args(argv)
    if bool(opts.gcp_project) != bool(opts.gcp_location):
        parser.error("--gcp-project requires --gcp-location")
    if not opts.gcp_location and not opts.gemini_api_key:
        parser.error(
            "You must specify either --gemini-api-key or --gcp-project and "
            "--gcp-location"
        )
    return opts


def main(argv: list[str]) -> None:
    my_dir = Path(__file__).parent.resolve()
    opts = parse_args(argv)
    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.DEBUG if opts.debug else logging.INFO,
    )

    jobs: int = opts.jobs
    llvm_dir: Path = opts.llvm_dir
    system_prompt = (my_dir / "check_reverts_system_prompt.md").read_text(
        encoding="utf-8"
    )

    if gemini_api_key := opts.gemini_api_key:
        genai_auth_args: dict[str, Any] = {
            "api_key": gemini_api_key,
        }
    else:
        genai_auth_args = {
            "vertexai": True,
            "project": opts.gcp_project,
            "location": opts.gcp_location,
        }

    # While the genai API _does_ seem to support `async` pretty well if that
    # mode is enabled, it does _not_ document thread safety guarantees on types.
    # Since having N clients is relatively cheap, give each thread its own
    # client.
    #
    # Ideally, this would be a set of threads pulling jobs from a `queue.Queue`
    # until the queue is empty, but there's no clean way to signal shutdown
    # in a queue until Python 3.13. Instead, use a queue that functions as
    # a connection cache, and use a `ThreadPool` to distribute the jobs.
    client_cache: queue.SimpleQueue[genai.Client] = queue.SimpleQueue()

    def run_one_sha(sha_to_process: str) -> tuple[str, GeminiRevertInference]:
        try:
            client = client_cache.get_nowait()
        except queue.Empty:
            client = genai.Client(
                http_options=types.HttpOptions(
                    retry_options=types.HttpRetryOptions(
                        attempts=5,
                        initial_delay=5,
                        max_delay=60,
                    )
                ),
                **genai_auth_args,
            )

        sha_result = process_one_sha(
            client, system_prompt, llvm_dir, sha_to_process
        )
        # N.B., not in a `finally`, since an exception may be raised as a result
        # of this client being in a weird state.
        #
        # At the time of writing, exceptions here kill the program anyway.
        client_cache.put(client)
        return sha_to_process, sha_result

    with contextlib.ExitStack() as exit_stack:
        input_stream = sys.stdin
        if f := opts.input:
            input_stream = exit_stack.enter_context(f.open(encoding="utf-8"))

        output_stream = exit_stack.enter_context(
            opts.output.open("w", encoding="utf-8")
        )
        pool = exit_stack.enter_context(
            multiprocessing.pool.ThreadPool(processes=jobs)
        )
        input_lines = (x.strip() for x in input_stream)
        input_shas = (x for x in input_lines if x)
        for sha, result in pool.imap_unordered(run_one_sha, input_shas):
            write_one_result(output_stream, sha, result)
