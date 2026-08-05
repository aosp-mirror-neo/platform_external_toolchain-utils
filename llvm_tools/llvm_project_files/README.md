This dir contains files that are symlinked into llvm-project by
`../ready_llvm_branch.py`.

This should **only** contain metadata about working _in general_ with LLVM,
like PRESUBMIT.cfg, GEMINI.md, etc.

The files here are symlinked in so we needn't land commits on LLVM branches for
every small change.
