import copy
import json
import unittest
from pathlib import Path
from report import summarize, timestamp

START = timestamp("2026-09-21T00:00:00Z")
END = timestamp("2026-09-28T00:00:00Z")


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.payload = json.loads(Path(__file__).with_name("sample.json").read_text())

    def test_counts_distinguish_period_from_snapshot(self):
        m = summarize(self.payload, START, END)
        self.assertEqual((m["total"], m["done"], len(m["completed"])), (8, 4, 3))
        self.assertEqual(m["total_hours"], 25.5)

    def test_start_included_end_excluded_and_timezone_normalized(self):
        issue = self.payload["issues"][0]
        issue["fields"]["worklog"] = {
            "total": 3,
            "worklogs": [
                {
                    "id": "a",
                    "started": "2026-09-21T08:00:00+08:00",
                    "timeSpentSeconds": 3600,
                },
                {
                    "id": "b",
                    "started": "2026-09-28T00:00:00Z",
                    "timeSpentSeconds": 7200,
                },
                {
                    "id": "c",
                    "started": "2026-09-20T23:59:59Z",
                    "timeSpentSeconds": 7200,
                },
            ],
        }
        self.assertEqual(
            summarize(self.payload, START, END)["issue_hours"][issue["key"]], 1
        )

    def test_rejects_incomplete_export_and_worklogs(self):
        self.payload["total"] += 1
        with self.assertRaisesRegex(ValueError, "Incomplete issue"):
            summarize(self.payload, START, END)
        self.payload["total"] -= 1
        self.payload["issues"][0]["fields"]["worklog"]["total"] += 1
        with self.assertRaisesRegex(ValueError, "Incomplete worklogs"):
            summarize(self.payload, START, END)

    def test_rejects_duplicate_issues_and_worklogs(self):
        self.payload["issues"].append(copy.deepcopy(self.payload["issues"][0]))
        self.payload["total"] += 1
        with self.assertRaisesRegex(ValueError, "Duplicate issue"):
            summarize(self.payload, START, END)
        self.payload["issues"].pop()
        self.payload["total"] -= 1
        logs = self.payload["issues"][0]["fields"]["worklog"]
        logs["worklogs"].append(copy.deepcopy(logs["worklogs"][0]))
        logs["total"] += 1
        with self.assertRaisesRegex(ValueError, "Duplicate worklog"):
            summarize(self.payload, START, END)

    def test_empty_is_valid_but_missing_worklogs_not_zero(self):
        self.assertEqual(
            summarize({"issues": [], "total": 0}, START, END)["total_hours"], 0
        )
        del self.payload["issues"][0]["fields"]["worklog"]
        with self.assertRaisesRegex(ValueError, "Incomplete worklogs"):
            summarize(self.payload, START, END)

    def test_rejects_invalid_duration_period_and_naive_time(self):
        self.payload["issues"][0]["fields"]["worklog"]["worklogs"][0][
            "timeSpentSeconds"
        ] = -1
        with self.assertRaisesRegex(ValueError, "Invalid duration"):
            summarize(self.payload, START, END)
        with self.assertRaises(ValueError):
            summarize(self.payload, END, START)
        with self.assertRaisesRegex(ValueError, "timezone"):
            timestamp("2026-09-21T00:00:00")


if __name__ == "__main__":
    unittest.main()
