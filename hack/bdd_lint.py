# SPDX-License-Identifier: GPL-3.0-only
"""Validate Gherkin syntax in features/ without executing step definitions.

Used by the spec-only phase of the BDD workflow: a feature file is written and
reviewed before any step definitions exist, so the normal test runner cannot be
used to check it -- undefined steps are expected at that point.
"""

import sys
from pathlib import Path

from gherkin.errors import CompositeParserException
from gherkin.parser import Parser
from gherkin.token_scanner import TokenScanner

FEATURES_DIR = Path(__file__).resolve().parent.parent / "features"


def main() -> int:
    """Parse every feature file and return 1 if any of them is malformed."""
    feature_files = sorted(FEATURES_DIR.rglob("*.feature"))
    if not feature_files:
        print(f"no .feature files found under {FEATURES_DIR}")
        return 0

    failed = False
    for path in feature_files:
        try:
            Parser().parse(TokenScanner(path.read_text()))
        except CompositeParserException as e:
            failed = True
            print(f"FAIL {path.relative_to(FEATURES_DIR.parent)}")
            for err in e.errors:
                print(f"  {err}")
        else:
            print(f"ok   {path.relative_to(FEATURES_DIR.parent)}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
