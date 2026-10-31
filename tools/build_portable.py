"""Build the Windows portable package (no installation on the customer PC).

Runs on the BUILD machine (any OS with Python 3.13 and internet to NuGet):
  python tools/build_portable.py [--nupkg path/to/python.3.13.17.nupkg] [--out dist]

Python comes from the official "python" NuGet package published by the
Python Software Foundation (the documented reduced Windows build for CI). It
is pinned to one version and checked against the SHA-512 recorded in the
NuGet catalog before use. The result is one folder + zip:

  SuperMarket-<version>-win64/
    python/            trimmed CPython 3.13 runtime (+ python313._pth)
    server/ web/ tools/ docs/ launcher.py start*.bat cloud-join.bat LICENSES/ README-WINDOWS.txt
  SuperMarket-<version>-win64.zip, .sha256, build-manifest.json

No customer data, databases, tests or git metadata are included.
"""

import argparse
import base64
import compileall
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server.version import PRODUCT_ID, VERSION  # noqa: E402

PY_VERSION = "3.13.17"
NUPKG_URL = "https://api.nuget.org/v3-flatcontainer/python/%s/python.%s.nupkg" % (PY_VERSION, PY_VERSION)
# packageHash from https://api.nuget.org/v3/catalog0/data/2026.10.21.16.02.39/python.3.13.17.json
NUPKG_SHA512_B64 = "DmZ5KLtPGVc8bNiPZRM7LgeyQ6XHFvM1tWqma7nsb4IyglbSLDop4joCdqxcam14YhH+q1JIkK0eVmKRlsaynA=="

# runtime parts we do not ship (smaller, less to patch): GUI, tests, pip, headers
DROP_PREFIXES = ("tools/include/", "tools/libs/", "tools/Lib/test/", "tools/Lib/idlelib/", "tools/Lib/tkinter/",
                 "tools/Lib/turtledemo/", "tools/Lib/ensurepip/", "tools/Lib/site-packages/", "tools/Lib/lib2to3/",
                 "tools/DLLs/_tkinter", "tools/DLLs/tcl", "tools/DLLs/tk", "tools/tcl/", "tools/Lib/pydoc_data/")
APP_FILES = ["launcher.py", "start.bat", "start-training.bat", "start-training-reset.bat", "start-lan.bat", "owner-recovery.bat", "cloud-join.bat"]
APP_DIRS = ["server", "web"]
TOOLS = ["shopctl.py", "syncctl.py", "Discover-Environment.ps1", "Read-ExcelViaCom.ps1"]
DOCS = ["USER_GUIDE.md", "SUPPORT_GUIDE.md", "SCANNER_COMPATIBILITY.md", "WINDOWS.md"]


def sha512_b64(path):
    h = hashlib.sha512()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return base64.b64encode(h.digest()).decode()


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def fetch_nupkg(dest):
    if not dest.exists():
        print("downloading", NUPKG_URL)
        with urllib.request.urlopen(NUPKG_URL, timeout=120) as r, open(dest, "wb") as fh:
            shutil.copyfileobj(r, fh)
    return dest


def git_commit():
    try:
        return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def build(nupkg, out):
    if sha512_b64(nupkg) != NUPKG_SHA512_B64:
        raise SystemExit("python package hash mismatch: refusing to build")
    if sys.version_info[:2] != (3, 13):
        raise SystemExit("build with Python 3.13 so the precompiled .pyc files match the bundled runtime")
    name = "SuperMarket-%s-win64" % VERSION
    target = out / name
    if target.exists():
        shutil.rmtree(target)
    py = target / "python"
    with zipfile.ZipFile(nupkg) as z:
        for info in z.infolist():
            n = info.filename
            if not n.startswith("tools/") or n.endswith("/") or n.startswith(DROP_PREFIXES) or "__pycache__" in n:
                continue
            dest = py / n[len("tools/"):]
            dest.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(dest, "wb") as fh:
                shutil.copyfileobj(src, fh)
    # sys.path is exactly these entries: the runtime and the program folder.
    # PYTHONPATH/PYTHONHOME or a user's other Python cannot leak in.
    (py / "python313._pth").write_text("Lib\nDLLs\n..\n", encoding="ascii")
    for f in APP_FILES:
        shutil.copy2(ROOT / f, target / f)
    for d in APP_DIRS:
        shutil.copytree(ROOT / d, target / d, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (target / "tools").mkdir()
    for f in TOOLS:
        shutil.copy2(ROOT / "tools" / f, target / "tools" / f)
    (target / "docs").mkdir()
    for f in DOCS:
        shutil.copy2(ROOT / "docs" / f, target / "docs" / f)
    lic = target / "LICENSES"
    lic.mkdir()
    shutil.copy2(py / "LICENSE.txt", lic / "Python-LICENSE.txt")
    for f in (ROOT / "web" / "fonts" / "LICENSES").iterdir():
        shutil.copy2(f, lic / ("font-" + f.name))
    shutil.copy2(ROOT / "docs" / "README-WINDOWS.txt", target / "README-WINDOWS.txt")
    # precompile (same 3.13 magic number) so the first start is fast even on a read-only Lib
    compileall.compile_dir(str(py / "Lib"), quiet=1, workers=0)
    for d in ("server",):
        compileall.compile_dir(str(target / d), quiet=1)
    manifest = {"product": PRODUCT_ID, "version": VERSION, "commit": git_commit(), "python": PY_VERSION,
                "python_source": NUPKG_URL, "python_sha512": NUPKG_SHA512_B64, "arch": "win64",
                "requires": "Windows 10 or 11, 64-bit (not yet verified on a clean PC)"}
    (target / "build-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    archive = out / (name + ".zip")
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(target.rglob("*")):
            if f.is_file():
                z.write(f, pathlib.Path(name) / f.relative_to(target))
    digest = sha256(archive)
    (out / (name + ".zip.sha256")).write_text("%s  %s\n" % (digest, archive.name), encoding="ascii")
    size = archive.stat().st_size
    print(json.dumps({"folder": str(target), "zip": str(archive), "sha256": digest, "size_mb": round(size / 1e6, 1)}, indent=2))
    return target, archive


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--nupkg", type=pathlib.Path, default=None)
    p.add_argument("--out", type=pathlib.Path, default=ROOT / "dist")
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    nupkg = a.nupkg or fetch_nupkg(a.out / ("python.%s.nupkg" % PY_VERSION))
    build(nupkg, a.out)


if __name__ == "__main__":
    main()
