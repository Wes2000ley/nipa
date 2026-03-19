#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import re


def guess_indicators(output):
    return {
        "fail": output.find("[FAIL]") != -1 or output.find("[fail]") != -1 or
                output.find(" FAIL:") != -1 or
                output.find("\nnot ok 1 selftests: ") != -1 or
                output.find("\n# not ok 1") != -1,
        "skip": output.find("[SKIP]") != -1 or output.find("[skip]") != -1 or
                output.find(" # SKIP") != -1 or output.find("SKIP:") != -1,
        "pass": output.find("[OKAY]") != -1 or output.find("[PASS]") != -1 or
                output.find("[ OK ]") != -1 or output.find("[OK]") != -1 or
                output.find("[ ok ]") != -1 or output.find("[pass]") != -1 or
                output.find("PASSED all ") != -1 or
                output.find("\nok 1 selftests: ") != -1 or
                bool(re.search(
                    r"# Totals: pass:[1-9]\d* fail:0 (xfail:0 )?(xpass:0 )?skip:0 error:0",
                    output)),
    }


def result_from_indicators(retcode, indicators):
    result = "pass"
    if indicators["skip"] or not indicators["pass"]:
        result = "skip"
    if retcode == 4:
        result = "skip"
    elif retcode:
        result = "fail"
    if indicators["fail"]:
        result = "fail"
    return result


def parse_nested_tests(full_run, namify_fn, prev_results=None):
    tests = []
    nested_tests = False

    result_re = re.compile(r"(not )?ok (\d+)( -)? ([^#]*[^ ])( +# +)?([^ ].*)?$")
    time_re = re.compile(r"time=(\d+)ms")

    for line in full_run.split("\n"):
        if nested_tests:
            if line.startswith("# "):
                line = line[2:]
            else:
                nested_tests = False
        elif line.startswith("# TAP version "):
            nested_tests = True
            continue

        if not nested_tests:
            continue

        if line.startswith("ok "):
            result = "pass"
        elif line.startswith("not ok "):
            result = "fail"
        else:
            continue

        match = result_re.match(line)
        if match is None:
            continue
        groups = match.groups()
        record = {"test": namify_fn(groups[3])}

        if len(groups) > 5 and groups[4] and groups[5]:
            if groups[5].lower().startswith("skip"):
                result = "skip"

            times = time_re.findall(groups[5].lower())
            if times:
                record["time"] = round(int(times[-1]) / 1000.0)

        record["result"] = result

        if prev_results is not None:
            for entry in prev_results:
                if entry["test"] == record["test"]:
                    entry["retry"] = result
                    break
            else:
                record["result"] = "skip"
                record["retry"] = result
                prev_results.append(record)
        else:
            tests.append(record)

    return tests
