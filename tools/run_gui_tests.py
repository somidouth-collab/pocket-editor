#!/usr/bin/env python3
"""Run each GUI scenario in a fresh process (one Tk lifecycle per process).

Repeated Tk interpreter destruction/recreation can crash the native Windows
font/event backend. This retains every assertion and reports any failing or
hanging child as a failed check rather than skipping GUI coverage.
"""
import argparse
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


def cases(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from cases(test)
        else:
            yield test.id()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('modules', nargs='+')
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT))
    suite = unittest.defaultTestLoader.loadTestsFromNames(args.modules)
    identifiers = list(dict.fromkeys(cases(suite)))
    for identifier in identifiers:
        print(f'GUI scenario: {identifier}', flush=True)
        try:
            result = subprocess.run([sys.executable, '-X', 'faulthandler', '-B',
                                     '-m', 'unittest', '-v', identifier], cwd=ROOT, timeout=90)
        except subprocess.TimeoutExpired:
            print(f'GUI scenario timed out: {identifier}', file=sys.stderr)
            return 1
        if result.returncode:
            return 1
    print(f'Passed {len(identifiers)} isolated GUI scenarios.', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
