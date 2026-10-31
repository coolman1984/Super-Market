"""Language quality gates (review of 31 Oct 2026): two dictionaries with identical
keys, every server error code explained in both languages, terminology unified,
no instruction to invent a loss, guide steps tied to real buttons and role-aware."""

import json
import pathlib
import re
import shutil
import subprocess
import unittest

from errorcodes import server_codes

ROOT = pathlib.Path(__file__).resolve().parents[1]
WEB = ROOT / "web" / "js"
NODE = shutil.which("node")


def dictionaries():
    script = ("global.window=global;global.SM={};const fs=require('fs');"
              "for (const f of ['i18n.js','i18n/en.js','i18n/ar.js']) eval(fs.readFileSync(%r+'/'+f,'utf8'));"
              "console.log(JSON.stringify(SM.i18n))" % str(WEB))
    return json.loads(subprocess.run([NODE, "-e", script], capture_output=True, text=True, encoding="utf-8", check=True).stdout)


def sources():
    files = [WEB / "app.js", WEB / "core.js", WEB / "scan.js"] + sorted((WEB / "views").glob("*.js"))
    return "\n".join(f.read_text(encoding="utf-8") for f in files)


BANNED_AR = ["الجارد", "جارد", "حد الآجل", "سطح المكتب", "يا عم", "يا باشا", "يسطا", "ياسطى", "يا معلم", "بتاع إيه", "اخرس", "غبي", "حمار"]


@unittest.skipUnless(NODE, "node is needed to read the browser dictionaries")
class Language(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = dictionaries()
        cls.ar, cls.en = cls.d["ar"], cls.d["en"]
        cls.src = sources()

    def test_same_keys_in_both_dictionaries(self):
        self.assertEqual(sorted(set(self.ar) ^ set(self.en)), [])

    def test_every_server_error_code_has_an_arabic_and_english_message(self):
        missing = sorted(c for c in server_codes() if ("err." + c) not in self.ar or ("err." + c) not in self.en)
        self.assertEqual(missing, [])

    def test_every_literal_key_used_by_the_interface_exists(self):
        used = set(re.findall(r"\bt\('([a-z_0-9.]+)'", self.src))
        used |= set(re.findall(r"\bt\(\"([a-z_0-9.]+)\"", self.src))
        prefixes = {k for k in used if k.endswith((".", "_"))}           # t('entry.' + kind): a family of keys
        self.assertEqual(sorted(k for k in used - prefixes if k not in self.ar or k not in self.en), [])
        for prefix in prefixes:
            self.assertTrue(any(k.startswith(prefix) for k in self.ar), prefix)

    def test_placeholders_and_numbers_match_between_languages(self):
        def digits(text):
            text = text.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
            return sorted(re.findall(r"\d+(?:\.\d+)?", text))
        bad = []
        for k in self.ar:
            if sorted(re.findall(r"\{[a-z_0-9]+\}", self.ar[k])) != sorted(re.findall(r"\{[a-z_0-9]+\}", self.en[k])):
                bad.append(("placeholder", k))
            if k.startswith(("scn.", "lbl.")) or "{b:" in self.ar[k]:
                continue
            if digits(re.sub(r"\{[a-z_0-9:.]+\}", "", self.ar[k])) != digits(re.sub(r"\{[a-z_0-9:.]+\}", "", self.en[k])):
                bad.append(("numbers", k, self.ar[k], self.en[k]))
        self.maxDiff = None
        self.assertEqual(bad, [])

    def test_formal_arabic_terms_and_no_colloquial_words(self):
        # owner direction 31 Oct 2026: respectful Egyptian Arabic is welcome ("ابدأ ورديتك", "في الدرج كام؟");
        # what stays out is the disrespectful, the street slang and the ambiguous (docs/LANGUAGE_GUIDE.md)
        banned = BANNED_AR
        hits = []
        for k, v in self.ar.items():
            for w in banned:
                if re.search(r"(?<![\u0621-\u064A])" + re.escape(w.strip()) + r"(?![\u0621-\u064A])", v):
                    hits.append((k, w, v))
        self.assertEqual(hits, [])
        for term in ("أمين الصندوق", "مسؤول الجرد", "رصيد بداية الوردية", "إغلاق الوردية", "الحد الائتماني", "الوزن الصافي", "حجز للفحص", "استدعاء"):
            self.assertTrue(any(term in v for v in self.ar.values()), term)

    def test_no_text_tells_the_user_to_invent_a_loss(self):
        phrases_ar = ["حتى يتوازن", "حتى تتوازن", "ليتوازن الوزن", "اكتب الفاقد حتى", "سجّل فاقدًا ليتوازن", "اخترع"]
        phrases_en = ["until weight balances", "until the weight balances", "to make the weight balance", "invent a loss to"]
        for k, v in self.ar.items():
            for p in phrases_ar:
                if p in v and "لا تخترع" not in v:
                    self.fail((k, v))
        for k, v in self.en.items():
            for p in phrases_en:
                if p in v.lower() and "never invent" not in v.lower() and "do not invent" not in v.lower():
                    self.fail((k, v))
        self.assertIn("لا تخترع", self.ar["help.prepare.errors"])
        self.assertIn("never invent", self.en["help.prepare.errors"])

    def test_every_screen_has_the_full_guide(self):
        screens = ["today", "sell", "cash", "returns", "receive", "stock", "prepare", "count", "reports", "parties", "catalog", "scanner", "settings"]
        fields = ["purpose", "need", "steps", "example", "errors", "fix", "ok"]
        for v in screens:
            for f in fields:
                for dic in (self.ar, self.en):
                    self.assertTrue(dic.get("help.%s.%s" % (v, f)), (v, f))
            self.assertGreaterEqual(len(self.ar["help.%s.steps" % v].split("|")), 3, v)
            self.assertTrue(re.search(r"\d", self.ar["help.%s.example" % v]), v)           # a numeric example
            self.assertTrue(any(k.startswith("help.%s.next." % v) for k in self.ar), v)
        self.assertIn("parties", screens)                                                  # suppliers and customers explained

    def test_guide_steps_name_only_real_buttons(self):
        tokens = set()
        for dic in (self.ar, self.en):
            for k, v in dic.items():
                if k.startswith("help."):
                    tokens |= set(re.findall(r"\{b:([a-z_0-9.]+)\}", v))
        quoted = set(re.findall(r"""['"]([a-z_0-9]+(?:\.[a-z_0-9]+)*)['"]""", self.src))
        prefixes = set(re.findall(r"\bt\('([a-z_0-9.]+\.)'\s*\+", self.src))
        unreal = sorted(k for k in tokens if k not in self.ar or not (k in quoted or any(k.startswith(p) for p in prefixes)))
        self.assertEqual(unreal, [])

    def test_next_steps_follow_role_and_state(self):
        script = r"""
global.window=global;global.SM={state:{},h:()=>0,t:k=>k,icon:()=>0,views:{}};const fs=require('fs');
for (const f of ['i18n.js','i18n/en.js','i18n/ar.js']) eval(fs.readFileSync(%r+'/'+f,'utf8'));
global.document={};eval(fs.readFileSync(%r+'/views/help.js','utf8'));
const roles=%s;
const out={};
for (const [role,perms] of Object.entries(roles)) {
  SM.can=p=>perms.includes(p);
  out[role]={};
  for (const v of Object.keys(SM.helpNextRules)) for (const open of [false,true]) {
    const tag=SM.helpNext(v,{shiftOpen:open});
    if(!(('help.'+v+'.next.'+tag) in SM.i18n.ar)||!(('help.'+v+'.next.'+tag) in SM.i18n.en)) throw new Error('missing '+v+' '+tag);
    out[role][v+(open?'+':'-')]=tag;
  }
}
console.log(JSON.stringify(out));""" % (str(WEB), str(WEB), json.dumps(self.roles()))
        out = json.loads(subprocess.run([NODE, "-e", script], capture_output=True, text=True, encoding="utf-8", check=True).stdout)
        self.assertEqual(out["counter"]["today-"], "count")            # a counter is never sent to open a shift or sell
        self.assertEqual(out["counter"]["today+"], "count")
        self.assertEqual(out["accountant"]["today-"], "reports")       # an accountant never opens a shift
        self.assertEqual(out["cashier"]["today-"], "shift")
        self.assertEqual(out["cashier"]["today+"], "any")
        self.assertEqual(out["cashier"]["sell-"], "open_first")
        self.assertEqual(out["cashier"]["sell+"], "close")
        self.assertEqual(out["receiver"]["receive-"], "handover")
        self.assertEqual(out["owner"]["settings-"], "owner")
        self.assertEqual(out["manager"]["settings-"], "any")           # a manager does not manage users

    @staticmethod
    def roles():
        import sys
        sys.path.insert(0, str(ROOT))
        from server.auth import ROLES
        return {r: sorted(p) for r, p in ROLES.items()}

    def test_settings_text_does_not_give_user_management_to_the_manager(self):
        self.assertIn("مالك المحل وحده", self.ar["help.settings.need"])
        self.assertNotIn("للمالك أو المدير", " ".join(self.ar.values()))

    def test_server_launcher_and_training_texts_use_the_same_terms(self):
        root = WEB.parent.parent
        banned = BANNED_AR
        files = [root / "launcher.py"] + sorted((root / "server").glob("*.py"))
        hits = [(f.name, w) for f in files for w in banned if re.search(r"(?<![\u0621-\u064A])" + w + r"(?![\u0621-\u064A])", f.read_text(encoding="utf-8"))]
        self.assertEqual(hits, [])

    def test_no_absolute_isolation_promise(self):
        """Training uses separate data; no text may promise that nothing can ever reach the real shop."""
        banned_ar = ["لا شيء هنا يصل", "معزولة تمامًا", "منفصلة تمامًا", "مستحيل أن", "لا يمكن إطلاقًا"]
        banned_en = ["nothing here reaches", "completely isolated", "totally separate", "impossible to"]
        for k, v in self.ar.items():
            for w in banned_ar:
                self.assertNotIn(w, v, k)
        for k, v in self.en.items():
            for w in banned_en:
                self.assertNotIn(w, v.lower(), k)
        self.assertIn("منفصلة", self.ar["help.in_training"])

    def test_no_desktop_shortcut_is_promised(self):
        for dic in (self.ar, self.en):
            for k, v in dic.items():
                self.assertNotIn("سطح المكتب", v, k)
                self.assertNotIn("desktop", v.lower(), k)


if __name__ == "__main__":
    unittest.main()
