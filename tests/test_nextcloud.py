"""Approved CVs are copied to Nextcloud. Runs offline: Nextcloud is a fake urlopen."""
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
TMP = tempfile.mkdtemp()
shutil.copy(HERE / "data" / "cv_content.json", TMP)
os.environ["JOB_TRACKER_DATA"] = TMP
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
sys.path.insert(0, str(HERE))
import server  # noqa: E402

NC = dict(NEXTCLOUD_URL="https://cloud.example", NEXTCLOUD_USER="ivan",
          NEXTCLOUD_APP_PASSWORD="app-pass", NEXTCLOUD_CV_FOLDER="Job search/Generated CVs")


class FakeResponse:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeNextcloud:
    """Records each request; answers from `codes`, default 201 (or 405 for MKCOL on existing)."""

    def __init__(self, codes=None):
        self.calls = []
        self.codes = codes or {}

    def __call__(self, req, timeout=None):
        self.calls.append((req.get_method(), req.full_url, dict(req.header_items())))
        status = self.codes.get(req.get_method(), 201)
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, "err", {}, None)
        return FakeResponse(status)


class NextcloudTests(unittest.TestCase):
    def setUp(self):
        with server.connect() as con:
            con.execute("DELETE FROM cv_drafts")
            con.execute("DELETE FROM jobs")
            cur = con.execute("INSERT INTO jobs(company, role) VALUES('Acme & Co', 'Buyer')")
            self.job_id = cur.lastrowid
            cur = con.execute("INSERT INTO cv_drafts(job_id, base, subtitle, profile, skills, reason, warnings, created) "
                              "VALUES(?,?,?,?,?,?,?,?)", (self.job_id, "operations", "s", "p", "[]", "r", "[]", "now"))
            self.draft_id = cur.lastrowid
            con.commit()
        self.pdf = server.CV_DIR / f"{self.draft_id}.pdf"
        server.CV_DIR.mkdir(exist_ok=True)
        self.pdf.write_bytes(b"%PDF-1.4 test")
        self.c = server.app.test_client()

    def tearDown(self):
        self.pdf.unlink(missing_ok=True)

    def approved(self):
        with server.connect() as con:
            return con.execute("SELECT approved FROM cv_drafts WHERE id = ?", (self.draft_id,)).fetchone()["approved"]

    def test_save_creates_folders_then_uploads(self):
        fake = FakeNextcloud()
        with mock.patch.multiple(server, **NC), mock.patch("urllib.request.urlopen", fake):
            rel = server.save_cv_to_nextcloud(self.pdf, "x.pdf")
        methods = [(m, u.split("/remote.php/dav/files/ivan/")[1]) for m, u, _ in fake.calls]
        self.assertEqual(methods, [("MKCOL", "Job%20search"), ("MKCOL", "Job%20search/Generated%20CVs"),
                                   ("PUT", "Job%20search/Generated%20CVs/x.pdf")])
        self.assertEqual(rel, "Job search/Generated CVs/x.pdf")
        headers = fake.calls[0][2]
        self.assertEqual(headers["User-agent"], "job-tracker")
        self.assertTrue(headers["Authorization"].startswith("Basic "))

    def test_existing_folder_is_fine(self):
        fake = FakeNextcloud({"MKCOL": 405})
        with mock.patch.multiple(server, **NC), mock.patch("urllib.request.urlopen", fake):
            server.save_cv_to_nextcloud(self.pdf, "x.pdf")
        self.assertEqual(fake.calls[-1][0], "PUT")

    def test_approve_saves_and_approves(self):
        fake = FakeNextcloud()
        with mock.patch.multiple(server, **NC), mock.patch("urllib.request.urlopen", fake):
            r = self.c.post(f"/api/cv/{self.draft_id}/approve")
        self.assertEqual(r.status_code, 200, r.json)
        self.assertIn("Ivan Bogoev CV - Acme & Co - ", r.json["saved"])
        self.assertTrue(self.approved())

    def test_failed_upload_leaves_draft_unapproved(self):
        fake = FakeNextcloud({"PUT": 507})
        with mock.patch.multiple(server, **NC), mock.patch("urllib.request.urlopen", fake):
            r = self.c.post(f"/api/cv/{self.draft_id}/approve")
        self.assertEqual(r.status_code, 502)
        self.assertIn("HTTP 507", r.json["error"])
        self.assertFalse(self.approved())

    def test_unreachable_nextcloud_leaves_draft_unapproved(self):
        def boom(req, timeout=None):
            raise urllib.error.URLError("no route")
        with mock.patch.multiple(server, **NC), mock.patch("urllib.request.urlopen", boom):
            r = self.c.post(f"/api/cv/{self.draft_id}/approve")
        self.assertEqual(r.status_code, 502)
        self.assertFalse(self.approved())

    def test_not_configured_only_approves(self):
        fake = FakeNextcloud()
        with mock.patch.multiple(server, NEXTCLOUD_URL="", NEXTCLOUD_USER="", NEXTCLOUD_APP_PASSWORD=""), \
                mock.patch("urllib.request.urlopen", fake):
            r = self.c.post(f"/api/cv/{self.draft_id}/approve")
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json["saved"])
        self.assertEqual(fake.calls, [])
        self.assertTrue(self.approved())


if __name__ == "__main__":
    unittest.main()
