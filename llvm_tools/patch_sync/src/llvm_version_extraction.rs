// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

use regex::Regex;
use std::fs::File;
use std::io::{BufRead, BufReader};
use std::path::Path;
use std::process::Command;

use anyhow::{bail, ensure, Context, Result};

const LLVM_ANDROID_REL_PATH: &str = "toolchain/llvm_android";
const CROS_TOOLCHAIN_MANIFEST_REL_PATH: &str = "manifest/_toolchain.xml";
const CROS_TC_UTILS_REL_PATH: &str = "src/third_party/toolchain-utils";

/// Return the Android checkout's current llvm version.
///
/// This uses android_version.get_svn_revision_number, a python function
/// that can't be executed directly. We spawn a Python3 program
/// to run it and get the result from that.
pub fn get_android_llvm_version(android_checkout: &Path) -> Result<String> {
    let mut command = new_android_cmd(android_checkout, "python3")?;
    command.args([
        "-c",
        "from src.llvm_android import android_version; \
         print(android_version.get_svn_revision_number(), end='')",
    ]);
    let stdout = check_output("could not get android llvm version", &mut command)?;
    let out_string = String::from_utf8(stdout)?.trim().to_string();
    Ok(out_string)
}

/// Return the ChromiumOS checkout's current llvm version from the manifest.
pub fn get_chromiumos_llvm_version(chromiumos_checkout: &Path) -> Result<String> {
    let toolchain_manifest = chromiumos_checkout.join(CROS_TOOLCHAIN_MANIFEST_REL_PATH);
    let file = File::open(&toolchain_manifest).context("opening toolchain manifest")?;
    let manifest_matcher = Regex::new(r"revision=.*chromeos/llvm-r(\d+)-\d+").unwrap();
    let line_iter = BufReader::new(file).lines();
    for line_res in line_iter {
        match line_res {
            Ok(line) => {
                if let Some(captures) = manifest_matcher.captures(&line) {
                    return Ok(captures.get(1).unwrap().as_str().to_owned());
                }
            }
            Err(x) => bail!(
                "failed to read line from {:?}: {x}",
                toolchain_manifest.to_string_lossy()
            ),
        }
    }
    bail!(
        "could not read chromiumos llvm version from {:?}",
        toolchain_manifest.display()
    );
}

/// Return the ChromiumOS checkout's next llvm version from the llvm_next.py file.
pub fn get_chromiumos_llvm_next_version(chromiumos_checkout: &Path) -> Result<String> {
    let mut command = new_cros_tc_utils_cmd(chromiumos_checkout, "python3")?;
    command.args([
        "-c",
        "from llvm_tools import llvm_next; \
         print(llvm_next.LLVM_NEXT_REV, end='')",
    ]);
    let stdout = check_output("could not get chromiumos llvm next version", &mut command)?;
    let out_string = String::from_utf8(stdout)?.trim().to_string();
    Ok(out_string)
}

/// Sort the Android patches using the cherrypick_cl.py Android utility.
///
/// This assumes that:
///   1. There exists a python script called cherrypick_cl.py
///   2. That calling it with the given arguments sorts the PATCHES.json file.
///   3. Calling it does nothing besides sorting the PATCHES.json file.
///
/// We aren't doing our own sorting because we shouldn't have to update patch_sync along
/// with cherrypick_cl.py any time they change the __lt__ implementation.
pub fn sort_android_patches(android_checkout: &Path) -> Result<()> {
    let mut command = new_android_cmd(android_checkout, "python3")?;
    command.args(["cherrypick_cl.py", "--reason", "patch_sync sorting"]);
    check_output("could not sort", &mut command)?;
    Ok(())
}

fn new_android_cmd(android_checkout: &Path, cmd: &str) -> Result<Command> {
    new_cmd_from_dir(android_checkout, LLVM_ANDROID_REL_PATH, cmd)
}

fn new_cros_tc_utils_cmd(cros_checkout: &Path, cmd: &str) -> Result<Command> {
    new_cmd_from_dir(cros_checkout, CROS_TC_UTILS_REL_PATH, cmd)
}

fn new_cmd_from_dir<P>(dir: &Path, rel_subdir: P, cmd: &str) -> Result<Command>
where
    P: AsRef<Path>,
{
    let mut command = Command::new(cmd);
    let subdir = dir.join(rel_subdir);
    ensure!(
        subdir.is_dir(),
        "can't make command; {:?} is not a directory",
        subdir.display()
    );
    command.current_dir(subdir);
    Ok(command)
}

fn check_output(desc: &str, cmd: &mut Command) -> Result<Vec<u8>> {
    let output = cmd.output()?;
    if !output.status.success() {
        bail!("{}: {:?}", desc, String::from_utf8_lossy(&output.stderr));
    }
    Ok(output.stdout)
}
