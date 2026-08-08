"""Generate a safe static HTML review report."""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path
from typing import Any


def _escape(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _joined(job: dict[str, Any], field: str) -> str:
    return ", ".join(job.get(field, [])) or "None detected"


def _card(job: dict[str, Any]) -> str:
    posted = _escape((job.get("date_posted") or "Unknown")[:10])
    url = _escape(job.get("apply_url"))
    new_badge = '<span class="badge">New</span>' if job.get("is_new") else ""
    active = "Active" if job.get("is_active") else f"Closed {_escape((job.get('closed_at') or 'date unknown')[:10])}"
    return f"""
    <article class="job">
      <div class="score">{float(job['priority_score']):.1f}</div>
      <div><h3>#{int(job['id'])} {_escape(job['company'])} - {_escape(job['title'])} {new_badge}</h3>
      <p class="meta">{_escape(job['location'])} · Posted {posted} · {active}</p>
      <div class="components">
        <span>Fit <b>{float(job['fit_score']):.1f}</b></span>
        <span>Competitive <b>{float(job['competitiveness_score']):.1f}</b></span>
        <span>Preference <b>{float(job['preference_score']):.1f}</b></span>
        <span>Recency <b>{float(job['recency_score']):.1f}</b></span>
      </div>
      <p><strong>Category:</strong> {_escape(job.get('detected_category') or 'Uncategorized')} · <strong>Seniority:</strong> {_escape(job.get('detected_seniority') or 'Unspecified')} · <strong>Experience:</strong> {_escape(job.get('required_experience_years') if job.get('required_experience_years') is not None else 'Unknown')}</p>
      <p><strong>Matching required:</strong> {_escape(_joined(job, 'matching_required_skills'))}</p>
      <p><strong>Matching preferred:</strong> {_escape(_joined(job, 'matching_preferred_skills'))}</p>
      <p><strong>Other profile matches:</strong> {_escape(_joined(job, 'matching_skills'))}</p>
      <p><strong>Missing required:</strong> {_escape(_joined(job, 'missing_required_skills'))}</p>
      <p><strong>Positive:</strong> {_escape(_joined(job, 'positive_reasons'))}</p>
      <p><strong>Negative:</strong> {_escape(_joined(job, 'negative_reasons'))}</p>
      <p><strong>Eligibility:</strong> {_escape(job.get('defense_eligibility_status'))} · <strong>Citizenship:</strong> {_escape(job.get('citizenship_requirement'))} · <strong>Export control:</strong> {_escape(job.get('export_control_requirement'))} · <strong>Clearance:</strong> {_escape(job.get('security_clearance_requirement'))} {_escape(job.get('required_clearance_level') or '')}</p>
      <p><strong>Eligibility reasons:</strong> {_escape(_joined(job, 'defense_eligibility_reasons'))}</p>
      <p><strong>Evidence:</strong> {_escape(_joined(job, 'eligibility_evidence_snippets'))}</p>
      <p><strong>Review:</strong> {_escape(job['relevance'])}{' - ' + _escape(job['review_note']) if job['review_note'] else ''} · <strong>Application:</strong> {_escape(job['status'])}</p>
      <p><a href="{url}" rel="noopener noreferrer">Open direct application page</a> · Review with <code>python app.py review {int(job['id'])} possible</code></p></div>
    </article>"""


def generate_report(
    jobs: list[dict[str, Any]], output_path: Path, *, inactive_jobs: list[dict[str, Any]] | None = None,
    follow_ups: list[dict[str, Any]] | None = None,
) -> None:
    reviewed = [job for job in jobs if job["relevance"] in {"poor match", "irrelevant"}]
    ineligible = [job for job in jobs if job.get("defense_eligibility_status", "").startswith("ineligible_")]
    reviewable = [job for job in jobs if job["relevance"] not in {"poor match", "irrelevant"} and job not in ineligible]
    sections: list[tuple[str, list[dict[str, Any]]]] = [
        ("Apply immediately", [job for job in reviewable if job["recommendation"] == "Apply immediately"]),
        ("Good matches", [job for job in reviewable if job["recommendation"] == "Good match"]),
        ("Stretch applications", [job for job in reviewable if job["recommendation"] == "Stretch application"]),
        ("Manual eligibility review", [job for job in reviewable if job["recommendation"] == "Manual eligibility review"]),
        ("Reviewed as poor or irrelevant", reviewed),
        ("Ineligible - citizenship or security clearance", ineligible),
        ("Recently closed jobs", sorted(inactive_jobs or [], key=lambda job: job.get("closed_at") or "", reverse=True)[:50]),
    ]
    follow_ups = follow_ups or []
    follow_sections = [
        ("Due now", [item for item in follow_ups if item.get("due")]),
        ("Upcoming", [item for item in follow_ups if item.get("category") == "upcoming"]),
        ("No contact available", [item for item in follow_ups if item.get("business_days_elapsed") is not None and not item.get("best_contact")]),
        ("Already followed up", [item for item in follow_ups if int(item.get("follow_up_count") or 0) > 0]),
    ]
    follow_body = "".join(
        f"<h3>{_escape(title)} ({len(items)})</h3>" + ("".join(
            f"<article class='job'><div class='score'>{float(item['follow_up_score']):.1f}</div><div>"
            f"<h3>#{int(item['id'])} {_escape(item['company'])} - {_escape(item['title'])}</h3>"
            f"<p>{_escape(item['recommendation'])}</p><p>{_escape(item['reason'])}</p></div></article>"
            for item in items) or "<p>No applications in this group.</p>")
        for title, items in follow_sections
    )
    body = "".join(
        f"<section><h2>{_escape(title)} ({len(items)})</h2>{''.join(_card(job) for job in items) or '<p>No jobs in this group.</p>'}</section>"
        for title, items in sections
    )
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Job Ranker Report</title><style>
body{{font:16px/1.5 system-ui,sans-serif;max-width:1100px;margin:auto;padding:24px;color:#18202a;background:#f6f8fa}}
h1,h2,h3{{line-height:1.2}} section{{margin:36px 0}} .job{{display:grid;grid-template-columns:72px 1fr;gap:18px;background:white;border:1px solid #d8dee4;border-radius:8px;padding:18px;margin:12px 0}}
.score{{font-size:28px;font-weight:700;color:#17613a}} .meta{{color:#59636e}} .components{{display:flex;flex-wrap:wrap;gap:12px}} .components span,.badge{{background:#eaf2ff;border-radius:5px;padding:3px 7px}} .badge{{font-size:13px}} a{{color:#075cc8}} p{{margin:.5em 0}} code{{background:#eef0f2;padding:2px 4px}}
</style></head><body><h1>Job Ranker</h1><p>Generated {_escape(datetime.now().astimezone().strftime('%Y-%m-%d %H:%M %Z'))}. Active jobs are shown by default. Scores are deterministic ranking heuristics, not offer probabilities.</p><section><h2>Follow-ups</h2><p>Decision support only; no communication is sent.</p>{follow_body}</section>{body}</body></html>"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(document, encoding="utf-8")
