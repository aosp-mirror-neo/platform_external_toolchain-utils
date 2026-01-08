# venv_tc/

toolchain-utils uses a virtual environment to manage its dependencies. This is
because `toolchain-utils` needs to run in three separate environments:

1. Within ChromeOS' chroot.
2. Within ChromeOS' tree, but outside of the chroot.
3. Outside of ChromeOS' tree.

Code in this directory manages the venv.

## venv management

Virtual environments are created on demand. There are up to two separate ones
created: one for inside of the chroot, and one for outside.

Virtual environments are recreated if their 'stamp' file changes. For full
details, see `./venv_python3.sh`. At a high level, the stamp file is meant
to represent the packages installed within the venv (so just includes
requirements.txt at the time of writing).

## Venv usage

`./venv_python3.sh foo.py --bar baz` is the recommended way to directly run
something in the venv. This will ensure the venv is established and up-to-date,
and will execute your program under the venv.

## `toolchain-utils` wrapper usage

`toolchain-utils` has `py/bin/...`, which is a tree of symlinks that make
importing `toolchain-utils`' modules easier. Those all point to
`venv_python3_wrapper.sh`, which ensures the venv is set up, and invokes
`../venv_python3_wrapper.py` in the virtual environment.

## Dependency maintenance

`wheels.py` is the one-stop shop for wheel synchronization, checksumming, etc.
It updates and consults `wheel-manifest.json`, which contains SHA512 sums of
all wheels that are currently needed for the venv.

### How do I add/update/remove a dependency?

See go/crostc-venv-updates.

## Why `venv_tc/`?

The `venv` and `virtualenv` module names conflict with Python packages that are
likely to be installed, which leads to confusion when e.g., running tests. The
`_tc` helps disambiguate.
