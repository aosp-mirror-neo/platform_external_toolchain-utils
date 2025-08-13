# Here be hacks

This directory exists to work around toolchain-utils' lack of a unified Python
virtual environment story.

It's being set up so experimentation can continue with Gemini (b/436267619)
with minimal wheel-reinventing.

A better long-term solution would be to make toolchain-utils manage virtual
environments properly, so the packages needed by tools here can be managed
in a more unified way.

**That said**, this directory is meant to be in a world independent of the larger module ecosystem of toolchain-utils. Until the `pip` packaging is resolved:
- no `cros_utils`/etc imports from here
- no imports of files here from other `toolchain-utils` files
- the python script(s) here should have their own `main` dispatch code, and should be invoked via `$(./establish_venv.sh)/bin/python ${script_name}`
