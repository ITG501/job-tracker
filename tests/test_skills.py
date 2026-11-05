"""Skill gaps from postings: matching, picking, and the picked skills reaching CV drafts.
Runs offline: Claude and the PDF build are mocked. Uses a temp copy of data/cv_content.json."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
TMP = tempfile.mkdtemp()
shutil.copy(HERE / "data" / "cv_content.json", TMP)
os.environ["JOB_TRACKER_DATA"] = TMP
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
sys.path.insert(0, str(HERE))
import server  # noqa: E402

DESC = "We need someone for freight and procurement. " * 10


class SkillTests(unittest.TestCase):
    def setUp(self):
        with server.connect() as con:
            con.execute("DELETE FROM jobs")
            con.execute("DELETE FROM my_skills")
        self.c = server.app.test_client()

    def add_job(self, skills, company="Acme"):
        reply = {"score": 70, "fit": "good", "reason": "ok", "gaps": "", "skills": skills}
        with mock.patch.object(server, "ask_claude", return_value=reply) as ask:
            r = self.c.post("/api/jobs", json={"company": company, "role": "Buyer", "description": DESC})
        self.assertEqual(r.status_code, 200, r.json)
        return r.json["id"], ask

    def test_match_and_gaps(self):
        jid, ask = self.add_job([
            {"name": "Supplier negotiation", "match": "Supplier Negotiation"},
            {"name": "Incoterms 2020", "match": "Incoterms"},
            {"name": "SAP MM", "match": "SAP"},          # not a real CV skill, must become a gap
            {"name": "Power BI", "match": ""},           # name itself is on the CV
            {"name": "SAP MM", "match": ""},             # duplicate
        ])
        self.assertIn("Skills:", ask.call_args.args[3])
        skills = self.c.get(f"/api/jobs/{jid}").json["skills"]
        self.assertEqual([(s["name"], s["status"], s["as"]) for s in skills], [
            ("Supplier negotiation", "have", "Supplier Negotiation"),
            ("Incoterms 2020", "have", "Incoterms"),
            ("SAP MM", "new", ""),
            ("Power BI", "have", "Power BI")])

    def test_pick_decline_and_future_jobs(self):
        jid, _ = self.add_job([{"name": "SAP MM", "match": ""}, {"name": "Six Sigma", "match": ""},
                               {"name": "Kotlin", "match": ""}])
        r = self.c.post(f"/api/jobs/{jid}/skills", json={
            "add": [{"name": "SAP MM", "as": "SAP MM"}, {"name": "Six Sigma", "as": "Lean Six Sigma"}],
            "decline": ["Kotlin"]})
        self.assertEqual(r.status_code, 200)
        skills = {s["name"]: s for s in self.c.get(f"/api/jobs/{jid}").json["skills"]}
        self.assertEqual(skills["SAP MM"]["status"], "have")
        self.assertEqual(skills["Six Sigma"]["as"], "Lean Six Sigma")
        self.assertEqual(skills["Kotlin"]["status"], "not_mine")

        # a later job: picked skills are in the list Claude sees, and match on their own
        jid2, ask = self.add_job([{"name": "sap mm", "match": ""}, {"name": "kotlin", "match": ""}], "Globex")
        self.assertIn("Lean Six Sigma", ask.call_args.args[3])
        s2 = {s["name"]: s["status"] for s in self.c.get(f"/api/jobs/{jid2}").json["skills"]}
        self.assertEqual(s2, {"sap mm": "have", "kotlin": "not_mine"})

        lst = self.c.get("/api/skills").json
        self.assertEqual({a["name"] for a in lst["added"]}, {"SAP MM", "Lean Six Sigma"})
        self.assertEqual([a["name"] for a in lst["not_mine"]], ["Kotlin"])

        # undo 'not mine' -> asked again
        self.c.post("/api/skills/remove", json={"name": "kotlin"})
        s2 = {s["name"]: s["status"] for s in self.c.get(f"/api/jobs/{jid2}").json["skills"]}
        self.assertEqual(s2["kotlin"], "new")

    def test_picked_skills_reach_cv_drafts(self):
        jid, _ = self.add_job([{"name": "SAP MM", "match": ""}])
        self.c.post(f"/api/jobs/{jid}/skills", json={"add": [{"name": "SAP MM", "as": "SAP MM"}], "decline": []})
        reply = {"base": "procurement", "subtitle": "Buyer", "profile": "Procurement person.",
                 "skills": ["SAP MM", "Supplier Negotiation", "Made Up Skill"], "reason": "x"}
        with mock.patch.object(server, "ask_claude", return_value=reply) as ask, \
             mock.patch.object(server, "render_cv"):
            r = self.c.post(f"/api/jobs/{jid}/cv")
        self.assertEqual(r.status_code, 200, r.json)
        self.assertIn('"SAP MM"', ask.call_args.args[3])
        draft = self.c.get(f"/api/jobs/{jid}").json["drafts"][0]
        self.assertEqual(draft["skills"], ["SAP MM", "Supplier Negotiation"])
        with mock.patch.object(server, "render_cv"):
            r = self.c.patch(f"/api/cv/{draft['id']}", json={"subtitle": "B", "profile": "P.", "skills": ["SAP MM", "Kotlin"]})
        self.assertEqual(r.json["warnings"], ["Skill isn't on your CV yet: Kotlin"])

    def test_old_job_without_skills(self):
        with server.connect() as con:
            con.execute("INSERT INTO jobs(company, role) VALUES('Old', 'Job')")
            jid = con.execute("SELECT id FROM jobs").fetchone()[0]
        self.assertEqual(self.c.get(f"/api/jobs/{jid}").json["skills"], [])


if __name__ == "__main__":
    unittest.main()
