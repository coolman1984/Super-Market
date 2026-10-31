"""Review 1170bf2 cases 2-3: typed numbers. "2,5" kg is two and a half kilos, never 25;
a money amount with a decimal comma is never multiplied by 100. Runs the real browser
parser from web/js/core.js under node (no browser needed, so it also runs on Windows CI)."""

import json
import pathlib
import re
import shutil
import subprocess
import unittest

CORE = pathlib.Path(__file__).resolve().parents[1] / "web" / "js" / "core.js"
NODE = shutil.which("node")


def parse(cases):
    src = CORE.read_text(encoding="utf-8")
    start = src.index("const AR_DIGITS")
    end = src.index("SM.parseInt")
    body = src[start:end].replace("SM.parseMoney", "const parseMoney").replace("SM.parseKg", "const parseKg")
    script = body + "\nconsole.log(JSON.stringify(%s.map(([f, s]) => f === 'kg' ? parseKg(s) : parseMoney(s))));" % json.dumps(cases)
    return json.loads(subprocess.run([NODE, "-e", script], capture_output=True, text=True, encoding="utf-8", check=True).stdout)


@unittest.skipUnless(NODE, "node runs the browser parser")
class TypedNumbers(unittest.TestCase):
    def test_decimal_comma_in_every_form(self):
        cases = [["kg", "٢،٥"], ["kg", "2,5"], ["kg", "٢٫٥"], ["kg", "2.5"], ["kg", " 2 , 5 "],
                 ["money", "12,50"], ["money", "١٢٫٥٠"], ["money", "12.5"], ["money", "150"]]
        self.assertEqual(parse(cases), [2500, 2500, 2500, 2500, 2500, 1250, 1250, 1250, 15000])

    def test_ambiguous_input_is_refused_not_guessed(self):
        cases = [["money", "1,000.50"], ["money", "1,005"], ["kg", "1.2.3"], ["money", "-5"], ["money", "abc"]]
        self.assertEqual(parse(cases), [None] * len(cases))


if __name__ == "__main__":
    unittest.main()
