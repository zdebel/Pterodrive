#!/usr/bin/env python3
"""Unified CLI tool to extract all UGH! assets (graphics, levels, and audio).

Usage:
  python3 -m extractor [path/to/UGH.EXE] [assets_dir]
"""
import os
import shutil
import sys

from .extract_assets import _find_ci, extract_all
from .export_audio import export_all as export_all_audio


def main():
    exe = sys.argv[1] if len(sys.argv) > 1 else 'UGH.EXE'
    outdir = sys.argv[2] if len(sys.argv) > 2 else 'assets'

    if not os.path.isfile(exe):
        print(f"Error: Executable not found at '{exe}'")
        sys.exit(1)

    print("=" * 70)
    print("           UGH! Unified Asset & Audio Extraction Pipeline")
    print(f"Source executable: {exe}")
    print(f"Output directory:  {outdir}/")
    print("=" * 70)

    # 1. Palettes, screens, levels, and sprites
    print("\n[1/3] Extracting palettes, full screens, levels, and sprites...")
    extract_all(exe, outdir, quiet=False)

    # 2. Music and sound effects (ADLX driver + OPL2 emulator)
    print("\n[2/3] Rendering OPL2 music & sound effects...")
    export_all_audio(exe, os.path.join(outdir, 'audio'), quiet=False)

    # 3. Executable and highscore reference
    print("\n[3/3] Copying executable reference into assets...")
    target_exe = os.path.join(outdir, 'UGH.EXE')
    if os.path.abspath(exe) != os.path.abspath(target_exe):
        shutil.copyfile(exe, target_exe)
    hi = _find_ci(os.path.dirname(os.path.abspath(exe)), 'UGH!.HI')
    if hi:
        shutil.copyfile(hi, os.path.join(outdir, 'UGH!.HI'))

    print("\n" + "=" * 70)
    print("✓ Complete extraction finished successfully!")
    print("=" * 70)


if __name__ == '__main__':
    main()
