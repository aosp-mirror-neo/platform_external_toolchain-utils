# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for check_reverts."""

import dataclasses
import json
import re
import time
from typing import Any, Self
import unittest
from unittest import mock

from llvm_tools.gemini_api import check_reverts


def mock_gemini_response(
    text: str | None = None,
    thinking_text: str | None = None,
    has_candidates: bool = True,
) -> mock.MagicMock:
    """Helper to create a mock Gemini response."""
    mock_response = mock.MagicMock()
    mock_response.usage_metadata = None
    if not has_candidates:
        mock_response.candidates = []
        return mock_response

    mock_candidate = mock.MagicMock()
    parts = []
    if thinking_text is not None:
        mock_thinking_part = mock.MagicMock()
        mock_thinking_part.thought = True
        mock_thinking_part.text = thinking_text
        parts.append(mock_thinking_part)
    if text is not None:
        mock_result_part = mock.MagicMock()
        mock_result_part.thought = False
        mock_result_part.text = text
        parts.append(mock_result_part)

    mock_candidate.content.parts = parts
    mock_response.candidates = [mock_candidate]
    return mock_response


@dataclasses.dataclass(frozen=True)
class FakeInference:
    """A fake inference type for testing generic functions."""

    some_string: str
    some_int: int

    @classmethod
    def from_json_checked(cls, json_object: dict[str, Any]) -> Self:
        some_string = check_reverts.get_dict_elem_with_type(
            json_object, "some_string", str
        )
        some_int = check_reverts.get_dict_elem_with_type(
            json_object, "some_int", int
        )
        return cls(some_string=some_string, some_int=some_int)

    def to_json(self) -> Any:
        return dataclasses.asdict(self)


class GetDictElemWithTypeTest(unittest.TestCase):
    """Tests for get_dict_elem_with_type."""

    def test_success_primitive(self) -> None:
        d = {"a": 1, "b": "hello", "c": True}
        self.assertEqual(check_reverts.get_dict_elem_with_type(d, "a", int), 1)
        self.assertEqual(
            check_reverts.get_dict_elem_with_type(d, "b", str), "hello"
        )
        self.assertEqual(
            check_reverts.get_dict_elem_with_type(d, "c", bool), True
        )

    def test_success_list(self) -> None:
        d = {"a": ["foo", "bar"], "b": [1, 2, 3]}
        self.assertEqual(
            check_reverts.get_dict_elem_with_type(d, "a", list[str]),
            ["foo", "bar"],
        )
        self.assertEqual(
            check_reverts.get_dict_elem_with_type(d, "b", list[int]), [1, 2, 3]
        )

    def test_success_empty_list(self) -> None:
        d: dict[str, Any] = {"a": []}
        self.assertEqual(
            check_reverts.get_dict_elem_with_type(d, "a", list[str]), []
        )

    def test_key_missing(self) -> None:
        d = {"a": 1}
        with self.assertRaisesRegex(ValueError, "No b key in {'a': 1}"):
            check_reverts.get_dict_elem_with_type(d, "b", str)

    def test_wrong_primitive_type(self) -> None:
        d = {"a": 1}
        with self.assertRaisesRegex(
            ValueError,
            "Key a is of type <class 'int'>; wanted <class 'str'>.*",
        ):
            check_reverts.get_dict_elem_with_type(d, "a", str)

    def test_bool_doesnt_pass_as_int(self) -> None:
        d = {"a": True}
        with self.assertRaisesRegex(
            ValueError,
            "Key a is of type <class 'bool'>; wanted <class 'int'>.*",
        ):
            check_reverts.get_dict_elem_with_type(d, "a", int)

    def test_not_a_list(self) -> None:
        d = {"a": 1}
        with self.assertRaisesRegex(
            ValueError,
            re.escape("Key a is of type <class 'int'>, not list[str]"),
        ):
            check_reverts.get_dict_elem_with_type(d, "a", list[str])

    def test_list_with_wrong_element_type(self) -> None:
        d = {"a": ["foo", 1]}
        with self.assertRaisesRegex(
            ValueError,
            "Element 1 of list is <class 'int'>, not <class 'str'>",
        ):
            check_reverts.get_dict_elem_with_type(d, "a", list[str])

    def test_list_with_bool_doesnt_pass_as_int(self) -> None:
        d = {"a": [1, True, 2]}
        with self.assertRaisesRegex(
            ValueError,
            "Element True of list is <class 'bool'>, not <class 'int'>",
        ):
            check_reverts.get_dict_elem_with_type(d, "a", list[int])


class ParseGeminiResponseTest(unittest.TestCase):
    """Tests for parse_gemini_response."""

    def test_success(self) -> None:
        mock_response = mock_gemini_response(
            text=json.dumps({"some_string": "hello", "some_int": 123})
        )

        result = check_reverts.parse_gemini_response(
            "some_sha", mock_response, FakeInference
        )
        self.assertEqual(
            result, FakeInference(some_string="hello", some_int=123)
        )


class QueryGeminiTest(unittest.TestCase):
    """Tests for query_gemini."""

    @mock.patch.object(time, "sleep", autospec=True)
    def test_query_gemini_success_first_try(
        self, mock_sleep: mock.MagicMock
    ) -> None:
        mock_client = mock.MagicMock()
        mock_chat = mock.MagicMock()
        mock_client.chats.create.return_value = mock_chat

        mock_response = mock_gemini_response(
            text=json.dumps({"some_string": "hello", "some_int": 123})
        )
        mock_chat.send_message.return_value = mock_response

        result = check_reverts.query_gemini(
            client=mock_client,
            system_prompt="prompt",
            response_schema=FakeInference,
            prompt_content="content",
            sha="sha",
        )

        self.assertEqual(
            result, FakeInference(some_string="hello", some_int=123)
        )
        mock_client.chats.create.assert_called_once_with(
            model=check_reverts.GEMINI_MODEL, config=mock.ANY
        )
        mock_chat.send_message.assert_called_once_with("content")
        mock_sleep.assert_not_called()

    @mock.patch.object(time, "sleep", autospec=True)
    def test_query_gemini_retry_on_broken_response(
        self, mock_sleep: mock.MagicMock
    ) -> None:
        mock_client = mock.MagicMock()
        mock_chat = mock.MagicMock()
        mock_client.chats.create.return_value = mock_chat

        mock_response_broken = mock_gemini_response(text="broken json")
        mock_response_success = mock_gemini_response(
            text=json.dumps({"some_string": "hello", "some_int": 123})
        )

        mock_chat.send_message.side_effect = [
            mock_response_broken,
            mock_response_success,
        ]

        result = check_reverts.query_gemini(
            client=mock_client,
            system_prompt="prompt",
            response_schema=FakeInference,
            prompt_content="content",
            sha="sha",
        )

        self.assertEqual(
            result, FakeInference(some_string="hello", some_int=123)
        )
        mock_client.chats.create.assert_called_once_with(
            model=check_reverts.GEMINI_MODEL, config=mock.ANY
        )
        self.assertEqual(mock_chat.send_message.call_count, 2)
        mock_chat.send_message.assert_has_calls(
            [mock.call("content"), mock.call(mock.ANY)]
        )
        mock_sleep.assert_not_called()

    @mock.patch.object(time, "sleep", autospec=True)
    def test_query_gemini_retry_loop(self, mock_sleep: mock.MagicMock) -> None:
        mock_client = mock.MagicMock()

        mock_chat1 = mock.MagicMock()
        mock_response_broken1 = mock_gemini_response(text="broken 1")
        mock_response_broken2 = mock_gemini_response(text="broken 2")

        mock_chat1.send_message.side_effect = [
            mock_response_broken1,
            mock_response_broken2,
        ]

        mock_chat2 = mock.MagicMock()
        mock_response_success = mock_gemini_response(
            text=json.dumps({"some_string": "hello", "some_int": 123})
        )
        mock_chat2.send_message.return_value = mock_response_success

        mock_client.chats.create.side_effect = [mock_chat1, mock_chat2]

        result = check_reverts.query_gemini(
            client=mock_client,
            system_prompt="prompt",
            response_schema=FakeInference,
            prompt_content="content",
            sha="sha",
        )

        self.assertEqual(
            result, FakeInference(some_string="hello", some_int=123)
        )
        mock_client.chats.create.assert_has_calls(
            [
                mock.call(model=check_reverts.GEMINI_MODEL, config=mock.ANY),
                mock.call(model=check_reverts.GEMINI_MODEL, config=mock.ANY),
            ]
        )
        mock_chat1.send_message.assert_has_calls(
            [mock.call("content"), mock.call(mock.ANY)]
        )
        mock_chat2.send_message.assert_called_once_with("content")
        # Backoff for first failed attempt (i=1).
        mock_sleep.assert_called_once_with(2)

    @mock.patch.object(time, "sleep", autospec=True)
    def test_query_gemini_retry_on_api_error_recreates_chat(
        self, mock_sleep: mock.MagicMock
    ) -> None:
        mock_client = mock.MagicMock()

        # Chat 1 returns no candidates (API error).
        mock_chat1 = mock.MagicMock()
        mock_response_no_candidates = mock_gemini_response(has_candidates=False)
        mock_chat1.send_message.return_value = mock_response_no_candidates

        # Chat 2 returns success.
        mock_chat2 = mock.MagicMock()
        mock_response_success = mock_gemini_response(
            text=json.dumps({"some_string": "hello", "some_int": 123})
        )
        mock_chat2.send_message.return_value = mock_response_success

        mock_client.chats.create.side_effect = [mock_chat1, mock_chat2]

        result = check_reverts.query_gemini(
            client=mock_client,
            system_prompt="prompt",
            response_schema=FakeInference,
            prompt_content="content",
            sha="sha",
        )

        self.assertEqual(
            result, FakeInference(some_string="hello", some_int=123)
        )
        mock_client.chats.create.assert_has_calls(
            [
                mock.call(model=check_reverts.GEMINI_MODEL, config=mock.ANY),
                mock.call(model=check_reverts.GEMINI_MODEL, config=mock.ANY),
            ]
        )
        mock_chat1.send_message.assert_called_once_with("content")
        mock_chat2.send_message.assert_called_once_with("content")
        mock_sleep.assert_called_once_with(2)
