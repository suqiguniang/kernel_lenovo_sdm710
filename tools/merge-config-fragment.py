#!/usr/bin/env python3
"""Deterministically merge a kernel config fragment into a defconfig.

Single source of truth stays the fragment file: this tool only rewrites the
matching lines of the target defconfig, so a re-run produces no diff.

Rules per fragment entry:
  CONFIG_X=y                 -> replace an existing "CONFIG_X=..." line, or
                                replace "# CONFIG_X is not set", or append.
  # CONFIG_X is not set      -> same, with the "is not set" form.

Usage:
  tools/merge-config-fragment.py <fragment.config> <defconfig> [--check]

--check exits 1 (without writing) when the defconfig is not already merged.
"""
import re
import sys

ASSIGN = re.compile(r'^(CONFIG_[A-Za-z0-9_]+)=')
NOTSET = re.compile(r'^# (CONFIG_[A-Za-z0-9_]+) is not set\s*$')


def parse_fragment(path):
    entries = []
    for raw in open(path, encoding='utf-8'):
        line = raw.rstrip('\n')
        s = line.strip()
        if not s or s.startswith('#'):
            m = NOTSET.match(s)
            if m:
                entries.append((m.group(1), f'# {m.group(1)} is not set'))
            continue
        m = ASSIGN.match(s)
        if m:
            entries.append((m.group(1), s))
    return entries


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    frag, target = argv[1], argv[2]
    check = '--check' in argv[3:]

    entries = parse_fragment(frag)
    lines = open(target, encoding='utf-8').read().split('\n')
    wanted = dict(entries)

    seen = set()
    out = []
    for line in lines:
        s = line.strip()
        key = None
        m = ASSIGN.match(s)
        if m:
            key = m.group(1)
        else:
            m = NOTSET.match(s)
            if m:
                key = m.group(1)
        if key and key in wanted:
            if key in seen:          # drop duplicate definitions
                continue
            seen.add(key)
            out.append(wanted[key])
        else:
            out.append(line)

    appended = [k for k, _ in entries if k not in seen]
    if appended:
        while out and out[-1] == '':
            out.pop()
        out.append('')
        for key in appended:
            out.append(wanted[key])
        out.append('')

    new_text = '\n'.join(out)
    if not new_text.endswith('\n'):
        new_text += '\n'
    old_text = open(target, encoding='utf-8').read()

    if check:
        if old_text == new_text:
            print(f'OK   {target}: fragment already merged '
                  f'({len(entries)} entries, {len(appended)} appended on a fresh merge)')
            return 0
        print(f'FAIL {target}: out of sync with {frag}')
        return 1

    if old_text == new_text:
        print(f'no change: {target} already merged ({len(entries)} entries)')
        return 0
    open(target, 'w', encoding='utf-8').write(new_text)
    print(f'merged {len(entries)} entries into {target} '
          f'({len(seen)} rewritten in place, {len(appended)} appended)')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
