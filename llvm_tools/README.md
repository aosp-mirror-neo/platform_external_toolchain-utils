# LLVM Tools

## Overview

These scripts helps automate tasks such as updating the LLVM next hash,
determing whether a new patch applies correctly, and patch management.

In addition, there are scripts that automate the process of retrieving the
git hash of LLVM from either google3, top of trunk, or for a specific SVN
version.

**NOTE: All scripts must must be run outside the chroot**

**NOTE: sudo must be permissive (i.e. **`cros_sdk`** should NOT prompt for a
password)**

## `update_packages_and_run_tests.py`

### Usage

This script is used for updating a package's LLVM hash (sys-devel/llvm,
sys-libs/compiler-rt, sys-libs/libcxx, and sys-libs/llvm-libunwind)
and then run tests after updating the git hash. There are three ways to test
the change, including starting tryjobs, recipe builders or using cq+1.

An example when this script should be run is when certain boards would like
to be tested with the updated `LLVM_NEXT_HASH`.

For example:

```
$ ./update_packages_and_run_tests.py \
  --is_llvm_next \
  --llvm_version tot \
  tryjobs \
  --options nochromesdk latest-toolchain \
  --builders kevin-release-tryjob nocturne-release-tryjob
```

The above example would update the packages' `LLVM_NEXT_HASH` to the top of
trunk's git hash and would submit tryjobs for kevin and nocturne boards, passing
in 'nochromesdk' and 'latest-toolchain' for each tryjob.

For help with the command line arguments of the script, run:

```
$ ./update_packages_and_run_tests.py --help
```

Similarly as the previous example, but for updating `LLVM_HASH` to
google3 and test with cq+1:

```
$ ./update_packages_and_run_tests.py \
  --llvm_version google3 \
  cq
```

Similarly as the previous example, but for updating `LLVM_NEXT_HASH` to
the git hash of revision 367622 and test with recipe builders:

```
$ ./update_packages_and_run_tests.py \
  --is_llvm_next \
  --llvm_version 367622 \
  recipe \
  --options -nocanary \
  --builders chromeos/toolchain/kevin-llvm chromeos/toolchain/nocturne-llvm
```

## `update_chromeos_llvm_hash.py`

### Usage

This script is used for updating a package's/packages' LLVM hashes and
creating a change list of those changes which will uploaded for review. For
example, some changes that would be included in the change list are
the updated ebuilds, changes made to the patches of the updated packages such
as being removed or an updated patch metadata file. These changes are determined
by the `--failure_mode` option.

An example where this script would be used is when multiple packages need to
have their `LLVM_NEXT_HASH` updated.

For example:

```
$ ./update_chromeos_llvm_hash.py \
  --update_packages sys-devel/llvm sys-libs/compiler-rt \
  --is_llvm_next \
  --llvm_version google3 \
  --failure_mode disable_patches
```

The example above would update sys-devel/llvm and sys-libs/compiler-rt's
`LLVM_NEXT_HASH` to the latest google3's git hash of LLVM. And the change list
may include patches that were disabled for either sys-devel/llvm or
sys-libs/compiler-rt.

For help with the command line arguments of the script, run:

```
$ ./update_chromeos_llvm_hash.py --help
```

For example, to update `LLVM_HASH` to top of trunk of LLVM:

```
$ ./update_chromeos_llvm_hash.py \
  --update_packages sys-devel/llvm sys-libs/compiler-rt \
  --llvm_version tot \
  --failure_mode disable_patches
```

For example, to create a roll CL to the git hash of revision 367622:

```
$ ./update_chromeos_llvm_hash.py \
  --update_packages sys-devel/llvm sys-libs/compiler-rt \
  sys-libs/libcxx sys-libs/llvm-libunwind \
  'dev-util/lldb-server' \
  --llvm_version 367622 \
  --failure_mode disable_patches
```

## `patch_manager.py`

### Usage

This script is used when when all the command line arguments are known such as
testing a specific metadata file or a specific source tree.

For help with the command line arguments of the script, run:

```
$ ./patch_manager.py --help
```

For example, to see all the failed (if any) patches:

```
$ ./patch_manager.py \
  --svn_version 367622 \
  --patch_metadata_file /abs/path/to/patch/file \
  --src_path /abs/path/to/src/tree \
  --failure_mode continue
```

For example, to disable all patches that failed to apply:

```
$ ./patch_manager.py \
  --svn_version 367622 \
  --patch_metadata_file /abs/path/to/patch/file \
  --src_path /abs/path/to/src/tree \
  --failure_mode disable_patches
```

For example, to bisect a failing patch and stop at the first bisected patch:

```
$ ./patch_manager.py \
  --svn_version 367622 \
  --patch_metadata_file /abs/path/to/patch/file \
  --src_path /abs/path/to/src/tree \
  --failure_mode bisect_patches \
  --good_svn_version 365631
```

For example, to bisect a failing patch and then continue bisecting the rest of
the failed patches:

```
$ ./patch_manager.py \
  --svn_version 367622 \
  --patch_metadata_file /abs/path/to/patch/file \
  --src_path /abs/path/to/src/tree \
  --failure_mode bisect_patches \
  --good_svn_version 365631 \
  --continue_bisection True
```

## LLVM Bisection

### `llvm_simple_bisect.py`

TODO(ryanbeltran): Please write some docs here.

## Other Helpful Scripts

### `get_llvm_hash.py`

#### Usage

The script has a class that deals with retrieving either the top of trunk git
hash of LLVM, the git hash of google3, or a specific git hash of a SVN version.
It also has other functions when dealing with a git hash of LLVM.

In addition, it has a function to retrieve the latest google3 LLVM version.

For example, to retrieve the top of trunk git hash of LLVM:

```
from get_llvm_hash import LLVMHash

LLVMHash().GetTopOfTrunkGitHash()
```

For example, to retrieve the git hash of google3:

```
from get_llvm_hash import LLVMHash

LLVMHash().GetGoogle3LLVMHash()
```

For example, to retrieve the git hash of a specific SVN version:

```
from get_llvm_hash import LLVMHash

LLVMHash().GetLLVMHash(<svn_version>)
```

For example, to retrieve the latest google3 LLVM version:

```
from get_llvm_hash import GetGoogle3LLVMVersion

GetGoogle3LLVMVersion(stable=True)
```

### `git_llvm_rev.py`

This script is meant to synthesize LLVM revision numbers, and translate between
these synthesized numbers and git SHAs. Usage should be straightforward:

```
~> ./git_llvm_rev.py --llvm_dir llvm-project-copy/ --rev r380000
6f635f90929da9545dd696071a829a1a42f84b30
~> ./git_llvm_rev.py --llvm_dir llvm-project-copy/ --sha 6f635f90929da9545dd696071a829a1a42f84b30
r380000
~> ./git_llvm_rev.py --llvm_dir llvm-project-copy/ --sha origin/some-branch
r387778
```

**Tip**: if you put a symlink called `git-llvm-rev` to this script somewhere on
your `$PATH`, you can also use it as `git llvm-rev`.

### `get_patch.py`

#### Usage

This script updates the proper ChromeOS packages with LLVM patches of your
choosing, and copies the patches into patch folders of the packages. This tool
supports both git hash of commits as well as differential reviews.

Usage:

```
get_patch.py --start-ref="HEAD" 47413bb27 p:74791
```

It tries to autodetect a lot of things. For more information, please see the
`--help`. This only pulls down the patches into the current tree, commits and
uploads must be done manually.

### `revert_checker.py`

**This script is copied from upstream LLVM. Please prefer to make upstream edits,
rather than modifying this script. It's kept in a CrOS repo so we don't need an
LLVM tree to `import` this from scripts here.**

This script reports reverts which happen 'across' a certain LLVM commit.

To clarify the meaning of 'across' with an example, if we had the following
commit history (where `a -> b` notes that `b` is a direct child of `a`):

123abc -> 223abc -> 323abc -> 423abc -> 523abc

And where 423abc is a revert of 223abc, this revert is considered to be 'across'
323abc. More generally, a revert A of a parent commit B is considered to be
'across' a commit C if C is a parent of A and B is a parent of C.

Usage example:

```
./revert_checker.py -C llvm-project-copy 123abc 223abc 323abc
```

In the above example, the tool will scan all commits between 123abc and 223abc,
and all commits between 123abc and 323abc for reverts of commits which are
parents of 123abc.

### `nightly_revert_checker.py`

This is an automated wrapper around `revert_checker.py`. It checks to see if any
new reverts happened across toolchains that we're trying to ship since it was
last run. If so, it either automatically cherry-picks the reverts, or sends
emails to appropriate groups.

Usage example for cherry-picking:
```
PYTHONPATH=../ ./nightly_revert_checker.py \
  cherry-pick
  --state_file state.json \
  --llvm_dir llvm-project-copy \
  --chromeos_dir ../../../../
  --reviewers=chromium-os-mage@google.com
```

Usage example for email:
```
PYTHONPATH=../ ./nightly_revert_checker.py \
  email
  --state_file state.json \
  --llvm_dir llvm-project-copy \
  --chromeos_dir ../../../../
```

### `bisect_clang_crashes.py`

This script downloads clang crash diagnoses from
gs://chromeos-toolchain-artifacts/clang-crash-diagnoses and sends them to 4c for
bisection.

Usage example:

```
$ ./bisect_clang_crashes.py --4c 4c-cli --state_file ./output/state.json
```

The above command downloads the artifacts of clang crash diagnoses and send them
to 4c server for bisection. The summary of submitted jobs will be saved in
output/state.json under the current path. The output directory will be created
automatically if it does not exist yet. To get more information of the submitted
jobs, please refer to go/4c-cli.

### `upload_lexan_crashes_to_forcey.py`

This script downloads clang crash diagnoses from Lexan's bucket and sends them
to 4c for bisection.

Usage example:

```
$ ./upload_lexan_crashes_to_forcey.py --4c 4c-cli \
    --state_file ./output/state.json
```

The above command downloads the artifacts of clang crash diagnoses and send them
to 4c server for bisection. The summary of submitted jobs will be saved in
output/state.json under the current path. The output directory will be created
automatically if it does not exist yet. To get more information of the submitted
jobs, please refer to go/4c-cli.

Note that it's recommended to 'seed' the state file with a most recent upload
date. This can be done by running this tool *once* with a `--last_date` flag.
This flag has the script override whatever's in the state file (if anything) and
start submitting all crashes uploaded starting at the given day.

### `werror_logs.py`

This tool exists to help devs reason about `-Werror` instances that _would_
break builds, were the `FORCE_DISABLE_WERROR` support in the compiler wrapper
not enabled.

Usage example:

```
$ ./werror_logs.py aggregate \
    --directory=${repo}/out/sdk/tmp/portage/dev-cpp/gtest-1.13.0-r12/cros-artifacts
```

## `fetch_cq_size_diff.py`

This script should be runnable both inside and outside of the chroot.

This script exists to help users fill in the llvm-next testing matrix. It's
capable of comparing the sizes of ChromeOS images, and the size of Chrome's
debuginfo. An example of this is:

```
$ ./fetch_cq_size_diff.py --image gs \
  gs://chromeos-image-archive/asurada-release/R122-15712.0.0/image.zip
  gs://chromeos-image-archive/asurada-cq/R122-15712.0.0-92036-8761629109681962289/image.zip
```

For convenience, this script can also figure out what to compare from a CL, like
so:

```
$ ./fetch_cq_size_diff.py --image cl \
  https://chromium-review.googlesource.com/c/chromiumos/overlays/board-overlays/+/5126116/3
```

In the above case, this script will find a completed CQ build associated with
PatchSet 3 of the given CL, and compare the `image.zip` generated by said build
with the image.zip generated by a release builder for the same board. CQ
attempts don't have to be entirely green for this; as long as there're a few
green boards to pick from, this script should be able to make a comparison.
