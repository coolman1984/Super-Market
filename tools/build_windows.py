"""Builds the Windows installer SuperMarket-Setup-<version>.exe, the same way as the Yousef app
(run on Windows; GitHub Actions does it, see .github/workflows/windows-portable.yml).

Steps: draw the icon, compile launcher.py and the server into SuperMarket.exe with Nuitka
(Python translated to C: no readable .py source in the customer's folder), copy the screens
(web/), then wrap everything in the Inno Setup installer (installer/super-market.iss).

    pip install "nuitka>=2.5,<3" ordered-set zstandard     and Inno Setup 6     (build machine only)
    python tools/build_windows.py                          TO_BUILD_CHANNEL=ci-test for a non-customer build

The customer's PC needs nothing: no Python, no packages, no internet.
"""
import glob
import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
sys.path.insert(0, str(ROOT))
from server.version import VERSION  # noqa: E402

PRODUCT, DEVELOPER, COPYRIGHT = "Super Market", "Mohamed Fawzy", "(c) 2026 Mohamed Fawzy"


def run(cmd):
    print(">", " ".join(str(c) for c in cmd), flush=True)
    subprocess.check_call([str(c) for c in cmd], cwd=ROOT)


def iscc():
    for p in [shutil.which("iscc"), r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe", r"C:\Program Files\Inno Setup 6\ISCC.exe"]:
        if p and os.path.exists(p):
            return p
    sys.exit("Inno Setup 6 (ISCC.exe) was not found.")


def version4():
    nums = [int("".join(ch for ch in part if ch.isdigit()) or 0) for part in VERSION.split("-")[0].split(".")]
    return ".".join(str(n) for n in (nums + [0, 0, 0, 0])[:4])


ENROLL_FILE = ROOT / "server" / "_enroll.py"


def bake_enroll_token(channel):
    """The installed program makes its own cloud store on its first run with a build token that only the service can sign.
    A customer (commercial) build must carry it, or the owner would have to connect it by hand: the build stops instead.
    The token comes from the encrypted secret SM_ENROLL_TOKEN, is written to the git-ignored server/_enroll.py and is never
    printed. Test builds (ci-test) carry none: they never connect (SM_CLOUD_URL=off)."""
    token = os.environ.get("SM_ENROLL_TOKEN", "").strip()
    if channel == "ci-test":
        ENROLL_FILE.unlink(missing_ok=True)
        return
    if not token:
        raise SystemExit("SM_ENROLL_TOKEN is not set: a customer build must carry the cloud build token (docs/RELEASE.md).")
    ENROLL_FILE.write_text('TOKEN = %r\n' % token, encoding="utf-8")


def main():
    BUILD.mkdir(exist_ok=True)
    bake_enroll_token(os.environ.get("TO_BUILD_CHANNEL", "commercial"))
    icon = BUILD / "supermarket.ico"
    run([sys.executable, "tools/make_icon.py", icon])
    v4 = version4()
    run([sys.executable, "-m", "nuitka", "--standalone", "--assume-yes-for-downloads", "--windows-console-mode=attach",
         "--python-flag=no_docstrings", f"--output-dir={BUILD}", "--output-filename=SuperMarket.exe",
         f"--windows-icon-from-ico={icon}", f"--company-name={DEVELOPER}", f"--product-name={PRODUCT}",
         f"--file-description={PRODUCT}", f"--file-version={v4}", f"--product-version={v4}", f"--copyright={COPYRIGHT}",
         "--include-package=server", "--include-data-dir=web=web",
         "--include-data-files=server/afguide_ar_lexicon.json=server/afguide_ar_lexicon.json",
         "--nofollow-import-to=tkinter,unittest,pydoc,test", "launcher.py"])
    dist = BUILD / "launcher.dist"
    assert (dist / "SuperMarket.exe").exists(), "SuperMarket.exe was not built"
    for name in ("README-WINDOWS.txt",):
        shutil.copy(ROOT / "docs" / name, dist / name)
    (dist / "docs").mkdir(exist_ok=True)
    for name in ("USER_GUIDE.md", "SUPPORT_GUIDE.md", "SCANNER_COMPATIBILITY.md", "WINDOWS.md"):
        shutil.copy(ROOT / "docs" / name, dist / "docs" / name)
    # the customer's folder holds no readable program source, caches or debugging files (same check as the Yousef app)
    forbidden = {".py", ".pyc", ".pyo", ".pdb", ".map", ".c", ".h"}
    leaks = [str(p.relative_to(dist)) for p in dist.rglob("*") if p.is_file() and p.suffix.lower() in forbidden]
    if leaks:
        raise RuntimeError("customer package contains readable sources: %r" % leaks[:20])
    channel = os.environ.get("TO_BUILD_CHANNEL", "commercial")
    run([iscc(), f"/DAppChannel={channel}", f"/DAppVersion={VERSION}", f"/DAppVersion4={v4}", f"/DAppPublisher={DEVELOPER}",
         f"/DAppCopyright={COPYRIGHT}", ROOT / "installer" / "super-market.iss"])
    ENROLL_FILE.unlink(missing_ok=True)       # never leave the token in the work tree
    if channel == "ci-test":
        (ROOT / "dist").mkdir(exist_ok=True)
        (ROOT / "dist" / "CI-TEST-BUILD-NOT-FOR-CUSTOMERS.txt").write_text("TEST ONLY. Built by CI. Never sell this installer.\n", encoding="utf-8")
    print("Done:", glob.glob(str(ROOT / "dist" / "SuperMarket-Setup-*.exe")))


if __name__ == "__main__":
    main()
