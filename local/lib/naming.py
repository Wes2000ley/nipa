#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import re


def namify(what):
    if not what:
        return "no-name"
    name = re.sub(r"[^0-9a-zA-Z]+", "-", what)
    if name.endswith("-"):
        name = name[:-1]
    return name
