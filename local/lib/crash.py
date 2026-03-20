#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import re


def has_crash(output):
    return (
        output.find("] RIP: ") != -1 or
        output.find("] Call Trace:") != -1 or
        output.find("] ref_tracker: ") != -1 or
        output.find("unreferenced object 0x") != -1
    )


def finger_print_skip_pfx_len(filters, needles):
    if filters and "crash-prefix-skip" in filters:
        for skip_pfx in filters["crash-prefix-skip"]:
            if len(needles) < len(skip_pfx):
                continue
            if needles[:len(skip_pfx)] == skip_pfx:
                return len(skip_pfx)
    return 0


def crash_finger_print(filters, lines):
    needles = []
    need_re = re.compile(r".*(  |0:|>\] )([a-z0-9_]+)\+0x[0-9a-f]+/0x[0-9a-f]+.*")
    skip = 0
    for line in lines:
        match = need_re.match(line)
        if not match:
            continue
        needles.append(match.groups()[1])
        skip = finger_print_skip_pfx_len(filters, needles)
        if len(needles) - skip == 5:
            break

    needles = needles[skip:]
    return ":".join(needles)


def extract_crash(outputs, prompt, get_filters):
    in_crash = False
    start = 0
    crash_lines = []
    finger_prints = set()
    last5 = [""] * 5

    for line in outputs.split("\n"):
        if in_crash:
            in_crash &= "] ---[ end trace " not in line
            in_crash &= "]  </TASK>" not in line
            in_crash &= line[-2:] != "] "
            if prompt:
                in_crash &= not line.startswith(prompt)
            if not in_crash:
                last5 = [""] * 5
                finger_prints.add(crash_finger_print(get_filters(), crash_lines[start:]))
        else:
            in_crash |= "] Hardware name: " in line
            in_crash |= "] ref_tracker: " in line
            in_crash |= " blocked for more than " in line
            in_crash |= line.startswith("unreferenced object 0x")
            if in_crash:
                start = len(crash_lines)
                crash_lines += last5

        last5 = last5[1:] + ["| " + line]

        if in_crash:
            crash_lines.append(line)

    if in_crash:
        finger_prints.add(crash_finger_print(get_filters(), crash_lines[start:]))

    return crash_lines, finger_prints
