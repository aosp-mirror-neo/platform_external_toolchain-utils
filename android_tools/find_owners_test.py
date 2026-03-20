# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for find_owners."""

import textwrap

from android_tools import find_owners
from llvm_tools import test_helpers


# pylint: disable=protected-access


class TestFindBestOwnersFrom(test_helpers.TempDirTestCase):
    """Tests for _find_best_owners_from."""

    def test_no_candidates(self) -> None:
        self.assertEqual(find_owners._find_best_owners_from([]), [])

    def test_single_candidate(self) -> None:
        per_file_candidates = [
            [
                find_owners.OwnersSuggestion(
                    username="user1",
                    distance=1,
                    is_explicitly_mentioned=False,
                    is_last_resort=True,
                )
            ]
        ]
        self.assertEqual(
            find_owners._find_best_owners_from(per_file_candidates), ["user1"]
        )

    def test_multiple_candidates_different_scores_num_owned_files(self) -> None:
        per_file_candidates = [
            [
                find_owners.OwnersSuggestion(
                    username="user1",
                    distance=1,
                    is_explicitly_mentioned=True,
                    is_last_resort=False,
                ),
                find_owners.OwnersSuggestion(
                    username="user2",
                    distance=1,
                    is_explicitly_mentioned=True,
                    is_last_resort=False,
                ),
            ],
            [
                find_owners.OwnersSuggestion(
                    username="user1",
                    distance=1,
                    is_explicitly_mentioned=True,
                    is_last_resort=False,
                )
            ],
        ]
        self.assertEqual(
            find_owners._find_best_owners_from(per_file_candidates), ["user1"]
        )

    def test_multiple_candidates_different_scores_last_resort(self) -> None:
        per_file_candidates = [
            [
                find_owners.OwnersSuggestion(
                    username="user1",
                    distance=1,
                    is_explicitly_mentioned=True,
                    is_last_resort=True,
                ),
                find_owners.OwnersSuggestion(
                    username="user2",
                    distance=1,
                    is_explicitly_mentioned=True,
                    is_last_resort=False,
                ),
            ]
        ]
        self.assertEqual(
            find_owners._find_best_owners_from(per_file_candidates), ["user2"]
        )

    def test_multiple_candidates_different_scores_distance(self) -> None:
        per_file_candidates = [
            [
                find_owners.OwnersSuggestion(
                    username="user1",
                    distance=2,
                    is_explicitly_mentioned=True,
                    is_last_resort=False,
                ),
                find_owners.OwnersSuggestion(
                    username="user2",
                    distance=1,
                    is_explicitly_mentioned=True,
                    is_last_resort=False,
                ),
            ]
        ]
        self.assertEqual(
            find_owners._find_best_owners_from(per_file_candidates), ["user2"]
        )

    def test_multiple_candidates_equal_best_scores(self) -> None:
        per_file_candidates = [
            [
                find_owners.OwnersSuggestion(
                    username="user1",
                    distance=1,
                    is_explicitly_mentioned=True,
                    is_last_resort=False,
                ),
                find_owners.OwnersSuggestion(
                    username="user2",
                    distance=1,
                    is_explicitly_mentioned=True,
                    is_last_resort=False,
                ),
            ]
        ]
        # Should return sorted list of users with equal best scores.
        self.assertEqual(
            find_owners._find_best_owners_from(per_file_candidates),
            ["user1", "user2"],
        )

    def test_candidates_with_none_distance(self) -> None:
        per_file_candidates = [
            [
                find_owners.OwnersSuggestion(
                    username="user1",
                    distance=None,
                    is_explicitly_mentioned=True,
                    is_last_resort=False,
                )
            ]
        ]
        self.assertEqual(
            find_owners._find_best_owners_from(per_file_candidates), ["user1"]
        )

    def test_multiple_candidates_different_scores_explicit_mentions(
        self,
    ) -> None:
        per_file_candidates = [
            [
                find_owners.OwnersSuggestion(
                    username="user1",
                    distance=1,
                    is_explicitly_mentioned=True,
                    is_last_resort=False,
                ),
                find_owners.OwnersSuggestion(
                    username="user2",
                    distance=1,
                    is_explicitly_mentioned=False,
                    is_last_resort=False,
                ),
            ]
        ]
        self.assertEqual(
            find_owners._find_best_owners_from(per_file_candidates), ["user1"]
        )

    def test_candidates_with_none_distance_and_known_distance(self) -> None:
        per_file_candidates = [
            [
                find_owners.OwnersSuggestion(
                    username="user1",
                    distance=None,
                    is_explicitly_mentioned=True,
                    is_last_resort=False,
                ),
                find_owners.OwnersSuggestion(
                    username="user2",
                    distance=1,
                    is_explicitly_mentioned=True,
                    is_last_resort=False,
                ),
            ]
        ]
        # user2 has a smaller distance (1 vs default_distance=2 for user1)
        self.assertEqual(
            find_owners._find_best_owners_from(per_file_candidates), ["user2"]
        )

    def test_last_resort_with_none_distance(self) -> None:
        per_file_candidates = [
            [
                find_owners.OwnersSuggestion(
                    username="user1",
                    distance=None,
                    is_explicitly_mentioned=True,
                    is_last_resort=True,
                ),
                find_owners.OwnersSuggestion(
                    username="user2",
                    distance=1,
                    is_explicitly_mentioned=True,
                    is_last_resort=False,
                ),
            ]
        ]
        # user2 is preferred because user1 is a last resort, even with None
        # distance
        self.assertEqual(
            find_owners._find_best_owners_from(per_file_candidates), ["user2"]
        )


class TestParseSuggestionsForGooglers(test_helpers.TempDirTestCase):
    """Tests for _parse_suggestions_for_googlers."""

    def test_valid_response_body(self) -> None:
        response_body = textwrap.dedent(
            """\
            )]}'
            {
              "code_owners": [
                {
                  "account": {
                    "_account_id": 1234,
                    "name": "Alex Doe",
                    "email": "a@google.com"
                  },
                  "scorings": {
                    "DISTANCE": 1,
                    "IS_EXPLICITLY_MENTIONED": 1,
                    "LAST_RESORT_SUGGESTION": 0
                  }
                },
                {
                  "account": {
                    "_account_id": 5678,
                    "name": "Jane Smith",
                    "email": "janesmith@google.com"
                  },
                  "scorings": {
                    "DISTANCE": 2,
                    "IS_EXPLICITLY_MENTIONED": 0,
                    "LAST_RESORT_SUGGESTION": 1
                  }
                }
              ]
            }
            """
        )
        expected_suggestions = [
            find_owners.OwnersSuggestion(
                username="a",
                distance=1,
                is_explicitly_mentioned=True,
                is_last_resort=False,
            ),
            find_owners.OwnersSuggestion(
                username="janesmith",
                distance=2,
                is_explicitly_mentioned=False,
                is_last_resort=True,
            ),
        ]
        result = find_owners._parse_suggestions_for_googlers(
            "http://example.com", response_body
        )
        self.assertCountEqual(result, expected_suggestions)

    def test_empty_response_body(self) -> None:
        response_body = textwrap.dedent(
            """\
            )]}'
            {
              "code_owners": []
            }
            """
        )
        result = find_owners._parse_suggestions_for_googlers(
            "http://example.com", response_body
        )
        self.assertEqual(result, [])

    def test_filtered_response_bodies(self) -> None:
        response_body = textwrap.dedent(
            """\
            )]}'
            {
              "code_owners": [
                {
                  "account": {
                    "_account_id": 1234,
                    "name": "Alex Doe",
                    "email": "a@chromium.org"
                  },
                  "scorings": {
                    "DISTANCE": 1,
                    "IS_EXPLICITLY_MENTIONED": 1,
                    "LAST_RESORT_SUGGESTION": 0
                  }
                },
                {
                  "account": {
                    "_account_id": 1234,
                    "name": "Alex Doe"
                  },
                  "scorings": {
                    "DISTANCE": 1,
                    "IS_EXPLICITLY_MENTIONED": 1,
                    "LAST_RESORT_SUGGESTION": 0
                  }
                },
                {
                  "account": {
                    "_account_id": 1234,
                    "name": "Alex Doe",
                    "email": "a@google.com"
                  }
                }
              ]
            }
            """
        )
        result = find_owners._parse_suggestions_for_googlers(
            "http://example.com", response_body
        )
        self.assertEqual(result, [])
