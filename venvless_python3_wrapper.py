#!/usr/bin/env python3
# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Wrapper for 'venvless' Python scripts in toolchain-utils.

A very small subset of Python scripts in toolchain-utils (currently just
`llvm_tools/patch_manager.py`) need to be able to operate with just a baseline
Python installation. This handles invoking them.

**Please** try not to add more users of this. All of their transitive
dependencies (that can't be made optional) are required to operate without a
venv.
"""

import importlib.util
import os
from pathlib import Path
import sys
import typing
from typing import Callable


def find_file_to_execute(argv0: str) -> Path:
    symlink_path = Path(os.getcwd(), argv0)
    symlink_parent = symlink_path.parent.resolve()
    me = (symlink_parent / symlink_path.name).resolve()
    toolchain_utils = me.parent
    relative_script_path = (
        symlink_parent.relative_to(toolchain_utils) / symlink_path.name
    )
    prefix = "py/bin/"
    relative_script_path_str = str(relative_script_path)
    if not relative_script_path_str.startswith(prefix):
        raise ValueError(
            f"Expected argv0 to be in {prefix} - it's {relative_script_path}"
        )
    target_script = relative_script_path_str[len(prefix) :]
    result = toolchain_utils / target_script
    if not result.exists():
        sys.exit(f"No script found at {target_script} - can't execute")
    return result


def main() -> None:
    main_file = find_file_to_execute(sys.argv[0])
    module_name = main_file.with_suffix("").name
    spec = importlib.util.spec_from_file_location(
        module_name,
        main_file,
    )
    if not spec:
        raise ValueError(f"Could not retrieve spec from module {module_name}")
    main_module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = main_module
    # `mypy` complains if `spec.loader` isn't checked, because it could've been
    # None in previous versions of Python. Assert it's non-None to assuage it.
    assert spec.loader, f"Spec for {module_name} does not have a loader"
    spec.loader.exec_module(main_module)

    # Provide less flexibility than our venv wrapper: main must be named `main`,
    # must take `sys.argv`, and must either return an int or None (both of which
    # are accepted by sys.exit; the latter is interpreted as exit(0)).
    maybe_main_fn = getattr(main_module, "main", None)
    if not maybe_main_fn:
        sys.exit(f"No function called main declared in {main_file}.")
    main_fn = typing.cast(Callable[[list[str]], int | None], maybe_main_fn)
    # Despite the cast above, `cros lint` complains that `main_fn` isn't
    # callable.
    # pylint: disable=not-callable
    sys.exit(main_fn(sys.argv[1:]))


if __name__ == "__main__":
    main()
