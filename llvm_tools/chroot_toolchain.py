# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Script to manage LLVM packages in the chroot.

The goal of this script is to allow for easier `workon`, installing, and
'resetting' of LLVM packages. It addresses a central difficulty: the LLVM
codebase is installed by a series of packages in ChromeOS - some parts like
Clang are installed 'everywhere' by simply `emerge sys-devel/llvm`, but others
(e.g., libcxx) get installed by a mixture of `cross-*` and `sys-libs/` packages.

This must be run inside of the chroot.
"""

import argparse
import logging
from pathlib import Path
import shlex
import subprocess

from llvm_tools import chroot


CROSS_COMPILE_TARGETS = (
    "cross-aarch64-cros-linux-gnu",
    "cross-arm-none-eabi",
    "cross-armv7a-cros-linux-gnueabihf",
    "cross-armv7m-cros-eabi",
    "cross-riscv32-cros-elf",
    "cross-x86_64-cros-linux-gnu",
)

CROSS_COMPILE_PKGS = (
    "libcxx",
    "llvm-libunwind",
    "compiler-rt",
)

BOARD_PKGS = (
    "sys-libs/libcxx",
    "sys-libs/llvm-libunwind",
    "sys-libs/compiler-rt",
    "sys-libs/scudo",
    "dev-util/lldb-server",
)


def get_cross_compile_combinations() -> list[str]:
    """Generates all cross-compile combinations."""
    results = []
    for category in CROSS_COMPILE_TARGETS:
        results.extend(
            [
                f"{category}/{pkg}"
                for pkg in CROSS_COMPILE_PKGS
                # Filter out cross-x86_64-cros-linux-gnu/compiler-rt as it
                # conflicts with files installed by sys-devel/llvm. This
                # conflict is intentional: sys-devel/llvm needs to provide them
                # for host binaries anyway. This package is therefore
                # effectively 'dead'.
                if not (
                    category == "cross-x86_64-cros-linux-gnu"
                    and pkg == "compiler-rt"
                )
            ]
        )
    return results


def raise_if_board_not_set_up(board: str) -> None:
    """Raises ValueError if the board is not set up."""
    if not Path(f"/build/{board}").is_dir():
        raise ValueError(
            f"/build/{board} does not exist. Did you run setup_board?"
        )


def handle_workon(*, start: bool, host: bool, board: str | None) -> None:
    """Handles the workon subcommand."""
    if board:
        raise_if_board_not_set_up(board)

    action = "start" if start else "stop"

    if host:
        cross_combos = get_cross_compile_combinations()

        host_cmd = [
            "cros",
            "workon",
            "--host",
            action,
            "sys-devel/llvm",
            "sys-libs/libcxx",
            "sys-libs/llvm-libunwind",
            "sys-libs/scudo",
            "dev-util/lldb-server",
        ] + cross_combos

        logging.info("Running: %s", shlex.join(host_cmd))
        subprocess.run(host_cmd, check=True, stdin=subprocess.DEVNULL)

        logging.info("Set up host packages!")

    if not board:
        return

    board_cmd = ["cros", "workon", "-b", board, action]
    board_cmd += BOARD_PKGS
    logging.info("Running: %s", shlex.join(board_cmd))
    subprocess.run(board_cmd, check=True, stdin=subprocess.DEVNULL)


def handle_build() -> None:
    """Handles the build subcommand."""
    cmd1 = (
        "sudo",
        "emerge",
        "-j",
        "sys-devel/llvm",
        "sys-libs/libcxx",
        "sys-libs/llvm-libunwind",
        "sys-libs/scudo",
    )
    logging.info("Running: %s", shlex.join(cmd1))
    subprocess.run(
        cmd1,
        check=True,
        stdin=subprocess.DEVNULL,
    )

    cross_combos = get_cross_compile_combinations()
    cmd2 = ["sudo", "emerge", "-j"] + cross_combos
    logging.info("Running: %s", shlex.join(cmd2))
    subprocess.run(cmd2, check=True, stdin=subprocess.DEVNULL)


def clean_up_old_binpkgs(
    packages: list[str], pkg_root: Path = Path("/var/lib/portage/pkgs")
) -> None:
    """Finds and deletes old binpkgs for specified packages."""
    files_to_delete = []
    for pkg_atom in packages:
        parts = pkg_atom.split("/")
        if len(parts) != 2:
            logging.warning("Skipping invalid package atom: %s", pkg_atom)
            continue
        category, name = parts
        pkg_dir = pkg_root / category

        if not pkg_dir.is_dir():
            continue
        for file_path in pkg_dir.glob(f"{name}-*.tbz2"):
            files_to_delete.append(str(file_path))

    if files_to_delete:
        cmd = ["sudo", "rm", "-f"] + files_to_delete
        logging.info("Running: %s", shlex.join(cmd))
        try:
            subprocess.run(cmd, check=True, stdin=subprocess.DEVNULL)
        except subprocess.CalledProcessError as e:
            logging.error("Failed to delete binpkgs: %s", e)


def handle_force_reset() -> None:
    """Handles the force-reset subcommand."""
    cross_combos = get_cross_compile_combinations()
    pkgs_to_stop = [
        "sys-devel/llvm",
        "sys-libs/libcxx",
        "sys-libs/llvm-libunwind",
        "sys-libs/scudo",
        "dev-util/lldb-server",
    ] + cross_combos

    cmd1 = ["cros", "workon", "--host", "stop"] + pkgs_to_stop
    logging.info("Running: %s", shlex.join(cmd1))
    subprocess.run(cmd1, check=True, stdin=subprocess.DEVNULL)

    # The old binpkgs for these **may** be invalid: if a user edited the llvm
    # ebuild directly, for instance, without `cros workon`'ing it, portage will
    # save the resultant binpkg and reuse that if reinstallation is requested
    # via -G.
    #
    # Forcing a redownload is the only way to be assured that we're resetting to
    # a landed version of LLVM.
    clean_up_old_binpkgs(pkgs_to_stop)

    cmd2 = ["sudo", "emerge", "-G"] + pkgs_to_stop
    logging.info("Running: %s", shlex.join(cmd2))
    subprocess.run(cmd2, check=True, stdin=subprocess.DEVNULL)


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parses arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--debug", action="store_true", help="Enable debug logging"
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    workon_parser = subparsers.add_parser("workon", help="Manage workon status")
    workon_action_group = workon_parser.add_mutually_exclusive_group(
        required=True
    )
    workon_action_group.add_argument(
        "--start", action="store_true", help="Start workon"
    )
    workon_action_group.add_argument(
        "--stop", action="store_true", help="Stop workon"
    )

    workon_parser.add_argument(
        "--host",
        action="store_true",
        help="Workon host packages (including `cross-*`)",
    )
    workon_parser.add_argument("--board", help="Board to operate on")

    build_parser = subparsers.add_parser(
        "build",
        help="Build all LLVM packages for either the host or a board.",
    )
    # TODO(gbiv): Add `--board` here. It doesn't exist at present, so the review
    # of this script is easier.
    build_parser.add_argument(
        "--host",
        action="store_true",
        required=True,
        help="Build host packages (including `cross-*`)",
    )

    force_reset_parser = subparsers.add_parser(
        "force-reset",
        help="""
        Forcibly reset to baseline toolchain packages. WARNING: use with care.
        This will reset toolchain packages, but any build _artifacts_ that were
        generated with these will not be cleaned. This can easily result in a
        broken chroot. It is **highly** recommended to just
        `cros_sdk --replace --delete-out-dir` instead.
        """,
    )
    force_reset_parser.add_argument(
        "--stop-workon",
        action="store_true",
        required=True,
        help="Pass to acknowledge that workon of everything will be stopped.",
    )
    # TODO(gbiv): Add `--board` here. It doesn't exist at present, so the review
    # of this script is easier.
    force_reset_parser.add_argument(
        "--host", action="store_true", required=True, help="Operate on host"
    )
    opts = parser.parse_args(argv)
    if opts.command == "workon" and not (opts.host or opts.board):
        parser.error("workon requires --host and/or --board")
    return opts


def main(argv: list[str]) -> None:
    opts = parse_args(argv)

    # Verify after parsing, so at least `--help` works outside.
    chroot.VerifyInsideChroot()

    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.DEBUG if opts.debug else logging.INFO,
    )

    match opts.command:
        case "workon":
            assert (
                opts.start != opts.stop
            ), "These must be required and mutually exclusive"
            handle_workon(
                start=opts.start,
                host=opts.host,
                board=opts.board,
            )
        case "build":
            handle_build()
        case "force-reset":
            handle_force_reset()
        case _:
            assert False, "Unhandled command"
