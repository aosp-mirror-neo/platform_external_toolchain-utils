// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

use anyhow::{bail, ensure, Result};
use std::ffi::OsStr;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

pub const WORK_BRANCH_NAME: &str = "__patch_sync_tmp";

#[derive(Debug, Clone)]
pub struct GitUploadOpts<'a> {
    pub git_ref: &'a str,
    pub wip_mode: bool,
    pub topic: Option<String>,
    pub reviewers: &'a [String],
    pub cc: &'a [String],
    pub labels: &'a [String],
    pub extra: &'a [String],
}

#[derive(Debug, Clone)]
pub struct GitContext {
    pub git_root: PathBuf,
    pub main_branch: String,
    pub remote: String,
}

impl GitContext {
    pub fn sync(&self) -> Result<()> {
        ensure!(
            self.git_root.is_dir(),
            "git_root {} is not a directory",
            self.git_root.display()
        );
        git_cd_cmd(&self.git_root, ["fetch", &self.remote, &self.main_branch])?;
        git_cd_cmd(
            &self.git_root,
            [
                "checkout",
                &format!("{}/{}", &self.remote, &self.main_branch),
            ],
        )?;
        Ok(())
    }

    pub fn upload(&self, commit_msg: &str, opts: GitUploadOpts) -> Result<()> {
        git_cd_cmd(&self.git_root, ["switch", "-c", WORK_BRANCH_NAME])?;
        git_cd_cmd(&self.git_root, ["add", "."])?;
        git_cd_cmd(&self.git_root, ["commit", "-m", commit_msg])?;
        let mut upload_options = Vec::new();
        upload_options.extend(opts.reviewers.iter().map(|x| format!("r={}", x)));
        upload_options.extend(opts.cc.iter().map(|x| format!("cc={}", x)));
        upload_options.extend(opts.labels.iter().map(|x| format!("l={}", x)));
        upload_options.extend(opts.extra.iter().cloned());
        if opts.wip_mode {
            upload_options.push("wip".to_string());
        }
        if let Some(topic) = opts.topic {
            upload_options.push(format!("topic={}", topic));
        }
        let trailing_options = if upload_options.is_empty() {
            "".to_string()
        } else {
            format!("%{}", upload_options.join(","))
        };
        git_cd_cmd(
            &self.git_root,
            [
                "push",
                &self.remote,
                &format!(
                    "{}:refs/for/{}{trailing_options}",
                    opts.git_ref, self.main_branch,
                ),
            ],
        )?;
        Ok(())
    }
}

/// Run a given git command from inside a specified git dir.
pub fn git_cd_cmd<I, S>(pwd: &Path, args: I) -> Result<Output>
where
    I: IntoIterator<Item = S>,
    S: AsRef<OsStr>,
{
    let mut command = Command::new("git");
    command.current_dir(pwd).args(args);
    let output = command.output()?;
    if !output.status.success() {
        bail!(
            "git command failed:\n  {:?}\nstdout --\n{}\nstderr --\n{}",
            command,
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr),
        );
    }
    Ok(output)
}

/// Clean up the git repo after we're done with it.
pub fn cleanup_branch(git_path: &Path, base_branch: &str, rm_branch: &str) -> Result<()> {
    git_cd_cmd(git_path, ["restore", "."])?;
    git_cd_cmd(git_path, ["clean", "-fd"])?;
    git_cd_cmd(git_path, ["checkout", base_branch])?;
    // It's acceptable to be able to not delete the branch. This may be
    // because the branch does not exist, which is an expected result.
    // Since this is a very common case, we won't report any failures related
    // to this command failure as it'll pollute the stderr logs.
    let _ = git_cd_cmd(git_path, ["branch", "-D", rm_branch]);
    Ok(())
}
