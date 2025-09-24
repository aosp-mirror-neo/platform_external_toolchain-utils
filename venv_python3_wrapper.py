# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Wrapper for Python scripts in toolchain-utils.

Python scripts here assume that they can import arbitrary modules. This is only
consistently possible if the root of toolchain-utils is on PYTHONPATH. The
simplest way to make that happen is to wrap the scripts.

py/bin/foo.py is expected to be a symlink to ./venv/venv_python3_wrapper.sh,
which invokes this script using the virtualenv's interpreter, with `"$0" "$@"`
as args (so `sys.argv[1]` is "$0"). This script's job is then invoking
`./foo.py`'s `main` with the `$@` args.
"""

import importlib.util
import inspect
import os
from pathlib import Path
import sys
import typing
from typing import Any, Callable


def find_file_to_execute(argv0: str) -> Path:
    symlink_path = Path(os.getcwd(), argv0)
    symlink_parent = symlink_path.parent.resolve()
    me = (symlink_parent / symlink_path.name).resolve()
    toolchain_utils = me.parent.parent
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
    main_file = find_file_to_execute(sys.argv[1])
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

    # We have a few `main` conventions to support here:
    # - Some return None; others return an exit code.
    # - Some take argv; others take no args.
    # It'd be nice to make this more uniform, but it's easy enough to handle
    # all of these until that happens.
    maybe_main_fn: Any = getattr(main_module, "main", None)
    if not maybe_main_fn:
        sys.exit(f"No function called 'main' declared in {main_module}.")

    if not callable(maybe_main_fn):
        sys.exit(f"'main' declared in {maybe_main_fn} is not callable.")

    main_fn = typing.cast(Callable[..., Any], maybe_main_fn)
    # Despite the checks and type assertion above, `cros lint` complains that
    # `main_fn` isn't callable.
    # pylint: disable=not-callable
    if inspect.signature(main_fn).parameters:
        result = main_fn(sys.argv[2:])
    else:
        result = main_fn()
    if result:
        sys.exit(result)


if __name__ == "__main__":
    main()
