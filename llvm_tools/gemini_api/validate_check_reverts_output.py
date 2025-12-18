# Copyright 2025 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Validates check_reverts' output against a golden file on known SHAs."""

import argparse
import collections
import json
import logging
from pathlib import Path
import subprocess
import sys
import tempfile

from cros_utils import cros_paths
from llvm_tools.gemini_api import check_reverts


def run_gemini_on_goldens(
    gemini_api_key: str,
    llvm_dir: Path,
    jobs: int | None,
    stop_after: str | None,
    only_sha: str | None,
) -> tuple[
    dict[str, check_reverts.GeminiRevertInference], tuple[str, ...] | None
]:
    """Runs Gemini on GOLDEN_SHAs, returning the results.

    Args:
        gemini_api_key: API key for Gemini invocations.
        llvm_dir: Up-to-date LLVM directory (with `.git`).
        jobs: Maximum concurrent jobs to execute; defaults to what
          `check_reverts.py` selects.
        stop_after: If specified, GOLDEN_SHAS will be truncated such that
          `stop_after` is the last SHA to test. Mutually exclusive with
          `only_sha`.
        only_sha: If specified, only this SHA will be tested.

    Returns:
        A tuple, containing:
        - A dict of {sha: inference_result}
        - A tuple of SHAs that were tested, or None if all were tested.
    """
    if stop_after and only_sha:
        raise ValueError(
            "Only one of `stop_after` and `only_sha` may be specified."
        )

    shas_to_test = GOLDEN_SHAS
    tested_all_shas = False
    if stop_after:
        try:
            i = shas_to_test.index(stop_after)
        except ValueError:
            raise ValueError(
                "--stop-after value {stop_after} does not exist in GOLDEN_SHAS"
            )
        shas_to_test = shas_to_test[: i + 1]
    elif only_sha:
        if only_sha not in shas_to_test:
            raise ValueError("Unknown SHA to test: {only_sha}")
        shas_to_test = (only_sha,)
    else:
        tested_all_shas = True

    check_reverts_command: list[str | Path] = [
        cros_paths.script_toolchain_utils_root()
        / "py"
        / "bin"
        / "llvm_tools"
        / "gemini_api"
        / "check_reverts.py",
        f"--gemini-api-key={gemini_api_key}",
        f"--llvm-dir={llvm_dir}",
    ]

    if jobs:
        check_reverts_command.append(f"--jobs={jobs}")

    logging.info("Comparing goldens for %d SHAs", len(shas_to_test))
    with tempfile.NamedTemporaryFile(
        prefix="gemini_revert_checker_"
    ) as raw_tempfile:
        # To allow for a range of reasonable implementations of the child
        # script, the tempfile is just treated as a path.
        #
        # NOTE: `close()`ing the tempfile fd deletes the tempfile.
        # `delete_on_close=False` requires py3.12, which is too new.
        output_file = Path(raw_tempfile.name)

        check_reverts_command += (
            "-o",
            output_file,
        )
        subprocess.run(
            check_reverts_command,
            check=True,
            input="\n".join(shas_to_test),
            encoding="utf-8",
        )

        results = {}
        with output_file.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                single_result = json.loads(line)
                sha = single_result["sha"]
                gemini_result = (
                    check_reverts.GeminiRevertInference.from_json_checked(
                        single_result["result"]
                    )
                )
                results[sha] = gemini_result
    maybe_shas_tested = None if tested_all_shas else shas_to_test
    if len(results) != len(shas_to_test):
        raise ValueError(
            f"Expected {len(shas_to_test)} results; got {len(results)}"
        )
    return results, maybe_shas_tested


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--gemini-api-key",
        required=True,
        help="Gemini API key.",
    )
    parser.add_argument(
        "-j",
        "--jobs",
        type=int,
        help="Number of jobs to pass to the Gemini checker script.",
    )
    parser.add_argument(
        "--llvm-dir",
        type=Path,
        help="Path to LLVM git dir root.",
        required=True,
    )
    parser.add_argument(
        "--update-golden",
        action="store_true",
        help="""
        If passed, the golden file will be updated instead of checked. 'Updated'
        means two things. First, any golden file entries that don't correspond
        to a SHA in GOLDEN_SHAS is removed. Second, any results from Gemini that
        aren't in the existing golden file are appended (that is, no entries in
        the golden response lists are _replaced_).
        """,
    )

    exclusive_group = parser.add_mutually_exclusive_group()
    exclusive_group.add_argument(
        "--stop-after",
        help="""
        Stop testing after processing the given SHA. If specified, this script
        will test everything in GOLDEN_SHAS before (& including) this SHA. This
        is meant to aid in iterative testing.
        """,
    )
    exclusive_group.add_argument(
        "--only-sha",
        help="If passed, only this SHA will be checked.",
    )
    return parser.parse_args(argv)


def _write_results_to_file(
    results: dict[str, list[check_reverts.GeminiRevertInference]],
    output_path: Path,
) -> None:
    """Writes results to a JSON file.

    This is used for writing golden files.
    """
    with output_path.open("w", encoding="utf-8") as f:
        dump_results = {k: [x.to_json() for x in v] for k, v in results.items()}
        json.dump(
            dump_results, f, sort_keys=True, separators=(",", ": "), indent=2
        )


def main(argv: list[str]) -> None:
    # This is essentially an assertion, so just do it super early in main.
    _verify_no_duplicate_golden_shas()

    my_dir = Path(__file__).resolve().parent
    golden_json = my_dir / "validate_check_reverts_output_golden.json"
    opts = parse_args(argv)
    logging.basicConfig(
        format=">> %(asctime)s: %(levelname)s: %(filename)s:%(lineno)d: "
        "%(message)s",
        level=logging.DEBUG if opts.debug else logging.INFO,
    )

    actual_results, maybe_tested_shas = run_gemini_on_goldens(
        opts.gemini_api_key,
        opts.llvm_dir,
        opts.jobs,
        opts.stop_after,
        opts.only_sha,
    )

    try:
        with golden_json.open(encoding="utf-8") as f:
            compare_against = {
                sha: [
                    check_reverts.GeminiRevertInference.from_json_checked(x)
                    for x in v
                ]
                for sha, v in json.load(f).items()
            }
    except FileNotFoundError:
        compare_against = {}

    if opts.update_golden:
        golden_shas_set = set(GOLDEN_SHAS)
        results_to_write = {
            sha: v
            for sha, v in compare_against.items()
            if sha in golden_shas_set
        }
        for k, v in actual_results.items():
            if cmp := results_to_write.get(k):
                if v not in cmp:
                    cmp.append(v)
            else:
                results_to_write[k] = [v]

        _write_results_to_file(results_to_write, golden_json)
        logging.info("Golden file successfully written to %s", golden_json)
        return

    if maybe_tested_shas is not None:
        # If we're just testing a subset, restrict this map to the subset.
        compare_against = {x: compare_against[x] for x in maybe_tested_shas}

    had_errors = False

    def log_golden_error(*args):
        nonlocal had_errors
        had_errors = True
        logging.error(*args)

    # Since the golden file is a map of SHA to a _list_ of acceptable outputs,
    # we can't just use the acceptable outputs from this run in generating a new
    # golden file - we need to write the new one additively.
    newly_expected_results = {}
    for sha, result in actual_results.items():
        expected_results = compare_against.pop(sha, None)
        if expected_results is None:
            log_golden_error(
                "SHA %s was given, but matches nothing in golden file", sha
            )
            newly_expected_results[sha] = [result]
            continue

        if result in expected_results:
            logging.debug("SHA %s matches expectations", sha)
            newly_expected_results[sha] = expected_results
            continue

        log_golden_error("SHA %s mismatches expectations!", sha)
        log_golden_error("Expected one of: %s", expected_results)
        log_golden_error("Got: %s", result)
        newly_expected_results[sha] = expected_results + [result]

    # NOTE: we delete from `compare_against` above for each SHA above; anything
    # remaining is a diff.
    if compare_against:
        for sha, result in compare_against:
            log_golden_error(
                "Uncovered SHA in golden: %s; expected result: %s", sha, result
            )

    if not had_errors:
        logging.info("Golden matches Gemini output precisely.")
        return

    new_golden_json = Path(f"{golden_json}.new")
    _write_results_to_file(newly_expected_results, new_golden_json)
    sys.exit(
        "Errors happened; see logs above. Golden specific to this run has "
        f"been placed at {new_golden_json}"
    )


# A sequence of upstream LLVM SHAs that this script should query Gemini with.
# Particularly tricky ones should receive a comment.
#
# This is placed near the bottom of the file, since it's a decent bit of
# clutter. No one's likely to get value out of reading the entire listing.
GOLDEN_SHAS: tuple[str, ...] = (
    # This is a reapply, but one that uses non-standard language in its commit
    # message to indicate that it's a reapply.
    "f72b3e1c07914fdea2fd367dada14b63adef731b",
    # Relatively standard reapply that lists both a reverted commit SHA and
    # reapplied commit SHA.
    "e8fc808bf8e78a3c80d1f8e293a92677b92366dd",
    # This is a _functional_ revert that doesn't actually revert a specific PR.
    "73ce0aca72348a80dc2c2175516a0993ab8d6be3",
    # This mentions many PRs, and is a reapply that only mentions the PR it
    # reverts by PR number.
    "fe899cedac18cf3fcf70c58084a1940936ab9a95",
    # This is a revert with a subject line that overflows into the commit body.
    "6c3d62a4b4f15eb001585bf61a8c3f4b9aff8237",
    # This is a patch that "essentially reverts the work" from another PR,
    # though does not take the form of an actual `git revert`.
    "7d2332391f81d44d7c9d1ca40bd5f393c59ad0df",
    # This is a revert (though one that only mentions the PR number), plus extra
    # changes.
    "66b34bc943644c7e20f5c5c22a706a091dd9b053",
    # This change reverts "a small part" of a larger PR.
    "3a4d506cb057488ab8dbaf234b7761edb1854be9",
    # Another revert subject line overflow, with multiple SHAs and PR numbers in
    # it (since this is a revert that's functionally relanding another commit).
    "deced287ad1da9a61302e12e0406f8be36f3831b",
    # This tries to "revert to the previous behavior to avoid [churn]," which is
    # not a traditional revert.
    "a9d491b17f4f0a131f68a5dbdac8d34c7c8427db",
    # This has mentions of 'reapply', but is a simple revert (though partial: it
    # leaves tests that were added in the PR it reverted).
    "2a5ac19605ae49d6628ac3af55d6b528cb13ed2e",
    # The subject implies this is a full revert, but body claims it's a partial
    # revert.
    "55f9eccee9b5ca6102206d4a1aba9ca21070881d",
    # This relands multiple PRs simultaneously.
    "0121a8e4319619527c9c28bbc01c74f794cc2255",
    # This Reapply mentions multiple SHAs, and has multiple PR numbers in the
    # subject.
    "496d31c8a9d69ded50e4aa7fbd5c5ba1ffd3ef2c",
    # This "effectively reverts" another PR.
    "3d994468098027f9cf550c78a1c91bb266040f61",
    # This commit has a large paragraph near the end mentioning multiple PRs,
    # reverts, and fixes.
    "76d98cfcc40e9a351efc52287338f0fd5d4402fb",
    # This is a reland that speaks of related, though independent, PRs that can
    # _also_ be relanded as a result of this PR's relanding.
    "440a4de55265c67cf41a77e40f96d7c18ed7590a",
    # This is a relatively straightforward reland, though uses nonstandard
    # verbiage in the body to give specifics (PR number, commit hash).
    "bae8f1336db6a7f3288a7dcf253f2d484743b257",
    # This commit says "To be reverted when [...]," but is not itself a revert.
    "6a45697fa63828c3ad90e2def12dae39b7e83dc5",
    # This is a reland that mentions many related LLVM commits, PRs, and issues.
    "4072a6b85beed8427d14f13248d2f9cfaede489f",
    # Simple revert, but includes "so we can revert ${another_pr}."
    "a85c725952f7eec54552c195353ff0cb6275a2e0",
    # Is neither a revert nor reland, but speaks of another PR being reverted.
    "a14659a2c8c82804b611925fa7a48fd26ef1d135",
    # **NOTE: it's recommended that you add SHAs _above_ this block**
    # This script can take a while to run to completion, and the SHAs below are
    # mostly uninteresting non-reverts. See the `--stop-after=` flag for faster
    # iteration.
    #
    # This block of SHAs is "the most recent N upstream commits at the time of
    # creating this script."
    #
    # They're mostly non-reverts, but useful to have as a baseline to protect
    # against false-positives.
    "de72cca671b962d0149cf880ec44b942a58ed843",
    "910d7e90bfc6aef5f974f0cf4b3fc034a2f4849a",
    "24ea1559d3d0005aefbca2c7a9fad164a8a33632",
    "61d569ffc338834cc81cddf31023de41fd3a6be7",
    "02f3e95a42d0a9bf84ac5535b74e9f96ef22a8ca",
    "69235d269b305e25798210d293969efb45ee3f1c",
    "95b16d1264725d8aad5a3b951f444fdec04c2224",
    "314458197e96b2344a47e91d449736c0bfbb5563",
    "197d1c1570160afbf3c5e03d81d42d124b4e5b44",
    "81f3ddf4a2d310a78b6286cf2f5eb527194f014c",
    "ed6cd8f195894e46bbf3d29c0fb639b487c2c4eb",
    "c23b4fbdbb70f04e637b488416d8e42449bfa1fb",
    "b800930db22d7735eec1d54cc66530ddab123a4d",
    "20051b7d6ed4de4301f05c385d3885696bf364df",
    "94c48a21bbdf0589540cb55057c216607e764919",
    "e9d71efb833d6f9fafd5cdff0f79d5c19b458a54",
    "6ebb8901cc0907ed4ff19444be5c545e918c1b52",
    "18e4f775c33af123772409ffde69dd424b98814a",
    "c7c022948031a3c79b92b1a0497dc75868382d17",
    "ff0093cecd0c807d122cfb6b74634074c962ade9",
    "29cde86ecc81cfe0770bf366ddaec522d3885c75",
    "6a32e2225ed42a1cb846d9753c5589e3462924c5",
    "e977b28c37c174c1b93ad78314650e03b545f560",
    "5f864560a6514bb74ecc1e0c7d3ff8c412228bfe",
    "3a561bc66264321d4c9f80d1249cb8fe1fa31e22",
    "229ab5aa2b11bb8738db2810677abfc89050ad80",
    "7e8a251f751c47f31103db2975a34d1b7780d992",
    "707447159341f7b5678dee4f47731af50524b9ae",
    "a82ca1b5603a4ed9598b784f703d908f32e970b8",
    "eccc6e22f81141691542f5dd5bbb7996e446a44f",
    "6a425f1e54d759ddc22afcbe1df442c7b35077c1",
    "0bdd312b1d0d4b9d30170f384d44fa017acfb096",
    "2d4bac867552aa361c16db26a01d36f27507994f",
    "2422972eeaebe94f591be2325563785ab7254d4e",
    "856a8b5ef9f40361f14b488a5dced9e9989f6fa8",
    "0720af8c24f1e11217a6492fed5e3f60c0a02a19",
    "92ac1ac9046d785f5f0c68e2d9f74b05c4db5d9c",
    "d7d0d7a80fc343750bbf85ea8c184737d9c70f62",
    "15a705dc931aace6ea2edf895e4258e0c3d825a0",
    "7d886fab74d4037d654d02bed24dd97c0ba863d6",
    "ffdaf85a95026945c654f981b09267ce9f81ae80",
    "b9ca01b7464caa211841f88281d0a7ed0a97d634",
    "3769ce013be2879bf0b329c14a16f5cb766f26ce",
    "c9f3a706e7a3d265d995424ac8f3f082ffaf980e",
    "05dd957cda663273ae0e5739656ffe701404f37c",
    "1458eb206fb652358b3ee7e75d95b52f3f4ac333",
    "4394a0ca4a7c0687ea0c73cdf994bb36efbc69f2",
    "b8195e3a8e77ec15f6abbcce86d6a51dca13a5c4",
    "27ed1f99e250c913715ca75c4f33e42d59a06006",
    "469863111f217aeea98d65b30266f28c7b6c1169",
    "dddeb07c2ea9bc4e507d3bd34980fa6e9513ed9f",
    "cb2d56ce960714ce6fce39e8b846326969a30c2d",
    "47f54e499210c2a66da0441b7ae54974a57d2182",
    "82046c7f339a74a198ec7b17612243663732c7f7",
    "49ccf46adc455b64c2be0006092651182b1cb2c4",
    "9faac938e1b03ea23e7212550860f8b8001757e1",
    "72bc1bea7a28f432658967463af2104db4663156",
    "abc22f771ebe05c2aeb8386337d9fb8d2bdd1094",
    "536e414b14edb2cfea59b0482a5b968ad34953a7",
    "41b5880c957320b1be68cdb642ba735fdd27bb7c",
    "adae37080587bf18da4b1ce3453a671d73bec724",
    "7a16a1ddb2eaa8c31cd648b0567897f551f8f6c6",
    "bc814348ec0412362b062cb2928e5fc76d31bccb",
    "9234066476aa82cfac3cee564883a3124df4584e",
    "b9c328480cc5c9fbc2940ce323a8dcb30a042b58",
    "11e1d465860903fd9ead27c0c1e60de4439011db",
    "d09dbdabb93ffdd6df25ae487c95a552f13e5e16",
    "c43c1c0c45fc1ec3fab7abd6e19b318f6468bf28",
    "75cc77e55e12d39aed94702b0b1365e39713081e",
    "ca52d9b8bebd9214db8ab71f87a1d5eb6d2ad42e",
    "069bf187ccc432fa379287670461462ed5001a04",
    "f68eedde7561cbe36ca775aa2d05de724fe04f96",
    "38542efcbabf5ae8ec4b3169321ac793f103bae0",
    "77c79313d1360b4f44919ddb7993543e3ac0a2b1",
    "9f102a90042fd3757c207112cfe64ee10182ace5",
    "4e11f89904dc9b77ef44b01c68742e5b00bfdf21",
    "1e2e903684719a0bdf559af261ffff9f551f4ebb",
    "740f690831a2eb09ba73b4fb5456a37ae62a5051",
    "229d86026fa0e5d9412a0d5004532f0d9733aac6",
    "660555191b3e886a578f3d9bfdcb49877e1c5da0",
    "f7c6c7ce361b8664eee962f10803e92661582176",
    "2ff44d7d658beca1724f04211e194bf4beb2a1a0",
    "06f06deb774ada5aa37db89fa7b4a88b13163e0d",
    "093395ca6b5c180eabd597236a928c5ce2854260",
    "193995d5a21dc8b923e19d9370aa8e1f374cd940",
    "b5902924b27348dfae35a501f8b6e5b66f3bed46",
    "093439c688db8d176081176576011275a1aecf23",
    "d97f0e93642722380be9ed190c17ea895817c339",
    "ad3196d7595dd53c4021b4bf4cd7bcefd85853df",
    "ed9a552563e1c8a95249036195f598990a695a95",
    "bd741975bc666d032665facd19144df9deedc5c8",
    "6d231fbb05417a77e8787f625fd14e1a30e27a5b",
    "44aedacb1b64b415fddfada39eb876602980ea72",
    "d95433bc8131e6c9f175c82f7b26e789084a347f",
    "3fbb553f7d31329212b658cca5b9eb5dae4e91b2",
    "de2bac367ff9da74191bd2de130e4a81db07ae08",
    "6f272d1ecf70fc555efb1a0c601095031d5b2ca9",
    "ff616a192bb486915200675d7be33dc042deca24",
    "f9b68838f61972fadfbe70787befc3abeb2efcb5",
    "0cb98c721bb540febab0fc0094388480940c49b0",
    "44fbeb3215f31ace95ea2a7e88121920e813db5d",
    "3b5cc2dc6374a5785741aedb28ad80b7e941b70c",
    "3fa34f17e822fbe652e694b7b421ce7108f902df",
    "e1171e6a98f9c1a5cd465a47210b2678631a9c3c",
    "c088b5ffca4c4b81a8fa0e7f006e9391eba1f191",
    "f3db0cb4d8326c4955472742872cb691d17e76c6",
    "82f5bd68d03c2ef963f5e53843b1c47989dcd5d7",
    "e10fdb989b8c59a8291f1f6931f3adfd374ad840",
    "ebaaf4d2fbf389ac3f171245e38c7a63812b43b8",
    "02fbb6a290779af31f24d6fffd104675fc10d986",
    "4be22dabc58046ddcab449368132754892242250",
    "5dff1ad3a3570f0f5a154590ce43b107dc6c3994",
    "69d0bd56ad064df569cd065902fb7036f0311c0a",
    "0bcf45ea3458ba79eb4257afcfd6af954292c9ce",
    "fac7453d2ca7ebe33dec3d60211c0374a2bb69cd",
    "900d20d0dc7b228cba9df98ed3ec713098c79342",
    "e368b5343d037c89051097c2a87a6fb76548014e",
    "cfa00d4dafbc7ffa112ea341c794b7cff7fca713",
    "f3bf8e01668bfbb32cd17be45507983557b979df",
    "dbfc3ed69088a88bffc20b16ce315746dd30fa28",
    "f73a3028c2d46928280d69d9e953ff79d2eb0fbb",
    "ceda56be7f03a790ea777e8b98b419209c3bfa49",
    "f24c50a635cbdbd214e02866a8cb22232862c3ff",
    "edeee824f044b834ec0bc8380afc345bb1a58f35",
    "fee6e539d0a052ca1f20adf55521856bfc5d5b26",
    "4784585747423a8ed6e3acbe3c8fbe97ba362cc5",
    "474bbc17831e45ae855b7385512d97c519c640fb",
    "e1d67530065efb64dba2f716a355a40535f4a19d",
    "246990dc029620619f41b6bd3bd7ba67ada1a384",
    "109040acec00e5beaef35e51df3d73d5ba4212a4",
    "46a8c094894e22d553cc527f9536b05db53250e8",
    "47944d071f27c04c1cccf51926eb14062471f6cc",
    "5805e887458801f2756d0466b84b712472507f2f",
    "95c32bf2d46ddd2c10dae426c75aa4dddcb146df",
    "04196ba01a4d2ea1649836d769e5651e89c05a82",
    "565f707beb176c81b3c18651f280304484378f2a",
    "aeeb9b507750553f0e85584bda20b8d2373b3bda",
    "6cd6de5bc0c8b09b3a252bfb8a62870c1cdede4c",
    "c869ef6ebc4882978252c3a98279928b31b58135",
    "a44532544bd96c68ce2bc885d0cc0c4c9116f8b1",
    "b9e133d5b6e41b652ba579bcb8850c00f72d0f01",
    "d618c36cb7a8c7951fb7532c07ea313b2d7ec1a7",
    "4da745a0f4fad9026dd4a84d4a9f169166575b80",
    "6ce68d3a12fb70a8a1247823e2c90a5a1dd4531d",
    "0b3ee2093954dd3c5a201eba4b7641adadd9b2c6",
    "7402cd6ded243972ab9a70da83845bce66e502c6",
    "6897ca460e6e28bcf76ae941438dd1313426e0bb",
    "6abf4f376efe9a708587e8f35d30ab850545d92f",
    "b83f7f195c64ab1c87ceea9cda9b54eaae893cdb",
    "f44d8d583c646baee12646f1609683c9afe48e33",
    "a3e068552923c0047f8a9c27c6558697b9371ed7",
    "1110e2ff9f8d055af0b81267bf01d720421b4b70",
    "406d9b1dd6522cf18e61c4c4af66db765de8afed",
    "edad89e4e052b0b7d0fe4943669b3b7c55d837a4",
    "a485e0eae01beaf68a94d1f050838866e849bd48",
    "71832a3139b454f8e714ff54e8bb0ea12dc095f5",
    "0a72e6ddac0f9154b806c40992d1616fa86957d8",
    "0abf4975bbf176d393869d290d55748794e220c4",
    "13daf3b70c6e8991c846e8384de47c5e84a94480",
    "35f003d13bce7f1a991d6a059c9c25e72009022c",
    "eb0ddba26b6a265b44b442ae666db43b9f28b26a",
    "9a592d9a849dacf02ff571c81f2b3a805e9d13e5",
    "a04142f11f926d09059614a6170eff35a4ea6ff6",
    "d9f9064cfae6929db3f55f6146ee23447b4f9f80",
    "44af26ea2e0b0fedb74276f9678eba4df5f83aab",
    "0168324523a2f6f804b2c2a2190d659b28456230",
    "df8da2ff8370fda479b5c118704af4f50e0d3536",
    "a196281896de208fca1dde315e377a46ec9a2e66",
    "2696e8c1499682f0b1f357d9035ed59f544892f8",
    "8381f95dec6d63158c034f7e173e37d97937b896",
    "04672e20d43679db4b13b8f9d19e3a2b748bca4f",
    "01472d8e357caa10964241ab50b3449014d1be12",
    "886b2133e372108da7b19bd2634c28bdbdf8d04a",
    "3d1c1a5277835baa3d71c23b396d2cbe594505d1",
    "7694856fddbb3fed10076aefec75c9b512cc352e",
    "acb5d0c211f72ba370bfeea7e5bf3b108f84895a",
    "c4846d29cdefc5fb6858ccf0378a8103b659016b",
    "a7f1702f2c5d4601de962cde14af35c313c16902",
    "7d3134f6cc59f47460646a13abcf824bae05d772",
    "e83abd774a4f7c09db26b886f8c686cdb373d1f7",
    "09dbdf651470bb4c9e5b81986a47f7c495285fbe",
    "b296ea9c14af60f9b4faa26a39ecc52c1762c794",
    "f61526971f9c62118090443c8b97fab07ae9499f",
    "d897355876287e410d35f1f0ac74d79955d50dd4",
    "885ddf4a3a4948b67ce5e792a97bf5148e8b479e",
    "d54aa36146297ddfb764394c4f70b0758b75becd",
    "281e6d2cc498d05f3ca601e3b1d595420e7ed827",
    "66392a8d8d81e66ec09452d35c85147dafb07571",
    "83e5a99ff6a5662b6e7fd6a0f9f21d70458022c2",
    "c3103068b713dbed8d8ac75b165086a1a19c89e9",
    "87404eaf0445c7e67091e4e71d6c1cfa6fd0edd4",
    "184821b63d769e48d8b89f70e8f7a5adbe429fae",
    "351b38f266718d862aa122e56667d6582625c918",
    "381623eb11cefd3ac21a36d028ba4832643010ef",
    "d1b6ce50dffcc70cd1610515527b4645b1136d1c",
    "5a47a1828abeefe72c82f732b446cc319ef65a31",
    "26dde15ed4f1310fa5df3baf03d802ea1cf009b8",
    "c2eddec4ff42eca8a93e3f8a0531dfb6e60a61ca",
    "334d0be2d496af6c511d2efb183b862e7d911329",
    "cae7bebcaa41e4c459e973b9688215f5a57bcb56",
)


def _verify_no_duplicate_golden_shas() -> None:
    counts = collections.Counter(GOLDEN_SHAS)
    shas_with_multiple_mentions = []
    for x, n in counts.most_common():
        # most_common()'s result is ordered from highest count to lowest, so we
        # can stop after hitting 1.
        if n == 1:
            break
        shas_with_multiple_mentions.append(x)

    if shas_with_multiple_mentions:
        shas_with_multiple_mentions.sort()
        raise ValueError(
            "SHA(s) in GOLDEN_SHAS have multiple mentions:"
            f"{shas_with_multiple_mentions}"
        )
