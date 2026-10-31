"""The in-app guide (af-guide, same engine as the Yousef app) passes the factory gate:
every guide, step, target, role course and problem is valid; every server error code
has a problem entry; every [[button]] named in a step exists in both UI dictionaries;
the wording passes the «العربية الميسّرة» lint; every data-guide target exists in the screens."""

import json
import pathlib
import re
import unittest

import helpers  # noqa: F401
from errorcodes import server_codes
from test_language import NODE, dictionaries
from server import afguide
from server.auth import PERMISSIONS, ROLES

ROOT = pathlib.Path(__file__).resolve().parents[1]
GUIDE = ROOT / "web" / "guide"


def access():
    return {"groups": [{"id": "shop", "permissions": [{"id": p} for p in sorted(PERMISSIONS) + ["*"]]}],
            "profiles": [{"id": r, "perms": sorted(perms) + ["*"]} for r, perms in ROLES.items()]}


@unittest.skipUnless(NODE, "node reads the UI dictionaries")
class GuideGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat, cls.texts = afguide.load_dir(GUIDE)
        cls.ui = dictionaries()

    def test_factory_check_passes_in_release_mode(self):
        found = afguide.check(self.cat, self.texts, ui=self.ui, access=access(),
                              error_codes=["err." + c for c in server_codes()], release=True)
        errors = [str(f) for f in found if f.level == "error"]
        self.assertEqual(errors, [])

    def test_every_server_error_is_explained_once(self):
        listed = [e for p in self.cat["problems"] for e in p["errors"]]
        self.assertEqual(len(listed), len(set(listed)))
        self.assertEqual(sorted(listed), sorted("err." + c for c in server_codes()))

    def test_every_marked_target_exists_in_the_screens(self):
        src = "\n".join(f.read_text(encoding="utf-8") for f in (ROOT / "web" / "js").rglob("*.js"))
        marked = set(re.findall(r"data-guide['\"]?\s*[:=]\s*['\"]([a-z_.]+)", src)) | set(re.findall(r"setAttribute\('data-guide', '([a-z_.]+)'\)", src))
        marked |= {"prep.mode.cutting", "prep.mode.recipe"}          # built from 'prep.mode.' + m
        for tid, t in self.cat["targets"].items():
            m = re.fullmatch(r'\[data-guide="([a-z_.]+)"\]', t["sel"])
            if m:
                self.assertIn(m.group(1), marked, tid)

    def test_each_role_course_matches_a_real_role_and_starts_with_the_screen(self):
        self.assertEqual({r["id"] for r in self.cat["roles"]}, set(ROLES))
        for r in self.cat["roles"]:
            self.assertEqual(r["path"][0], "first-look", r["id"])

    def test_training_examples_use_real_training_data(self):
        """The examples name things that exist in the training copy (codes, receipt, products)."""
        from server.training import ean
        text = json.dumps(self.texts["ar"], ensure_ascii=False)
        for code in (ean("622000000001"), ean("622000000002"), "TR-000001"):
            self.assertIn(code, text)
        for name in ("عصير مانجو", "لحم بقري بالعظم", "مورد تجريبي", "طاجن لحمة بالبصل"):
            self.assertIn(name, text)


if __name__ == "__main__":
    unittest.main()
