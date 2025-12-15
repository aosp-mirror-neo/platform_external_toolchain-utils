# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tests for check_reverts."""

import re
import unittest

from llvm_tools.gemini_api import check_reverts


class GetDictElemWithTypeTest(unittest.TestCase):
    """Tests for get_dict_elem_with_type."""

    def test_success_primitive(self):
        d = {"a": 1, "b": "hello", "c": True}
        self.assertEqual(check_reverts.get_dict_elem_with_type(d, "a", int), 1)
        self.assertEqual(
            check_reverts.get_dict_elem_with_type(d, "b", str), "hello"
        )
        self.assertEqual(
            check_reverts.get_dict_elem_with_type(d, "c", bool), True
        )

    def test_success_list(self):
        d = {"a": ["foo", "bar"], "b": [1, 2, 3]}
        self.assertEqual(
            check_reverts.get_dict_elem_with_type(d, "a", list[str]),
            ["foo", "bar"],
        )
        self.assertEqual(
            check_reverts.get_dict_elem_with_type(d, "b", list[int]), [1, 2, 3]
        )

    def test_success_empty_list(self):
        d = {"a": []}
        self.assertEqual(
            check_reverts.get_dict_elem_with_type(d, "a", list[str]), []
        )

    def test_key_missing(self):
        d = {"a": 1}
        with self.assertRaisesRegex(ValueError, "No b key in {'a': 1}"):
            check_reverts.get_dict_elem_with_type(d, "b", str)

    def test_wrong_primitive_type(self):
        d = {"a": 1}
        with self.assertRaisesRegex(
            ValueError,
            "Key a is of type <class 'int'>; wanted <class 'str'>.*",
        ):
            check_reverts.get_dict_elem_with_type(d, "a", str)

    def test_bool_doesnt_pass_as_int(self):
        d = {"a": True}
        with self.assertRaisesRegex(
            ValueError,
            "Key a is of type <class 'bool'>; wanted <class 'int'>.*",
        ):
            check_reverts.get_dict_elem_with_type(d, "a", int)

    def test_not_a_list(self):
        d = {"a": 1}
        with self.assertRaisesRegex(
            ValueError,
            re.escape("Key a is of type <class 'int'>, not list[str]"),
        ):
            check_reverts.get_dict_elem_with_type(d, "a", list[str])

    def test_list_with_wrong_element_type(self):
        d = {"a": ["foo", 1]}
        with self.assertRaisesRegex(
            ValueError,
            "Element 1 of list is <class 'int'>, not <class 'str'>",
        ):
            check_reverts.get_dict_elem_with_type(d, "a", list[str])

    def test_list_with_bool_doesnt_pass_as_int(self):
        d = {"a": [1, True, 2]}
        with self.assertRaisesRegex(
            ValueError,
            "Element True of list is <class 'bool'>, not <class 'int'>",
        ):
            check_reverts.get_dict_elem_with_type(d, "a", list[int])
