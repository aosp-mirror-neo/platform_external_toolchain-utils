// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

package main

func warningSuppressionsForLLVM_r563880(packageNameAndCategory string) []string {
	switch packageNameAndCategory {
	// Observed and suppressed on 51 builders during testing.
	// e.g., amd64-generic-cq: https://ci.chromium.org/b/8715339992245735889.
	case "chromeos-base/chromeos-login":
		return []string{"-Wno-unused-private-field"}
	// Observed and suppressed on 51 builders during testing.
	// e.g., amd64-generic-cq: https://ci.chromium.org/b/8715339992245735889.
	case "chromeos-base/diagnostics":
		return []string{"-Wno-unused-private-field"}
	// Observed and suppressed on 3 builders during testing.
	// e.g., fatcat-cq: https://ci.chromium.org/b/8715339982079177585.
	case "chromeos-base/intel-openvino":
		return []string{"-Wno-deprecated-literal-operator"}
	// Observed and suppressed on 4 builders during testing.
	// e.g., betty-fuzzer-cq: https://ci.chromium.org/b/8715339980373199665.
	case "chromeos-base/libchrome":
		return []string{"-Wno-nontrivial-memcall"}
	// Observed and suppressed on 18 builders during testing.
	// e.g., betty-fuzzer-cq: https://ci.chromium.org/b/8715339980373199665.
	case "chromeos-base/modemfwd":
		return []string{"-Wno-unused-private-field"}
	// Observed and suppressed on 7 builders during testing.
	// e.g., brox-cq: https://ci.chromium.org/b/8715339980931440017.
	case "chromeos-base/odml":
		return []string{"-Wno-unused-private-field"}
	// Observed and suppressed on 43 builders during testing.
	// e.g., amd64-generic-cq: https://ci.chromium.org/b/8715339992245735889.
	case "chromeos-base/secagentd":
		return []string{"-Wno-unused-private-field"}
	// Observed and suppressed on 1 builder during testing.
	// e.g., arm-generic-cq: https://ci.chromium.org/b/8715339977521747697.
	case "dev-python/numpy":
		return []string{"-Wno-unsupported-floating-point-opt"}
	// Observed on many builders during testing.
	// e.g., atlas-cq https://cr-buildbucket.appspot.com/build/8714855299967095297
	case "media-libs/dlm":
		return []string{"-Wno-deprecated-literal-operator"}
	// Observed and suppressed on 51 builders during testing.
	// e.g., amd64-generic-cq: https://ci.chromium.org/b/8715339992245735889.
	case "net-analyzer/ndt7-client-cc":
		return []string{"-Wno-deprecated-literal-operator"}
	// Observed and suppressed on 2 builders during testing.
	// e.g., hatch-cq: https://ci.chromium.org/b/8715339988336430497.
	case "sys-boot/intel-cmlfsp":
		return []string{"-Wno-unused-but-set-variable"}
	// Observed and suppressed on 39 builders during testing.
	// e.g., asurada-cq: https://ci.chromium.org/b/8715339979823410017.
	case "sys-cluster/fcp":
		return []string{"-Wno-invalid-specialization"}
	// Observed and suppressed on 1 builder during testing.
	// e.g., arm64-generic-kernel-v5_15-buildtest-cq: https://ci.chromium.org/b/8715339978801687617.
	case "sys-kernel/chromeos-kernel-5_15":
		return []string{"-Wno-frame-larger-than"}
	// Observed and suppressed on 7 builders during testing.
	// e.g., amd64-generic-kernel-v6_1-cq: https://ci.chromium.org/b/8715339976616340401.
	case "sys-kernel/chromeos-kernel-6_1":
		return []string{"-Wno-attribute-warning", "-Wno-frame-larger-than"}
	// Observed and suppressed on 5 builders during testing.
	// e.g., amd64-generic-kernel-v6_12-cq: https://ci.chromium.org/b/8715339976833712113.
	case "sys-kernel/chromeos-kernel-6_12":
		return []string{"-Wno-attribute-warning", "-Wno-frame-larger-than"}
	// Observed and suppressed on 17 builders during testing.
	// e.g., amd64-generic-kernel-v6_6-cq: https://ci.chromium.org/b/8715339977108236625.
	case "sys-kernel/chromeos-kernel-6_6":
		return []string{"-Wno-attribute-warning", "-Wno-frame-larger-than"}
	default:
		return nil
	}
}
