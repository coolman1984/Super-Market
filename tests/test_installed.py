"""The installed program (SuperMarket.exe, built like the Yousef app): data outside the
program folder, its own Edge app window, and a clean stop that only this PC can ask for."""

import http.client
import os
import pathlib
import tempfile
import threading
import unittest
from unittest import mock

import helpers  # noqa: F401
import launcher
from server import window
from server.app import serve


class InstalledProgram(unittest.TestCase):
    def test_data_lives_in_programdata_when_compiled_and_sm_home_wins(self):
        with mock.patch.object(launcher, "COMPILED", True), mock.patch.dict(os.environ, {"ProgramData": r"C:\ProgramData"}, clear=False):
            os.environ.pop("SM_HOME", None)
            self.assertEqual(launcher.home_dir(), pathlib.Path(r"C:\ProgramData") / "SuperMarket")
        with mock.patch.dict(os.environ, {"SM_HOME": "/tmp/x"}):
            self.assertEqual(launcher.home_dir(), pathlib.Path("/tmp/x"))
        with mock.patch.object(launcher, "COMPILED", False):
            os.environ.pop("SM_HOME", None)
            self.assertEqual(launcher.home_dir(), launcher.ROOT)          # the portable folder keeps its data next to it

    def test_window_is_an_edge_application_window_without_tabs(self):
        cmd = window.app_command(r"C:\Edge\msedge.exe", "http://127.0.0.1:8765/")
        self.assertIn("--app=http://127.0.0.1:8765/", cmd)
        with mock.patch.object(window, "edge_path", return_value=r"C:\Edge\msedge.exe"), mock.patch.object(window.subprocess, "Popen") as popen:
            self.assertEqual(window.open_ui("http://127.0.0.1:8765/"), "edge-app")
            self.assertIn("--app=http://127.0.0.1:8765/", popen.call_args[0][0])

    def test_window_has_its_own_profile_and_shares_nothing_with_the_persons_browser(self):
        """Review L12 (owner order 31 Oct 2026): a real program window, not the person's browser profile."""
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\Shop\AppData\Local"}):
            cmd = window.app_command(r"C:\Edge\msedge.exe", "http://127.0.0.1:8765/")
        profile = [c for c in cmd if c.startswith("--user-data-dir=")]
        self.assertEqual(len(profile), 1)
        self.assertIn("SuperMarket", profile[0])
        self.assertIn("window-profile", profile[0])
        self.assertNotIn("Microsoft", profile[0])               # never Edge's default "User Data"
        self.assertIn("--no-first-run", cmd)

    def test_stop_needs_the_key_from_the_data_folder(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        httpd = serve(pathlib.Path(tmp.name) / "data", "shop", port=0)
        self.addCleanup(httpd.app.store.close)
        self.addCleanup(httpd.server_close)
        httpd.app.stop_key = "k" * 32
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        port = httpd.server_address[1]

        def post(key):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("POST", "/api/local/stop", "{}", {"X-SM": "1", "X-SM-Stop": key, "Content-Type": "application/json"})
            status = conn.getresponse().status
            conn.close()
            return status
        self.assertEqual(post("wrong"), 403)
        self.assertTrue(t.is_alive())
        self.assertEqual(post("k" * 32), 200)
        t.join(5)
        self.assertFalse(t.is_alive())                                    # stopped cleanly


if __name__ == "__main__":
    unittest.main()
