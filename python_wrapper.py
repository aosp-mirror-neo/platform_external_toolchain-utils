#!/usr/bin/env python3
# Copyright 2024 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Wrapper for Python scripts in toolchain-utils.

Python scripts here assume that they can import arbitrary modules. This is only
consistently possible if the root of toolchain-utils is on PYTHONPATH. The
simplest way to make that happen is to wrap the scripts.

py/bin/foo.py will be invoked _similarly to_ `PYTHONPATH=. ./foo.py`. If the
script has a `main` or `_main` function defined, it will be called with a
single argument: `sys.argv[1:]`.
"""

import importlib.util
import inspect
import os
from pathlib import Path
import sys
from typing import Any, Callable


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


def main():
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
    if not spec.loader:
        raise ValueError(f"Spec for {module_name} does not have a loader")
    spec.loader.exec_module(main_module)

    # We have various `main` conventions to support here, unfortunately:
    # - Some return None; others return an exit code.
    # - Some take argv; others take no args.
    # - Some are called `main`, others are called `_main`.
    # - Some capitalize `Main`, others don't.
    # It'd be nice to make this more uniform, but it's easy enough to handle
    # all of these until that happens.
    main_fns = (
        "main",
        "Main",
        "_main",
        "_Main",
    )
    main_fn: Callable[..., Any]
    for f in main_fns:
        if my_fn := getattr(main_module, f, None):
            main_fn = my_fn
            break
    else:
        sys.exit(
            f"No function called any of {main_fns} declared in {main_file}."
        )
    if inspect.signature(main_fn).parameters:
        result = main_fn(sys.argv[1:])
    else:
        result = main_fn()
    if result:
        sys.exit(result)


if __name__ == "__main__":
    main()
