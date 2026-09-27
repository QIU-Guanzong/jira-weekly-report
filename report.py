"""Generate a weekly PDF from a complete, sanitized Jira issue/worklog export."""

import argparse
import json
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from xml.sax.saxutils import escape


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timestamps must include a timezone")
    return result.astimezone(timezone.utc)


def summarize(payload, start, end):
    if start >= end:
        raise ValueError("The reporting interval must have positive length")
    issues = payload["issues"]
    if type(payload.get("total")) is not int or payload["total"] != len(issues):
        raise ValueError("Incomplete issue export: retrieve all pages first")
    seen, completed, statuses, hours, issue_hours = set(), [], Counter(), Counter(), {}
    logged_tasks = []
    for issue in issues:
        key, fields = issue["key"], issue["fields"]
        if key in seen:
            raise ValueError(f"Duplicate issue: {key}")
        seen.add(key)
        status = fields["status"]["statusCategory"]["key"]
        if status not in ("new", "indeterminate", "done"):
            raise ValueError(f"Unknown status category for {key}")
        statuses[status] += 1
        resolved = fields.get("resolutiondate")
        if status == "done" and resolved and start <= timestamp(resolved) < end:
            completed.append(
                {
                    "key": key,
                    "summary": fields["summary"],
                    "resolved": timestamp(resolved),
                }
            )
        worklogs = fields.get("worklog")
        if (
            worklogs is None
            or type(worklogs.get("total")) is not int
            or worklogs["total"] != len(worklogs.get("worklogs", []))
        ):
            raise ValueError(f"Incomplete worklogs for {key}: retrieve all pages first")
        ids, seconds = set(), 0
        for log in worklogs["worklogs"]:
            if log["id"] in ids:
                raise ValueError(f"Duplicate worklog for {key}")
            ids.add(log["id"])
            duration = log["timeSpentSeconds"]
            if (
                isinstance(duration, bool)
                or not isinstance(duration, int)
                or duration < 0
            ):
                raise ValueError(f"Invalid duration for {key}")
            if start <= timestamp(log["started"]) < end:
                seconds += duration
                # This is logged work, not inferred from assignment or lifetime totals.
                hours[timestamp(log["started"]).strftime("%Y-%m-%d")] += duration / 3600
        issue_hours[key] = seconds / 3600
        if seconds:
            logged_tasks.append(
                {
                    "key": key,
                    "summary": fields["summary"],
                    "status": status,
                    "hours": seconds / 3600,
                }
            )
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while day < end:
        hours.setdefault(day.strftime("%Y-%m-%d"), 0.0)
        day += timedelta(days=1)
    return {
        "total": len(issues),
        "done": statuses["done"],
        "statuses": dict(statuses),
        "completed": sorted(completed, key=lambda x: x["key"]),
        "hours": dict(sorted(hours.items())),
        "issue_hours": issue_hours,
        "logged_tasks": sorted(logged_tasks, key=lambda row: row["key"]),
        "total_hours": sum(issue_hours.values()),
    }


def write_pdf(payload, metrics, start, end, destination):
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
        PageBreak,
    )
    from reportlab.graphics.shapes import Drawing, Rect, String

    ink, muted, accent = "#203337", "#5B6A6C", "#B24D32"
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="TitleCustom",
            fontName="Helvetica-Bold",
            fontSize=25,
            leading=29,
            textColor=colors.HexColor(ink),
            spaceAfter=10,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SectionCustom",
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=16,
            spaceBefore=18,
            spaceAfter=9,
            textColor=colors.HexColor(ink),
        )
    )
    styles.add(
        ParagraphStyle(
            name="SmallCustom",
            fontName="Helvetica",
            fontSize=9,
            leading=13,
            textColor=colors.HexColor(muted),
        )
    )
    body = styles["BodyText"]
    body.fontSize = 10
    body.leading = 14

    def p(text, style="BodyText"):
        return Paragraph(escape(str(text)), styles[style])

    synthetic = payload.get("synthetic", False)
    story = [
        p(
            "SYNTHETIC SAMPLE | No customer data"
            if synthetic
            else "JIRA WEEKLY REPORT",
            "SmallCustom",
        ),
        Spacer(1, 12),
        p(payload.get("project", "Delivery report"), "TitleCustom"),
        p(f"{start:%d %b %Y} to {end:%d %b %Y} (end exclusive, UTC)", "SmallCustom"),
        p("Scope: " + payload["scope"], "SmallCustom"),
        Spacer(1, 18),
    ]
    percent = f"{metrics['done'] / metrics['total']:.0%}" if metrics["total"] else "N/A"
    values = [
        [
            p("COMPLETED THIS PERIOD", "SmallCustom"),
            p("SPRINT SNAPSHOT", "SmallCustom"),
            p("WORK LOGGED", "SmallCustom"),
        ],
        [
            p(str(len(metrics["completed"])), "TitleCustom"),
            p(percent, "TitleCustom"),
            p(f"{metrics['total_hours']:.1f} h", "TitleCustom"),
        ],
        [
            p("Resolved and currently Done", "SmallCustom"),
            p(f"{metrics['done']} of {metrics['total']} issues Done", "SmallCustom"),
            p("Worklog start falls in period", "SmallCustom"),
        ],
    ]
    table = Table(values, colWidths=[170, 170, 170])
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEABOVE", (0, 0), (-1, 0), 1, colors.HexColor(ink)),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.append(table)
    story.append(p("Current sprint status", "SectionCustom"))
    drawing = Drawing(510, 64)
    offset = 0
    palette = [
        ("new", "To do", "#D9DEDB"),
        ("indeterminate", "In progress", "#BBCCC4"),
        ("done", "Done", "#45695E"),
    ]
    for index, (key, label, color) in enumerate(palette):
        count = metrics["statuses"].get(key, 0)
        width = 510 * count / max(metrics["total"], 1)
        if width:
            drawing.add(
                Rect(
                    offset,
                    28,
                    width,
                    24,
                    fillColor=colors.HexColor(color),
                    strokeColor=None,
                )
            )
        drawing.add(
            String(
                index * 170,
                7,
                f"{label}: {count}",
                fontName="Helvetica",
                fontSize=10,
                fillColor=colors.HexColor(ink),
            )
        )
        offset += width
    story.append(drawing)
    story.append(p("Work logged by day", "SectionCustom"))
    drawing = Drawing(510, 115)
    hours = metrics["hours"]
    maximum = max(hours.values(), default=1) or 1
    if not any(hours.values()):
        drawing.add(
            String(
                0, 50, "No worklogs in this period.", fontName="Helvetica", fontSize=10
            )
        )
    else:
        # Include zero-work days to preserve calendar spacing.
        step = 510 / len(hours)
        for index, (day, value) in enumerate(hours.items()):
            x = index * step
            height = 70 * value / maximum
            drawing.add(
                Rect(
                    x + 8,
                    23,
                    max(step - 20, 2),
                    height,
                    fillColor=colors.HexColor(accent),
                    strokeColor=None,
                )
            )
            drawing.add(
                String(
                    x + 8,
                    height + 29,
                    f"{value:.1f} h",
                    fontName="Helvetica",
                    fontSize=9,
                    fillColor=colors.HexColor(ink),
                )
            )
            drawing.add(
                String(
                    x + 8,
                    6,
                    day[5:],
                    fontName="Helvetica",
                    fontSize=9,
                    fillColor=colors.HexColor(muted),
                )
            )
    story.append(drawing)
    story.append(p("Completed work", "SectionCustom"))
    rows = [
        [
            p("Issue", "SmallCustom"),
            p("Outcome", "SmallCustom"),
            p("This week", "SmallCustom"),
        ]
    ]
    for item in metrics["completed"]:
        rows.append(
            [
                p(item["key"]),
                p(item["summary"]),
                p(f"{metrics['issue_hours'][item['key']]:.1f} h"),
            ]
        )
    if len(rows) == 1:
        rows.append([p("-"), p("No issues resolved in this period."), p("-")])
    table = Table(rows, colWidths=[80, 355, 75], repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, 0), 0.7, colors.HexColor(ink)),
                ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#DFE4E1")),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story += [
        table,
        Spacer(1, 16),
        p("Reading this report", "SectionCustom"),
        p(
            "Sprint progress is an issue-count snapshot, not a burndown or velocity measure. Completed work uses resolution dates and current Done status. Reopened or historically completed issues require changelog data for a full transition-based account. Worklogs are attributed by start date and are not split across midnight.",
            "SmallCustom",
        ),
    ]

    story += [
        PageBreak(),
        p("Time spent on tasks", "TitleCustom"),
        p(f"{start:%d %b %Y} to {end:%d %b %Y} (end exclusive, UTC)", "SmallCustom"),
        p(
            "Every task with work logged in this period, including unfinished work. "
            "Status is the current export snapshot. Hours are rounded to one decimal.",
            "SmallCustom",
        ),
        Spacer(1, 18),
    ]
    status_labels = {"new": "To do", "indeterminate": "In progress", "done": "Done"}
    logged_rows = [
        [p(label, "SmallCustom") for label in ("Issue", "Task", "Status", "Hours")]
    ]
    for task in metrics["logged_tasks"]:
        logged_rows.append(
            [
                p(task["key"]),
                p(task["summary"]),
                p(status_labels[task["status"]]),
                p(f"{task['hours']:.1f}"),
            ]
        )
    if not metrics["logged_tasks"]:
        logged_rows.append([p("-"), p("No worklogs in this period."), p("-"), p("0.0")])
    logged_rows.append([p("Total"), p(""), p(""), p(f"{metrics['total_hours']:.1f}")])
    logged_table = Table(
        logged_rows, colWidths=[95, 265, 95, 55], repeatRows=1, hAlign="LEFT"
    )
    logged_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, 0), 0.7, colors.HexColor(ink)),
                ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#DFE4E1")),
                ("LINEABOVE", (0, -1), (-1, -1), 0.7, colors.HexColor(ink)),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story.append(logged_table)

    def footer(canvas, doc):
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor(muted))
        canvas.drawString(
            42,
            28,
            "Synthetic demonstration | No client access or deployment"
            if synthetic
            else "Generated from the supplied export",
        )
        canvas.drawRightString(552, 28, f"{doc.page}")

    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    SimpleDocTemplate(
        str(destination),
        pagesize=(595.28, 841.89),
        rightMargin=42,
        leftMargin=42,
        topMargin=36,
        bottomMargin=48,
    ).build(story, onFirstPage=footer, onLaterPages=footer)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    start, end = timestamp(args.start), timestamp(args.end)
    if (end - start).total_seconds() > 7 * 86400:
        parser.error("Weekly reports must cover at most 7 days")
    payload = json.loads(args.input.read_text())
    metrics = summarize(payload, start, end)
    write_pdf(payload, metrics, start, end, args.output)
    print(json.dumps(metrics, default=str, indent=2))


if __name__ == "__main__":
    main()
