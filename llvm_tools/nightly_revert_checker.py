# Copyright 2020 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Checks for new reverts in LLVM on a nightly basis.

If any reverts are found that were previously unknown, this cherry-picks them or
fires off an email. All LLVM SHAs to monitor are autodetected.
"""

import argparse
import dataclasses
import json
import logging
import os
from pathlib import Path
import pprint
import re
import subprocess
import time
from typing import Any, Callable, Dict, Iterable, List, NamedTuple, Tuple

from cros_utils import email_sender
from cros_utils import git_utils
from cros_utils import tiny_render
from llvm_tools import get_llvm_hash
from llvm_tools import git_llvm_rev
from llvm_tools import patch_utils
from llvm_tools import revert_checker


ONE_DAY_SECS = 24 * 60 * 60
# How often to send an email about a HEAD not moving.
HEAD_STALENESS_ALERT_INTERVAL_SECS = 21 * ONE_DAY_SECS
# How long to wait after a HEAD changes for the first 'update' email to be sent.
HEAD_STALENESS_ALERT_INITIAL_SECS = 60 * ONE_DAY_SECS


# Not frozen, as `next_notification_timestamp` may be mutated.
@dataclasses.dataclass(frozen=False, eq=True)
class HeadInfo:
    """Information about about a HEAD that's tracked by this script."""

    # The most recent SHA observed for this HEAD.
    last_sha: str
    # The time at which the current value for this HEAD was first seen.
    first_seen_timestamp: int
    # The next timestamp to notify users if this HEAD doesn't move.
    next_notification_timestamp: int

    @classmethod
    def from_json(cls, json_object: Any) -> "HeadInfo":
        return cls(**json_object)

    def to_json(self) -> Any:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True, eq=True)
class State:
    """Persistent state for this script."""

    # Mapping of LLVM SHA -> List of reverts that have been seen for it
    seen_reverts: Dict[str, List[str]] = dataclasses.field(default_factory=dict)
    # Mapping of friendly HEAD name (e.g., main-legacy) to last-known info
    # about it.
    heads: Dict[str, HeadInfo] = dataclasses.field(default_factory=dict)

    @classmethod
    def from_json(cls, json_object: Any) -> "State":
        # Autoupgrade old JSON files.
        if "heads" not in json_object:
            json_object = {
                "seen_reverts": json_object,
                "heads": {},
            }

        return cls(
            seen_reverts=json_object["seen_reverts"],
            heads={
                k: HeadInfo.from_json(v)
                for k, v in json_object["heads"].items()
            },
        )

    def to_json(self) -> Any:
        return {
            "seen_reverts": self.seen_reverts,
            "heads": {k: v.to_json() for k, v in self.heads.items()},
        }


def _find_interesting_android_shas(
    android_llvm_toolchain_dir: str,
) -> List[Tuple[str, str]]:
    llvm_project = Path(android_llvm_toolchain_dir) / "toolchain/llvm-project"
    aosp_main_sha = git_utils.resolve_ref(llvm_project, "aosp/main")
    merge_base = subprocess.check_output(
        ["git", "merge-base", aosp_main_sha, "aosp/upstream-main"],
        cwd=llvm_project,
        encoding="utf-8",
    ).strip()
    logging.info(
        "Merge-base for aosp/main (HEAD == %s) and aosp/upstream-main is %s",
        aosp_main_sha,
        merge_base,
    )

    # Android no longer has a testing branch, so just follow main.
    return [("aosp/main", merge_base)]


def _find_interesting_chromeos_shas(
    chromeos_path: Path,
) -> List[Tuple[str, str]]:
    llvm_hash = get_llvm_hash.LLVMHash()

    current_llvm = llvm_hash.GetCrOSCurrentLLVMHash(chromeos_path)
    results: List[Tuple[str, str]] = [("llvm", current_llvm)]
    next_llvm = llvm_hash.GetCrOSLLVMNextHash()
    if current_llvm != next_llvm:
        results.append(("llvm-next", next_llvm))
    return results


_Email = NamedTuple(
    "_Email",
    [
        ("subject", str),
        ("body", tiny_render.Piece),
    ],
)


def _generate_revert_email(
    repository_name: str,
    friendly_name: str,
    sha: str,
    prettify_sha: Callable[[str], tiny_render.Piece],
    get_sha_description: Callable[[str], tiny_render.Piece],
    new_reverts: List[revert_checker.Revert],
) -> _Email:
    email_pieces = [
        "It looks like there may be %s across %s ("
        % (
            "a new revert" if len(new_reverts) == 1 else "new reverts",
            friendly_name,
        ),
        prettify_sha(sha),
        ").",
        tiny_render.line_break,
        tiny_render.line_break,
        "That is:" if len(new_reverts) == 1 else "These are:",
    ]

    revert_listing = []
    for revert in sorted(new_reverts, key=lambda r: r.sha):
        revert_listing.append(
            [
                prettify_sha(revert.sha),
                " (appears to revert ",
                prettify_sha(revert.reverted_sha),
                "): ",
                get_sha_description(revert.sha),
            ]
        )

    email_pieces.append(tiny_render.UnorderedList(items=revert_listing))
    email_pieces += [
        tiny_render.line_break,
        "PTAL and consider reverting them locally.",
    ]
    return _Email(
        subject="[revert-checker/%s] new %s discovered across %s"
        % (
            repository_name,
            "revert" if len(new_reverts) == 1 else "reverts",
            friendly_name,
        ),
        body=email_pieces,
    )


_EmailRecipients = NamedTuple(
    "_EmailRecipients",
    [
        ("well_known", List[str]),
        ("direct", List[str]),
    ],
)


def _send_revert_email(recipients: _EmailRecipients, email: _Email) -> None:
    email_sender.EmailSender().SendX20Email(
        subject=email.subject,
        identifier="revert-checker",
        well_known_recipients=recipients.well_known,
        direct_recipients=["gbiv@google.com"] + recipients.direct,
        text_body=tiny_render.render_text_pieces(email.body),
        html_body=tiny_render.render_html_pieces(email.body),
    )


def _write_state(state_file: str, new_state: State) -> None:
    tmp_file = state_file + ".new"
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(
                new_state.to_json(),
                f,
                sort_keys=True,
                indent=2,
                separators=(",", ": "),
            )
        os.rename(tmp_file, state_file)
    except:
        try:
            os.remove(tmp_file)
        except FileNotFoundError:
            pass
        raise


def _read_state(state_file: str) -> State:
    try:
        with open(state_file, encoding="utf-8") as f:
            return State.from_json(json.load(f))
    except FileNotFoundError:
        logging.info(
            "No state file found at %r; starting with an empty slate",
            state_file,
        )
        return State()


@dataclasses.dataclass(frozen=True)
class NewRevertInfo:
    """A list of new reverts for a given SHA."""

    friendly_name: str
    sha: str
    new_reverts: List[revert_checker.Revert]


def locate_new_reverts_across_shas(
    llvm_config: git_llvm_rev.LLVMConfig,
    upstream_main_branch: str,
    interesting_shas: List[Tuple[str, str]],
    state: State,
) -> Tuple[State, List[NewRevertInfo]]:
    """Locates and returns yet-unseen reverts across `interesting_shas`."""
    new_state = State()
    revert_infos = []
    for friendly_name, sha in interesting_shas:
        logging.info("Finding reverts across %s (%s)", friendly_name, sha)
        all_reverts = revert_checker.find_reverts(
            str(llvm_config.dir),
            sha,
            root=f"{llvm_config.remote}/{upstream_main_branch}",
        )
        logging.info(
            "Detected the following revert(s) across %s:\n%s",
            friendly_name,
            pprint.pformat(all_reverts),
        )

        new_state.seen_reverts[sha] = [r.sha for r in all_reverts]

        if sha not in state.seen_reverts:
            logging.info("SHA %s is new to me", sha)
            existing_reverts = set()
        else:
            existing_reverts = set(state.seen_reverts[sha])

        new_reverts = [r for r in all_reverts if r.sha not in existing_reverts]
        if not new_reverts:
            logging.info("...All of which have been reported.")
            continue

        new_head_info = None
        if old_head_info := state.heads.get(friendly_name):
            if old_head_info.last_sha == sha:
                new_head_info = old_head_info

        if new_head_info is None:
            now = int(time.time())
            notify_at = HEAD_STALENESS_ALERT_INITIAL_SECS + now
            new_head_info = HeadInfo(
                last_sha=sha,
                first_seen_timestamp=now,
                next_notification_timestamp=notify_at,
            )
        new_state.heads[friendly_name] = new_head_info

        revert_infos.append(
            NewRevertInfo(
                friendly_name=friendly_name,
                sha=sha,
                new_reverts=new_reverts,
            )
        )
    return new_state, revert_infos


def detect_latest_cros_llvm_branch(
    chromeos_path: Path, llvm_config: git_llvm_rev.LLVMConfig, sha: str
) -> str:
    rev = git_llvm_rev.translate_sha_to_rev(llvm_config, sha).number
    result = get_llvm_hash.DetectLatestLLVMBranch(chromeos_path, rev)
    if not result:
        raise ValueError(
            f"No branches in {llvm_config.dir} found for LLVM revision {rev}?"
        )
    return result


def do_cherrypick(
    chromeos_path: Path,
    llvm_config: git_llvm_rev.LLVMConfig,
    upstream_main_branch: str,
    repository: str,
    interesting_shas: List[Tuple[str, str]],
    state: State,
    reviewers: List[str],
    cc: List[str],
) -> State:
    def prettify_sha(sha: str) -> tiny_render.Piece:
        rev = get_llvm_hash.GetVersionFrom(llvm_config.dir, sha)
        return prettify_sha_for_email(sha, rev)

    new_state = State()
    new_state, new_revert_infos = locate_new_reverts_across_shas(
        llvm_config, upstream_main_branch, interesting_shas, state
    )
    llvm_config_dir = Path(llvm_config.dir)

    for revert_info in new_revert_infos:
        logging.info(
            "Applying new reverts across %s...", revert_info.friendly_name
        )
        branch = detect_latest_cros_llvm_branch(
            chromeos_path, llvm_config, revert_info.sha
        )
        # The branch will come in the form 'cros/chromeos/llvm-r${N}-${M}`.
        # `cros` is the remote
        assert branch.startswith(
            "cros/"
        ), f"Expected {branch} to start with 'cros/'"
        branch_without_remote = branch[len("cros/") :]
        branch_head = git_utils.resolve_ref(llvm_config_dir, branch)
        with git_utils.create_worktree(
            llvm_config_dir, commitish=branch_head
        ) as worktree:
            # Note that it's possible that we see the same SHA in multiple
            # iterations of this loop. Since we're committing to separate
            # branches, we need to upload separate patches.
            for sha, reverted_sha in revert_info.new_reverts:
                # Always `checkout` the branch's original HEAD, so we don't
                # create a patch stack on Gerrit. Often the reviewer will want
                # to keep/drop certain patches; stacking them adds complexity
                # for questionable benefit.
                git_utils.discard_changes_and_checkout(worktree, branch_head)
                _upload_revert_cherry_pick(
                    sha=sha,
                    branch_without_remote=branch_without_remote,
                    reverted_sha=reverted_sha,
                    llvm_config=llvm_config,
                    llvm_worktree=worktree,
                    reviewers=reviewers,
                    cc=cc,
                )
    maybe_email_about_stale_heads(
        new_state,
        repository,
        recipients=_EmailRecipients(
            well_known=[],
            direct=reviewers + cc,
        ),
        prettify_sha=prettify_sha,
        is_dry_run=False,
    )
    return new_state


def _append_footers_to_commit_message(
    message: str, footers: Iterable[str]
) -> str:
    lines = message.rstrip().splitlines()
    footer_key_re = re.compile(r"^\S+:")

    footer_block = []
    nonfooter_block = lines

    # Parse out existing footers. Footers may/may not exist in a previous
    # commit. If they do, they all exist in the last paragraph of a commit
    # message, and they all match `footer_key_re`.
    for i, line in reversed(list(enumerate(lines))):
        if not line:
            nonfooter_block = lines[:i]
            footer_block = lines[i + 1 :]
            break

        # If this line isn't a valid footer line, the paragraph we're in isn't
        # a series of footers.
        if not footer_key_re.search(line):
            break

    footer_block += footers
    # Add `[""]` to ensure that there's a line separating the nonfooters from
    # the paragraph of footers.
    return "\n".join(nonfooter_block + [""] + footer_block)


def _upload_revert_cherry_pick(
    sha: str,
    branch_without_remote: str,
    reverted_sha: str,
    llvm_config: git_llvm_rev.LLVMConfig,
    llvm_worktree: Path,
    reviewers: List[str],
    cc: List[str],
):
    """Mockable helper to create and upload patches."""
    cherry_pick_returncode = subprocess.run(
        ["git", "cherry-pick", sha],
        check=False,
        cwd=llvm_worktree,
        stdin=subprocess.DEVNULL,
    ).returncode
    # If a cherry-pick fails, it could be for one of two reasons:
    #   1. It's empty.
    #   2. It's a merge conflict.
    # In the former case, the cherry-pick is a nop that should be ignored,
    # since we've already picked it (or equivalent) somehow. In the latter, we
    # still want to bring it to the mage's attention, so upload it with the
    # merge-conflict markers baked in. If the mage cares, they can fix it up
    # and land it.
    if cherry_pick_returncode:
        if not git_utils.has_discardable_changes(llvm_worktree):
            logging.warning(
                "Cherry-pick of SHA %s would be empty; skipping upload", sha
            )
            return

        logging.error(
            "Cherry-pick failed. Still uploading, but with highlights."
        )
        subprocess.run(
            ["git", "add", "."],
            check=True,
            cwd=llvm_worktree,
            stdin=subprocess.DEVNULL,
        )
        subprocess.run(
            ["git", "cherry-pick", "--continue"],
            check=True,
            cwd=llvm_worktree,
            stdin=subprocess.DEVNULL,
        )
        is_cl_a_merge_conflict = True
    else:
        is_cl_a_merge_conflict = False

    footer_lines = patch_utils.generate_chromiumos_llvm_footer(
        is_cherry=True,
        apply_from=git_llvm_rev.translate_sha_to_rev(
            llvm_config, reverted_sha
        ).number,
        apply_until=git_llvm_rev.translate_sha_to_rev(llvm_config, sha).number,
        original_sha=sha,
        platforms=("chromiumos",),
        info=None,
    )
    commit_message = subprocess.run(
        ["git", "log", "-n1", "--format=%B", sha],
        check=True,
        cwd=llvm_worktree,
        encoding="utf-8",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
    ).stdout

    new_commit_message = _append_footers_to_commit_message(
        commit_message, footer_lines
    )
    if is_cl_a_merge_conflict:
        new_commit_message = f"MERGE CONFLICT: {new_commit_message}"

    subprocess.run(
        ["git", "commit", "--amend", "-m", new_commit_message],
        check=True,
        cwd=llvm_worktree,
        stdin=subprocess.DEVNULL,
    )
    cl_head = git_utils.resolve_ref(git_dir=llvm_worktree, ref="HEAD")
    logging.info("Successfully cherry-picked %s as %s", sha, cl_head)
    cls = git_utils.upload_to_gerrit(
        llvm_worktree,
        ref=cl_head,
        remote=git_utils.CROS_EXTERNAL_REMOTE,
        branch=branch_without_remote,
        reviewers=reviewers,
        cc=cc,
    )
    if is_cl_a_merge_conflict:
        # Set V-1 for more visibility.
        for cl in cls:
            try:
                git_utils.set_gerrit_label(
                    cwd=Path(llvm_config.dir),
                    cl_id=cl,
                    label_name=git_utils.GERRIT_LABEL_VERIFIED,
                    label_value="-1",
                )
            except subprocess.CalledProcessError:
                logging.warning("Failed to set V-1 on CL %d; ignoring", cl)


def prettify_sha_for_email(
    sha: str,
    rev: int,
) -> tiny_render.Piece:
    """Returns a piece of an email representing the given sha and its rev."""
    # 12 is arbitrary, but should be unambiguous enough.
    short_sha = sha[:12]
    return tiny_render.Switch(
        text=f"r{rev} ({short_sha})",
        html=tiny_render.Link(
            href=f"https://github.com/llvm/llvm-project/commit/{sha}",
            inner=f"r{rev}",
        ),
    )


def maybe_email_about_stale_heads(
    new_state: State,
    repository_name: str,
    recipients: _EmailRecipients,
    prettify_sha: Callable[[str], tiny_render.Piece],
    is_dry_run: bool,
) -> bool:
    """Potentially send an email about stale HEADs in `new_state`.

    These emails are sent to notify users of the current HEADs detected by this
    script. They:
    - aren't meant to hurry LLVM rolls along,
    - are worded to avoid the implication that an LLVM roll is taking an
      excessive amount of time, and
    - are initially sent at the 2 month point of seeing the same HEAD.

    We've had multiple instances in the past of upstream changes (e.g., moving
    to other git branches or repos) leading to this revert checker silently
    checking a very old HEAD for months. The intent is to send emails when the
    correctness of the HEADs we're working with _might_ be wrong.
    """
    logging.info("Checking HEAD freshness...")
    now = int(time.time())
    stale = sorted(
        (name, info)
        for name, info in new_state.heads.items()
        if info.next_notification_timestamp <= now
    )
    if not stale:
        logging.info("All HEADs are fresh-enough; no need to send an email.")
        return False

    stale_listings = []

    for name, info in stale:
        days = (now - info.first_seen_timestamp) // ONE_DAY_SECS
        pretty_rev = prettify_sha(info.last_sha)
        stale_listings.append(
            f"{name} at {pretty_rev}, which was last updated ~{days} days ago."
        )

    shas_are = "SHAs are" if len(stale_listings) > 1 else "SHA is"
    email_body = [
        "Hi! This is a friendly notification that the current upstream LLVM "
        f"{shas_are} being tracked by the LLVM revert checker:",
        tiny_render.UnorderedList(stale_listings),
        tiny_render.line_break,
        "If that's still correct, great! If it looks wrong, the revert "
        "checker's SHA autodetection may need an update. Please file a bug "
        "at go/crostc-bug if an update is needed. Thanks!",
    ]

    email = _Email(
        subject=f"[revert-checker/{repository_name}] Tracked branch update",
        body=email_body,
    )
    if is_dry_run:
        logging.info("Dry-run specified; would otherwise send email %s", email)
    else:
        _send_revert_email(recipients, email)

    next_notification = now + HEAD_STALENESS_ALERT_INTERVAL_SECS
    for _, info in stale:
        info.next_notification_timestamp = next_notification
    return True


def do_email(
    is_dry_run: bool,
    llvm_config: git_llvm_rev.LLVMConfig,
    upstream_main_branch: str,
    repository: str,
    interesting_shas: List[Tuple[str, str]],
    state: State,
    recipients: _EmailRecipients,
) -> State:
    def prettify_sha(sha: str) -> tiny_render.Piece:
        rev = get_llvm_hash.GetVersionFrom(llvm_config.dir, sha)
        return prettify_sha_for_email(sha, rev)

    def get_sha_description(sha: str) -> tiny_render.Piece:
        return subprocess.check_output(
            ["git", "log", "-n1", "--format=%s", sha],
            cwd=llvm_config.dir,
            encoding="utf-8",
        ).strip()

    new_state, new_reverts = locate_new_reverts_across_shas(
        llvm_config, upstream_main_branch, interesting_shas, state
    )

    for revert_info in new_reverts:
        email = _generate_revert_email(
            repository,
            revert_info.friendly_name,
            revert_info.sha,
            prettify_sha,
            get_sha_description,
            revert_info.new_reverts,
        )
        if is_dry_run:
            logging.info(
                "Would send email:\nSubject: %s\nBody:\n%s\n",
                email.subject,
                tiny_render.render_text_pieces(email.body),
            )
        else:
            logging.info("Sending email with subject %r...", email.subject)
            _send_revert_email(recipients, email)
            logging.info("Email sent.")

    maybe_email_about_stale_heads(
        new_state, repository, recipients, prettify_sha, is_dry_run
    )
    return new_state


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "action",
        choices=["cherry-pick", "email", "dry-run"],
        help="Automatically cherry-pick upstream reverts, send an email, or "
        "write to stdout.",
    )
    parser.add_argument(
        "--state_file", required=True, help="File to store persistent state in."
    )
    parser.add_argument(
        "--llvm_dir", required=True, help="Up-to-date LLVM directory to use."
    )
    parser.add_argument("--debug", action="store_true")
    parser.add_argument(
        "--reviewers",
        type=str,
        nargs="*",
        help="""
        Requests reviews from REVIEWERS. All REVIEWERS must have existing
        accounts.
        """,
    )
    parser.add_argument(
        "--cc",
        type=str,
        nargs="*",
        help="""
        CCs the CL or email to the recipients. If in cherry-pick mode, all
        recipients must have Gerrit accounts.
        """,
    )

    subparsers = parser.add_subparsers(dest="repository")
    subparsers.required = True

    chromeos_subparser = subparsers.add_parser("chromeos")
    chromeos_subparser.add_argument(
        "--chromeos_dir",
        required=True,
        type=Path,
        help="Up-to-date CrOS directory to use.",
    )

    android_subparser = subparsers.add_parser("android")
    android_subparser.add_argument(
        "--android_llvm_toolchain_dir",
        required=True,
        help="Up-to-date android-llvm-toolchain directory to use.",
    )

    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    opts = parse_args(argv)

    logging.basicConfig(
        format="%(asctime)s: %(levelname)s: "
        "%(filename)s:%(lineno)d: %(message)s",
        level=logging.DEBUG if opts.debug else logging.INFO,
    )

    action = opts.action
    llvm_dir = opts.llvm_dir
    repository = opts.repository
    state_file = opts.state_file
    reviewers = opts.reviewers if opts.reviewers else []
    cc = opts.cc if opts.cc else []

    if opts.repository == "chromeos":
        chromeos_path = opts.chromeos_dir
        interesting_shas = _find_interesting_chromeos_shas(chromeos_path)
        recipients = _EmailRecipients(well_known=["mage"], direct=cc)
        llvm_config = git_llvm_rev.LLVMConfig(
            remote=git_utils.CROS_EXTERNAL_REMOTE,
            dir=llvm_dir,
        )
        upstream_main_branch = "upstream/main"
    elif opts.repository == "android":
        interesting_shas = _find_interesting_android_shas(
            opts.android_llvm_toolchain_dir
        )
        recipients = _EmailRecipients(
            well_known=[],
            direct=["android-llvm-dev@google.com"] + cc,
        )
        llvm_config = git_llvm_rev.LLVMConfig(
            remote="origin",
            dir=llvm_dir,
        )
        upstream_main_branch = git_llvm_rev.MAIN_BRANCH
        # Set this to placate linting bits. Shouldn't be used by
        # `opts.repository == "android"` code.
        chromeos_path = Path()
    else:
        raise ValueError(f"Unknown repository {opts.repository}")

    logging.info("Interesting SHAs were %r", interesting_shas)

    state = _read_state(state_file)
    logging.info("Loaded state\n%s", pprint.pformat(state))

    # We want to be as free of obvious side-effects as possible in case
    # something above breaks. Hence, action as late as possible.
    if action == "cherry-pick":
        if repository != "chromeos":
            raise RuntimeError(
                "only chromeos supports automatic cherry-picking."
            )

        new_state = do_cherrypick(
            chromeos_path=chromeos_path,
            llvm_config=llvm_config,
            upstream_main_branch=upstream_main_branch,
            repository=repository,
            interesting_shas=interesting_shas,
            state=state,
            reviewers=reviewers,
            cc=cc,
        )
    else:
        new_state = do_email(
            is_dry_run=action == "dry-run",
            llvm_config=llvm_config,
            upstream_main_branch=upstream_main_branch,
            interesting_shas=interesting_shas,
            repository=repository,
            state=state,
            recipients=recipients,
        )

    _write_state(state_file, new_state)
    return 0
