This repo is the ChromeOS fork of LLVM's upstream repository.

ChromeOS branches off of `main` about four times per quarter. Each branch
contains a series of fix cherry-picks, and local patches. Patches applied are
generally either sourced from ../toolchain-utils/llvm_patches, or mirrored to
that directory.

For **upstream cherrypicks**, all commits must:
- have metadata embedded in their message (incl the original author),
- and their author must be reset to `crostc-worker <crostc-worker@crostc-chrotomation.iam.gserviceaccount.com>`.

For more information on metadata format, see
`${CROSTC_GOOGLE3_DOCS)/llvm-gcc-gdb-and-other-tools-info/applying-upstream-patch-to-toolchain.md`.
Load the `chromeos-toolchain-pointers` skill if necessary.
