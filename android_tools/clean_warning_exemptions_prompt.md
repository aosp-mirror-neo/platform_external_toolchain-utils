# Instructions

Your task is to simplify an Android.bp file's modifications that were just made
in this repository.

The diff is produced after these instructions. It should consist of changes to
precisely one `Android.bp` file, which is a Blueprint files that configure
Android's build. These changes add `cflags` to targets that break with one or
more compiler warnings enabled.

# Simplification rules

You should carefully apply the following rules when simplifying:

1. If the file only has one target with `cflags` added, do nothing to that file unless Cross-File Hoisting (Rule 3) applies across the repository.
2. If the file has multiple targets with common `cflags` added, try to identify
   a common `defaults` between at least two of the targets, and move the
   `cflags` from the targets to the `defaults`.
3. **Cross-File Defaults Hoisting Rule**: If a shared `defaults` module cannot be found within the same `Android.bp` file, search parent directories within the **current git repository** for an ancestor `defaults` (such as a repository-wide `<repo>_defaults` or `Android.bp` root default). **CRITICAL**: You MUST only consider `Android.bp` files within the current git repository and MUST NOT traverse into other repositories. Hoist the flag to the cross-file `defaults` if $2^N > M$, where:
   - $N$ = Number of individual target suppressions removed in the current repository.
   - $M$ = Estimated number of unaffected targets in the current repository inheriting that `defaults`.
   If $2^N > M$, move the flag and its associated comment to the shared ancestor `defaults` and remove the individual target `cflags` across the repository.

You are **only** to make changes to `cflags` fields of `Android.bp` targets,
and their related comments. Under no circumstances should you modify fields or
comments that are unrelated to `cflags`.

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

## 5. Centralize in common default, with multiple options

`defaults` lists may contain multiple values. In this case, `cflag` lists
between them become concatenated. They're all equally acceptable candidates to
move flags to, if doing so will allow for deduplication of newly-added flags.

Given the following file:

```
cc_defaults {
    name: "ex5_main_defaults",
}

cc_defaults {
    name: "ex5_extra_defaults",
    cflags: ["-fstrict-aliasing"],
}

cc_library {
    name: "ex5_my_library",
    defaults: ["ex5_main_defaults"],
    srcs: ["my_file.cc"],
    cflags: [
      "-Wno-bar",
      // b/1234: Temporarily suppress this.
      "-Wno-foo",
    ],
}

cc_library {
    name: "ex5_my_other_library",
    defaults: [
      "ex5_extra_defaults",
      "ex5_main_defaults",
    ],
    srcs: ["my_other_file.cc"],
    // b/1234: Temporarily suppress this.
    cflags: ["-Wno-foo"],
}
```

`ex5_main_defaults` is shared between the `cc_library`s with `-Wno-foo`, so
`-Wno-foo` should be moved to `ex5_main_defaults`.

```
cc_defaults {
    name: "ex5_main_defaults",
    // b/1234: Temporarily suppress this.
    cflags: ["-Wno-foo"],
}

cc_defaults {
    name: "ex5_extra_defaults",
    cflags: ["-fstrict-aliasing"],
}

cc_library {
    name: "ex5_my_library",
    defaults: ["ex5_main_defaults"],
    srcs: ["my_file.cc"],
    cflags: [
      "-Wno-bar",
    ],
}

cc_library {
    name: "ex5_my_other_library",
    defaults: [
      "ex5_extra_defaults",
      "ex5_main_defaults",
    ],
    srcs: ["my_other_file.cc"],
}
```

# Commit to examine

The output of `git diff HEAD^..HEAD -- THE_FILE` for this repository is
appended below:
