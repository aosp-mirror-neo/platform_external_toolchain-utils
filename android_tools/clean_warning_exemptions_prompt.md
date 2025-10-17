# Instructions

Your task is to simplify the commit that was just made in this repository.

The commit is produced after these instructions. It should consist of changes
to `Android.bp` files, which are Blueprint files that configure Android's
build. These changes add `cflags` to targets that break with one or more
compiler warnings enabled.

# Simplification rules

You should carefully apply the following rules to each file that was changed:

1. If a file only has one target with cflags added, do nothing to that file.
2. If a file has multiple targets with common cflags added, try to identify a
   common `defaults` between at least two of the targets, and move the cflags
   from the targets to the `defaults`.

After editing files, you are done. You are **not** to perform any operations
outside of reading and writing files. You are **not** to attempt to run tools
like `git`.

# Description of Android.bp files

An example file is

```
// A comment explaining the file.
cc_defaults {
    name: "our_main_defaults",
    cflags: ["-Wfoo"],
}

// A comment explaining the following line.
cc_defaults {
    name: "our_sub_defaults",
    defaults: ["our_main_defaults"],
    cflags: ["-Wbar"],
}

cc_library {
    name: "my_library",
    defaults: ["our_sub_defaults"],
    cflags: ["-Wbaz"],
    srcs: ["my_file.cc"],
}

cc_binary {
    name: "my_binary",
    defaults: ["our_main_defaults"],
    cflags: ["-Wqux"],
    srcs: ["main.cc"],
}
```

This file declares two targets: a library called `my_library`, and a binary
called `my_binary`. It declares two defaults, `our_main_defaults` and
`our_sub_defaults`.

`my_library` consists of `my_file.cc`, which is built with
flags `-Wfoo -Wbar -Wbaz`. Note that `my_file.cc` has defaults
`our_sub_defaults`, which 'inherits' settings from its own `defaults`:
`our_main_defaults`. Hence, all flags from `our_main_defaults`,
`our_sub_defaults`, and `my_library` apply to this build.

`my_binary` consists of `main.cc`, which is built with flags `-Wfoo -Wqux`,
because it inherits from `our_main_defaults`.

# Examples

## 1. No changes necessary

Given `-Wno-foo` was added by the HEAD commit in the following file:

```
cc_defaults {
    name: "ex1_main_defaults",
}

cc_library {
    name: "ex1_my_library",
    defaults: ["ex1_sub_defaults"],
    // b/1234: Temporarily suppress this.
    cflags: ["-Wno-foo"],
    srcs: ["my_file.cc"],
}
```

Because only one target received the flag, there is nothing to move.

## 2. Centralize in common default

Given `-Wno-foo` was added by the HEAD commit in the following file:

```
cc_defaults {
    name: "ex2_main_defaults",
}

cc_library {
    name: "ex2_my_library",
    defaults: ["ex2_main_defaults"],
    // b/1234: Temporarily suppress this.
    cflags: ["-Wno-foo"],
    srcs: ["my_file.cc"],
}

cc_library {
    name: "ex2_my_other_library",
    defaults: ["ex2_main_defaults"],
    // b/1234: Temporarily suppress this.
    cflags: ["-Wno-foo"],
    srcs: ["my_other_file.cc"],
}
```

Because two targets contain identical flags and share defaults, the file should
be transformed to:

```
cc_defaults {
    name: "ex2_main_defaults",
    // b/1234: Temporarily suppress this.
    cflags: ["-Wno-foo"],
}

cc_library {
    name: "ex2_my_library",
    defaults: ["ex2_main_defaults"],
    srcs: ["my_file.cc"],
}

cc_library {
    name: "ex2_my_other_library",
    defaults: ["ex2_main_defaults"],
    srcs: ["my_other_file.cc"],
}
```

## 3. Centralize repeated in common default

Given `-Wno-foo` **and** `-Wno-bar` were added by the HEAD commit in the
following file:

```
cc_defaults {
    name: "ex3_main_defaults",
}

cc_library {
    name: "ex3_my_library",
    defaults: ["ex3_main_defaults"],
    cflags: [
        // b/1234: Temporarily suppress this.
        "-Wno-foo",
        // b/1234: Temporarily suppress this.
        "-Wno-bar",
    ],
    srcs: ["my_file.cc"],
}

cc_library {
    name: "ex3_my_other_library",
    defaults: ["ex3_main_defaults"],
    // b/1234: Temporarily suppress this.
    cflags: ["-Wno-foo"],
    srcs: ["my_other_file.cc"],
}
```

Because two targets contain matching flags and share defaults, the matching
flags should be moved into the shared defaults, so the new file should be:

```
cc_defaults {
    name: "ex3_main_defaults",
    // b/1234: Temporarily suppress this.
    cflags: ["-Wno-foo"],
}

cc_library {
    name: "ex3_my_library",
    defaults: ["ex3_main_defaults"],
    srcs: ["my_file.cc"],
    cflags: [
        // b/1234: Temporarily suppress this.
        "-Wno-bar",
    ],
}

cc_library {
    name: "ex3_my_other_library",
    defaults: ["ex3_main_defaults"],
    srcs: ["my_other_file.cc"],
}
```

## 4. Search parent defaults

Given `-Wno-foo` was added by the HEAD commit in the following file:

```
cc_defaults {
    name: "ex4_main_defaults",
}

cc_defaults {
    name: "ex4_sub_defaults",
    defaults: ["ex4_main_defaults"],
    cflags: ["-fstrict-aliasing"],
}

cc_library {
    name: "ex4_my_library",
    defaults: ["ex4_sub_defaults"],
    // b/1234: Temporarily suppress this.
    cflags: ["-Wno-foo"],
    srcs: ["my_file.cc"],
}

cc_library {
    name: "ex4_my_other_library",
    defaults: ["ex4_main_defaults"],
    // b/1234: Temporarily suppress this.
    cflags: ["-Wno-foo"],
    srcs: ["my_other_file.cc"],
}
```

Because `ex4_main_defaults` is **transitively** shared between both targets, the
cflags should be moved into that.

```
cc_defaults {
    name: "ex4_main_defaults",
    // b/1234: Temporarily suppress this.
    cflags: ["-Wno-foo"],
}

cc_defaults {
    name: "ex4_sub_defaults",
    defaults: ["ex4_main_defaults"],
    cflags: ["-fstrict-aliasing"],
}

cc_library {
    name: "ex4_my_library",
    defaults: ["ex4_sub_defaults"],
    srcs: ["my_file.cc"],
}

cc_library {
    name: "ex4_my_other_library",
    defaults: ["ex4_main_defaults"],
    srcs: ["my_other_file.cc"],
}
```

# Commit to examine

The output of `git format-patch HEAD^..HEAD` for this repository is appended below:
