#!/usr/bin/env python3
"""Compile the optional native OPL2 core (extractor/_opl2c.c).

  python3 -m extractor.build_native [--out DIR] [--universal]

Needs a C compiler (cc/gcc/clang, or MSVC's cl on Windows).  Without the
resulting library extractor/opl2.py falls back to the numpy implementation,
which renders identical audio, only slower.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, '_opl2c.c')


def lib_name() -> str:
    ext = {'win32': '.dll', 'darwin': '.dylib'}.get(sys.platform, '.so')
    return '_opl2c' + ext


def build(out_dir: str = HERE, universal: bool = False) -> str:
    out = os.path.join(out_dir, lib_name())
    cc = os.environ.get('CC') or next((c for c in ('cc', 'gcc', 'clang') if shutil.which(c)), None)
    if sys.platform == 'win32' and not os.environ.get('CC') and shutil.which('cl') and not shutil.which('gcc'):
        cmd = ['cl', '/nologo', '/O2', '/LD', SRC, '/Fe:' + out, '/Fo:' + os.path.join(out_dir, '_opl2c.obj')]
    elif cc:
        cmd = [cc, '-O3', '-shared', '-o', out, SRC]
        if sys.platform == 'darwin':
            cmd[2] = '-dynamiclib'
            if universal:
                cmd[1:1] = ['-arch', 'arm64', '-arch', 'x86_64']
        elif sys.platform == 'win32':
            cmd[1:1] = ['-static-libgcc']
        else:
            cmd[1:1] = ['-fPIC']
    else:
        raise SystemExit('no C compiler found (tried cc, gcc, clang, cl)')
    os.makedirs(out_dir, exist_ok=True)
    subprocess.run(cmd, check=True)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--out', default=HERE, help='directory for the library (default: extractor/)')
    ap.add_argument('--universal', action='store_true', help='macOS: arm64 + x86_64 in one library')
    args = ap.parse_args()
    print('built', build(args.out, args.universal))


if __name__ == '__main__':
    main()
