#!/usr/bin/env python3
"""Run all verification and regression tests for UGH! Python Port."""
import glob
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
TESTS_DIR = os.path.join(ROOT, 'tests')


def main():
    test_files = sorted(glob.glob(os.path.join(TESTS_DIR, 'test_*.py')))
    if not test_files:
        print("No test files found in tests/.")
        sys.exit(1)

    print("=" * 70)
    print("                UGH! Python Port - Test Suite Runner")
    print(f"Found {len(test_files)} test suites in tests/")
    print("=" * 70 + "\n")

    failed = []
    for test in test_files:
        name = os.path.basename(test)
        print(f"\n>>> Running: {name}")
        cmd = [sys.executable, test]
        res = subprocess.run(cmd, cwd=ROOT)
        if res.returncode != 0:
            failed.append((name, res.returncode))
            print(f"FAILED: {name} (exit code {res.returncode})")
        else:
            print(f"PASSED: {name}")

    print("\n" + "=" * 70)
    if not failed:
        print(f"ALL {len(test_files)} TEST SUITES PASSED (100% PARITY & HEALTH)!")
        print("=" * 70)
        sys.exit(0)
    else:
        print(f"TEST RUN FAILED: {len(failed)} of {len(test_files)} tests failed.")
        for name, code in failed:
            print(f"  - {name} (code {code})")
        print("=" * 70)
        sys.exit(1)


if __name__ == '__main__':
    main()
