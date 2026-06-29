// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

package main

func warningSuppressionsForLLVM_r584947(packageNameAndCategory string) []string {
	switch packageNameAndCategory {
	// Observed and suppressed on 42 builders during testing.
	// e.g., asurada-cq: https://ci.chromium.org/b/8678797060881034945.
	case "chromeos-base/arc-keymaster":
		return []string{"-Wno-character-conversion"}
	// Observed and suppressed on 42 builders during testing.
	// e.g., asurada-cq: https://ci.chromium.org/b/8678797060881034945.
	case "chromeos-base/arc-keymint":
		return []string{"-Wno-character-conversion"}
	// Observed and suppressed on 22 builders during testing.
	// e.g., betty-arc-r-container-cq: https://ci.chromium.org/b/8678797061031006113.
	case "chromeos-base/chromeos-fpmcu-bloonchipper-unittests":
		return []string{"-Wno-uninitialized-const-pointer"}
	// Observed and suppressed on 22 builders during testing.
	// e.g., betty-arc-r-container-cq: https://ci.chromium.org/b/8678797061031006113.
	case "chromeos-base/chromeos-fpmcu-dartmonkey-unittests":
		return []string{"-Wno-uninitialized-const-pointer"}
	// Observed and suppressed on 1 builder during testing.
	// e.g., staging-build-chromiumos-sdk: https://ci.chromium.org/b/8678324675048799153.
	case "chromeos-base/perfetto":
		return []string{"-Wno-c2y-extensions"}
	// Observed and suppressed on 1 builder during testing.
	// e.g., host-packages-cq: https://ci.chromium.org/b/8678797063835217441.
	case "dev-libs/boringssl":
		return []string{"-Wno-character-conversion"}
	// Observed and suppressed on 1 builder during testing.
	// e.g., betty-msan-fuzzer-cq: https://ci.chromium.org/b/8678797061549902817.
	case "dev-libs/glib":
		return []string{"-Wno-implicit-function-declaration"}
	// Observed and suppressed on 70 builders during testing.
	// e.g., amd64-generic-cq: https://ci.chromium.org/b/8678797067857006097.
	case "sys-apps/coreboot-utils":
		return []string{"-Wno-sometimes-uninitialized"}
	// Observed and suppressed on 11 builders during testing.
	// e.g., amd64-generic-kernel-v5_10-buildtest-cq: https://ci.chromium.org/b/8678797067785398129.
	case "sys-kernel/chromeos-kernel-5_10":
		return []string{"-Wno-sometimes-uninitialized", "-Wno-uninitialized", "-Wno-uninitialized-const-pointer"}
	// Observed and suppressed on 8 builders during testing.
	// e.g., amd64-generic-kernel-v5_15-buildtest-cq: https://ci.chromium.org/b/8678797067697116065.
	case "sys-kernel/chromeos-kernel-5_15":
		return []string{"-Wno-sometimes-uninitialized"}
	// Observed and suppressed on 5 builders during testing.
	// e.g., arm-generic-kernel-v5_4-buildtest-cq: https://ci.chromium.org/b/8678797058829148161.
	case "sys-kernel/chromeos-kernel-5_4":
		return []string{"-Wno-sometimes-uninitialized", "-Wno-uninitialized-const-pointer"}
	// Observed and suppressed on 2 builders during testing.
	// e.g., reven-cq: https://ci.chromium.org/b/8678797065405447633.
	case "sys-kernel/chromeos-kernel-6_12":
		return []string{"-Wno-frame-larger-than"}
	// Observed and suppressed on 11 builders during testing.
	// e.g., amd64-generic-kernel-v6_6-buildtest-cq: https://ci.chromium.org/b/8678797058028545473.
	case "sys-kernel/chromeos-kernel-6_6":
		return []string{"-Wno-frame-larger-than", "-Wno-uninitialized"}
	default:
		return nil
	}
}
