import copy
import json
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader

from report import summarize, timestamp, write_pdf

START = timestamp("2026-09-21T00:00:00Z")
END = timestamp("2026-09-28T00:00:00Z")


class PdfTests(unittest.TestCase):
    def setUp(self):
        self.payload = json.loads(Path(__file__).with_name("sample.json").read_text())

    def render(self, payload):
        metrics = summarize(payload, START, END)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.pdf"
            write_pdf(payload, metrics, START, END, output)
            pages = [page.extract_text() for page in PdfReader(output).pages]
        return metrics, pages

    def test_sample_includes_unfinished_work_and_reconciled_total(self):
        metrics, pages = self.render(self.payload)
        self.assertEqual(len(pages), 2)
        self.assertIn("Time spent on tasks", pages[1])
        self.assertIn("In progress", pages[1])
        for task in metrics["logged_tasks"]:
            self.assertIn(task["key"], pages[1])
        self.assertIn("25.5", pages[1])
        for page in pages:
            self.assertIn("Synthetic demonstration", page)

    def test_long_worklog_table_continues_with_headers_and_all_tasks(self):
        template = self.payload["issues"][0]
        self.payload["issues"] = []
        for index in range(60):
            issue = copy.deepcopy(template)
            issue["key"] = f"LONG-{index:03}"
            issue["fields"]["status"]["statusCategory"]["key"] = "indeterminate"
            issue["fields"]["summary"] = (
                "Handle retry errors & preserve <literal> input in the export " * 3
            )
            self.payload["issues"].append(issue)
        self.payload["total"] = len(self.payload["issues"])
        _, pages = self.render(self.payload)
        self.assertGreater(len(pages), 3)
        body = "\n".join(pages[1:])
        for issue in self.payload["issues"]:
            self.assertEqual(body.count(issue["key"]), 1)
        for page in pages[1:]:
            self.assertIn("Hours", page)
            self.assertIn("Status", page)
        self.assertIn("<literal>", body)
        self.assertIn("360.0", body)

    def test_empty_report_has_no_invented_work(self):
        self.payload.update(issues=[], total=0)
        metrics, pages = self.render(self.payload)
        self.assertEqual(metrics["logged_tasks"], [])
        self.assertIn("No worklogs in this period.", pages[-1])
        self.assertIn("N/A", pages[0])


if __name__ == "__main__":
    unittest.main()
