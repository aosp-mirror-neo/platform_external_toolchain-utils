#!/bin/bash -eu
# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
#
# Instructs Gemini to analyze and then try to fix rustsec issues in your
# current tree.
#
# It creates a commit for each `cargo update`, and will enter interactive mode
# when it's done.
#
# Gemini gets run inside of the src/third_party/rust_crates directory.

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage: gemini_try_fix_rustsec.sh [flags...]

Analyzes and attempts to fix rustsec issues in the current tree using Gemini.
This script invokes Gemini inside of the src/third_party/rust_crates directory
with a prompt specialized for rustsec fixes.

Any flags passed to this script will be forwarded to the `gemini` command.
EOF
  exit 0
fi

prompt=$(cat <<'EOF'
# Fixing Rust Advisories

This is the ChromeOS `rust_crates` directory, which contains all third-party
rust crates. Your job is to identify if any have active rustsec advisories,
and fix them.

## Identifying advisories

To identify any active advisories, run `scripts/cargo-audit.py`. Note that this
will log some ignored crates; pay no attention to them. Instead, look for fatal
advisories. For example,

```
** Fatal advisories found:
  - crate "rand" version "0.7.3" is unsound
```

## Fixing advisories

For each advisory, your flow should look as follows. If a step fails, use
`git checkout -- .` to reset to HEAD, and move on to the next advisory.
**Please** be sure to note the failure in your summary.

1. Attempt to update the crate. This should be done via `cargo update`.
   **Always** do targeted updates, like so:

```bash
$ cd projects && cargo update -p rand
```

2. Run `./vendor.py`. Note that this regenerates all crates, and patches may
   fail to apply. The `vendor.py` script has more infomration on how patches
   are identified and applied.
3. If the above is successful, run `git add .` and create a commit. The message
   of the commit should look like:

"""
cargo-update PACKAGE_NAME_VERSION_WITH_ADVISORY

This updates PACKAGE_NAME_WITH_ADVISORY, which DETAILS_ABOUT_ADVISORY.

BUG=FIXME
TEST=CQ+1
"""

### Commit message examples

"""
cargo-update time-0.3.40

This updates time, which was impacted by
https://rustsec.org/advisories/RUSTSEC-2026-0009.html.

BUG=FIXME
TEST=CQ+1
"""

Another example for an advisory without a RUSTSEC issue (e.g., `unsound`).

"""
cargo-update rand-0.7.3

This updates rand, which was reported by rustsec as unsound.

BUG=FIXME
TEST=CQ+1
"""

## Completion

When you are out of crates with advisories, summarize your work.

Example summary:

```
Updates complete!

- time-0.3.40: I made commit abcdef12345, upgrading to 0.3.44.
- anyhow-1.30.0: I tried updating, but cargo-audit.py still had errors. No
  semver-compatible version seems to have a fix.

With these changes, the RustSec advisories for time-0.3.40 are cleared.

Please remember: as the user, it's **your responsibility** to verify this work.
Generally, RustSec's website shows semver ranges of impacted crates. Any
unsuccessful upgrades also need action from you to resolve.
```
EOF
)

my_dir="$(dirname "$(readlink -m "$0")")"
rust_crates="${my_dir}/../../rust_crates"
cd "${rust_crates}"

if ! command -v gemini >/dev/null 2>&1; then
  echo "Error: 'gemini' command not found on \$PATH." >&2
  echo "Please ensure the Gemini CLI is installed and available." >&2
  exit 1
fi

exec gemini -i "${prompt}" "$@"
