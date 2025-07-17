# Gemini Code Agent Guide

This document provides essential context for AI agents and developers interacting with the `toolchain-utils` repository.

## Critical Warnings

-   **`compiler_wrapper/` is auto-generated:** This directory contains a Go module that is overwritten by an automated process. **DO NOT EDIT FILES HERE.** Changes should be made to the upstream source located in the `../chromiumos-overlay/sys-devel/llvm/files/compiler_wrapper` directory.
-   **`llvm_tools/llvm-project-copy/` is a clone:** If this directory exists, it is a temporary clone of the upstream LLVM project. It should never be modified as part of regular activities.

## Project Overview

This repository contains a collection of scripts and tools for supporting the ChromeOS and Android toolchains. It includes a compiler wrapper, tools for profile-guided optimization (PGO) and AutoFDO, automation for CI/CD bots, and utilities for interacting with the ChromeOS development environment.

### Key Directories

-   `cros_utils/`: A core library of **Python** utilities for common tasks (Git, Google Storage, etc.).
-   `afdo_tools/` & `pgo_tools/`: **Python** scripts for managing AutoFDO and PGO profiles.
-   `bot_tools/`: **Python** scripts for interacting with ChromeOS CI/CQ bots.
-   `llvm_tools/` & `llvm_patches/`: Tools and patches related to the LLVM/Clang toolchain. Due to codebase evolution, this also contains some general Python modules.
-   `rust_tools/`: Tools for the Rust toolchain, including the critical `rust_uprev.py` script for upgrades.
-   `seccomp_tools/`: Tools for working with seccomp security policies.

## Development Workflow

### Languages and Dependencies

-   **Primary Languages:** Python, Rust.
-   **Python:**
    -   Dependencies are managed by the ChromeOS chroot and the `check-presubmit.py` script.
    -   Code is type-hinted (`mypy`/`pyright`) and follows Chromium style (`black`).
    -   Scripts are executed via symlinks in `py/bin/` which use `./python_wrapper.py` to handle imports.
-   **Rust**:
    -   Standard `cargo` commands (`fmt`, `clippy`, `test`) are used for formatting, linting, and testing within each crate's directory.

### Common Tasks

#### Running Tools

To execute a Python-based tool, use the symlinks in the `py/bin` directory. For example, to run the `afdo_tools/monitor_chrome_afdo.py` script, run

```bash
./py/bin/afdo_tools/monitor_chrome_afdo.py --chrome-tree=/path/to/chrome
```

#### Testing and Verification

1.  **Run All Python Tests:**
    To run the full Python test suite, execute the main test runner:
    ```bash
    ./run_python_tests.sh
    ```

2.  **Run Specific Python Tests:**
    To run a specific test file, pass it as an argument:
    ```bash
    ./run_python_tests.sh llvm_tools/atomic_write_file_test.py
    ```

3.   **When you are done with a series of changes**, you should run `./toolchain_utils_githooks/check-presubmit.py` to validate your work. It runs `mypy`, `black --check`, and `cros lint` on your changed files. It also runs all unittests.
    ```bash
    ./toolchain_utils_githooks/check-presubmit.py [your_changed_files]
    ```
