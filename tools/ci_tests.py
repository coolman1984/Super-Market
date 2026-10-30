"""Run the test suite and also report each failure as a GitHub Actions
annotation (readable through the public check-runs API when logs are not)."""

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))


def main():
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT / "tests"))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    for test, trace in result.failures + result.errors:
        tail = "\n".join(trace.strip().splitlines()[-12:])
        msg = tail.replace("%", "%25").replace("\r", "").replace("\n", "%0A")
        print("::error title=%s::%s" % (str(test).replace(":", " ").replace(",", " "), msg))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
