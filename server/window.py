# Taken from the Yousef app (server/window.py, same owner); only the names differ.
"""The program's own window (owner, 29 October 2026): Super Market opens like any other Windows program, not as a tab of the browser (same as the Yousef app).

1.2.0 used pywebview (WebView2 through .NET). In the compiled program that needed stand-ins for modules Nuitka left out, and inside it
downloads (Excel, Word), printing, file pickers and WhatsApp links behaved differently from the browser. Since then the program asks Microsoft Edge,
which is part of every Windows 10 and 11 PC, for an application window instead: `msedge --app=<address>` is its own window with the
program's name and icon in the task bar, no tabs and no address bar, and the same engine the pages were built and tested on.
Nothing extra is bundled: Python standard library only.

Order of tries, each skip written to the server log:
  1. Microsoft Edge application window,
  2. Google Chrome application window (a PC without Edge),
  3. the default browser (not Windows, or neither browser found) - the behaviour of 1.1.11.

`open_ui()` starts the window and returns at once how it was shown: 'edge-app', 'chrome-app' or 'browser'. The system keeps running
when the window is closed (the mailbox and the other office PCs still need it); opening the program again shows the window again.
"""
import os
import subprocess
import sys
import webbrowser

_EDGE = (('ProgramFiles(x86)', 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
         ('ProgramFiles', 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
         ('LOCALAPPDATA', 'Microsoft', 'Edge', 'Application', 'msedge.exe'))
_CHROME = (('ProgramFiles', 'Google', 'Chrome', 'Application', 'chrome.exe'),
           ('ProgramFiles(x86)', 'Google', 'Chrome', 'Application', 'chrome.exe'),
           ('LOCALAPPDATA', 'Google', 'Chrome', 'Application', 'chrome.exe'))


def _say(log, msg):
    try:
        (log or (lambda *_: None))(msg)
    except Exception:  # noqa: BLE001 - a log line must never stop the program from opening
        pass


def _find(places):
    if sys.platform != 'win32':
        return None
    for var, *rest in places:
        base = os.environ.get(var)
        if base:
            p = os.path.join(base, *rest)
            if os.path.isfile(p):
                return p
    return None


def edge_path():
    return _find(_EDGE)


def chrome_path():
    return _find(_CHROME)


def app_command(exe, url):
    """The command line of an application window. The person's own browser profile is used on purpose: it has already passed the
    browser's first-run questions, so no welcome page or sign-in question ever covers the program."""
    return [exe, f'--app={url}', '--start-maximized', '--no-first-run', '--no-default-browser-check']


def _try_app(name, exe, url, log):
    if not exe:
        _say(log, f'window: {name} was not found for the application window')
        return False
    try:
        flags = getattr(subprocess, 'DETACHED_PROCESS', 0)       # the window does not belong to this program's (hidden) console
        subprocess.Popen(app_command(exe, url), close_fds=True, creationflags=flags,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _say(log, f'window: opened the {name} application window')
        return True
    except Exception as e:  # noqa: BLE001 - the next way is tried
        _say(log, f'window: the {name} application window could not start ({e.__class__.__name__}: {e})')
        return False


def open_ui(url, log=None):
    """Show `url` in the program's own window. Never blocks. Returns 'edge-app', 'chrome-app' or 'browser'."""
    if not os.environ.get('SM_NO_WINDOW'):
        if _try_app('Microsoft Edge', edge_path(), url, log):
            return 'edge-app'
        if _try_app('Google Chrome', chrome_path(), url, log):
            return 'chrome-app'
        _say(log, 'window: no application window is possible on this PC - opening the default browser instead')
    try:
        webbrowser.open(url)
    except Exception as e:  # noqa: BLE001
        _say(log, f'window: the browser could not be opened ({e})')
    return 'browser'
