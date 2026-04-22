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


def find_file_to_execute(argv0: str) -> tuple[Path, Path]:
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
        # TODO(b/505362208): Unconditionally add .py once the .py symlinks
        # no longer exist
        if not result.name.endswith(".py"):
            result_py = result.with_suffix(".py")
            if result_py.exists():
                return result_py, toolchain_utils
        sys.exit(f"No script found at {target_script} - can't execute")
    return result, toolchain_utils


def check_imports(toolchain_utils_root: Path) -> None:
    """Verifies that all loaded modules are either local or stdlib.

    We need verification _on top of_ just checking "does the import work with
    this wrapper?" since the system Python installation this is invoked against
    may have globally-installed 3p packages like `requests`, `yaml`, etc. This
    wrapper provides no real isolation from those.
    """
    # Identify obvious third-party locations.
    # This is a heuristic, but "venvless" scripts are expected to be simple.
    ignored_modules = (
        # _distutils_hack is distributed with Python, so can be safely ignored.
        "_distutils_hack",
    )

    all_stdlib_modules = sys.stdlib_module_names | set(sys.builtin_module_names)

    violations = []
    # NOTE: best practice recommended by Python's docs is to always take
    # `tuple(sys.modules)` to avoid modifications while we iterate.
    for module_name, module in tuple(sys.modules.items()):
        if module_name in ignored_modules:
            continue

        # If this starts with a stdlib path name, assume it's OK. This can
        # technically be evaded (e.g., by creating an `os.toolchain_utils` pip
        # package, but the intent of this isn't to guard against pathological
        # cases.
        root_pkg = module_name.split(".")[0]
        if root_pkg in all_stdlib_modules:
            continue

        if module_file := getattr(module, "__file__", None):
            module_file_path = Path(module_file).resolve()
            if module_file_path.is_relative_to(toolchain_utils_root):
                continue

            violations.append(
                f"Module '{module_name}' is loaded from '{module_file_path}'"
            )
            continue

        # If this module has __path__ and no __file__, it's a namespace package.
        # Note that `__path__` is a list of relevant paths.
        if module_paths := getattr(module, "__path__", None):
            for module_path in module_paths:
                module_file_path = Path(module_path).resolve()
                if not module_file_path.is_relative_to(toolchain_utils_root):
                    violations.append(
                        f"Namespace package '{module_name}' is loaded from "
                        f"'{module_paths}'"
                    )
                    break

            continue

        violations.append(
            f"Package '{module_name}' has no load point, but "
            "isn't from stdlib?"
        )

    if not violations:
        return

    violations.sort()
    sys.exit(
        "Error: The following modules appear to be third-party dependencies:\n"
        + "\n".join(violations)
        + "\n"
        "Venvless scripts may only depend on the stlidb and toolchain-utils."
    )


def main() -> None:
    main_file, toolchain_utils = find_file_to_execute(sys.argv[0])
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

    if os.environ.get("CROSTC_TEST_MODULE_IMPORTS"):
        print(
            "CROSTC_TEST_MODULE_IMPORTS found in env; testing module imports..."
        )
        check_imports(toolchain_utils)
        print("Module import tests passed.")
        return

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
