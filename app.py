"""Command-line entry point for Job Ranker."""

from __future__ import annotations

import argparse
import csv
import logging
import sys
import time
from datetime import date, datetime, time as datetime_time, timezone
from pathlib import Path
from typing import Any

import requests

from collectors import AshbyCollector, GreenhouseCollector, LeverCollector
from config_loader import load_configuration
from ranking.scorer import ANALYSIS_VERSION, score_job
from ranking.posting_health import freshness_score, repost_risk
from ranking.evidence import extract_requirements, map_evidence
from ranking.company import saturation
from ranking.analytics import summarize, historical_conversion
from ranking.prep import prep_topics
from ranking.priority import calculate_priority
from config_loader import load_yaml
from ranking.follow_up import add_business_days, rank_follow_ups
from reports.html_report import generate_report
from storage.database import (Database, VALID_CONTACT_TYPES, VALID_FOLLOW_UP_METHODS,
                              VALID_REVIEWS, VALID_STATUSES)

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "config"
DEFAULT_DATABASE = ROOT / "data" / "jobs.db"
DEFAULT_REPORT = ROOT / "data" / "report.html"
DEFAULT_CALIBRATION = ROOT / "data" / "calibration.csv"
COLLECTORS = {"greenhouse": GreenhouseCollector, "lever": LeverCollector, "ashby": AshbyCollector}
ACTIONABLE_APPLICATION_STATUSES = {"not reviewed", "saved"}


def contextualize_priority(database: Database, job_id: int, job: Any, score: Any,
                           profile: dict[str, Any], preferences: dict[str, Any], *,
                           analytics_rows: list[dict[str, Any]] | None = None,
                           company_rows: list[dict[str, Any]] | None = None,
                           application_effort: int | None = None,
                           observation_counts: tuple[int, int, int] | None = None) -> Any:
    history = historical_conversion(analytics_rows if analytics_rows is not None else database.analytics_rows(), score.role_family,
                                    minimum_sample=int(preferences.get("analytics", {}).get("minimum_sample", 5)))
    company_health = saturation(company_rows if company_rows is not None else database.company_applications(job.company), preferences.get("company_saturation", {}))
    saturation_value = {"low": 85.0, "moderate": 55.0, "high": 25.0}[company_health["level"]]
    if observation_counts is None:
        with database.connect() as connection:
            application = connection.execute("SELECT estimated_effort_minutes FROM application_status WHERE job_id=?", (job_id,)).fetchone()
            observations = connection.execute("SELECT count(*),sum(reopened),sum(description_changed) FROM job_observations WHERE job_id=?", (job_id,)).fetchone()
        effort = application[0] if application else None
    else:
        effort = application_effort
        observations = observation_counts
    effort_value = None if effort is None else 90.0 if effort <= 15 else 70.0 if effort <= 30 else 45.0 if effort <= 60 else 20.0
    health_value = 50.0
    if observations and observations[0]:
        discovered = job.date_discovered if job.date_discovered.tzinfo else job.date_discovered.replace(tzinfo=timezone.utc)
        observed_age = max(0.0, (datetime.now(timezone.utc) - discovered).total_seconds() / 86400)
        risk, _ = repost_risk(age_days=observed_age, reopened_count=observations[1] or 0,
                              times_seen=observations[0], description_changes=observations[2] or 0,
                              thresholds=preferences.get("posting_health", {}))
        health_value = {"low": 85.0, "moderate": 55.0, "high": 20.0}[risk]
    priority, factors = calculate_priority(
        overall_score=score.overall_score, eligibility=score.eligibility_status,
        role_weight=profile.get("target_role_weights", {}).get(score.role_family),
        freshness=freshness_score(job.date_posted, preferences.get("posting_health", {}))[0],
        historical_conversion=history, posting_health=health_value,
        company_saturation=saturation_value, application_effort=effort_value,
        config=preferences.get("priority", {}),
    )
    return score.model_copy(update={"priority_score": priority, "priority_factors": factors})


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Discover and rank entry-level technical jobs.")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db", help="Create or migrate the local SQLite database")
    scan_command = commands.add_parser("scan", help="Fetch, score, and save configured job boards")
    scan_command.add_argument("--force-detail-refresh", action="store_true")
    scan_command.add_argument("--detail-timeout", type=float, default=15.0)
    scan_command.add_argument("--detail-workers", type=int, default=10)
    scan_command.add_argument("--detail-retries", type=int, choices=range(0, 3), default=2)
    scan_command.add_argument("--detail-retry-interval", type=float, default=3600.0)
    scan_command.add_argument("--detail-board-timeout", type=float, default=120.0)
    commands.add_parser("rescore", help="Re-evaluate every stored job, including its overall score")
    commands.add_parser("reanalyze", help="Refresh derived v2 intelligence without rescanning or changing overall scores")
    listing = commands.add_parser("list", help="List saved jobs by priority")
    listing.add_argument("--minimum-score", type=float, default=0)
    state = listing.add_mutually_exclusive_group()
    state.add_argument("--active", action="store_true", help="Show active jobs (default)")
    state.add_argument("--inactive", action="store_true", help="Show inactive jobs")
    listing.add_argument("--new", action="store_true", dest="new_only")
    listing.add_argument("--company")
    listing.add_argument("--category")
    listing.add_argument("--recommendation")
    listing.add_argument("--limit", type=int, default=50)
    eligibility = listing.add_mutually_exclusive_group()
    eligibility.add_argument("--eligible", action="store_const", const="eligible", dest="eligibility")
    eligibility.add_argument("--manual-eligibility-review", action="store_const", const="manual_review", dest="eligibility")
    eligibility.add_argument("--ineligible", action="store_const", const="ineligible", dest="eligibility")
    top = commands.add_parser("top", help="Show today's recommended applications in detail")
    top.add_argument("--limit", type=positive_int, default=5)
    ranked = commands.add_parser("ranked", help="Show the globally highest-ranked actionable jobs")
    ranked.add_argument("--limit", type=positive_int, default=5)
    show = commands.add_parser("show", help="Show a complete scoring breakdown")
    show.add_argument("job_id", type=int)
    package = commands.add_parser("package", help="Build a deterministic application package")
    package.add_argument("job_id", type=int)
    effort = commands.add_parser("set-effort", help="Set estimated application minutes")
    effort.add_argument("job_id", type=int); effort.add_argument("minutes", type=positive_int)
    commands.add_parser("analytics", help="Show application funnel analytics")
    prep = commands.add_parser("prep", help="Generate deterministic interview preparation")
    prep.add_argument("job_id", type=int)
    answers = commands.add_parser("answers", help="List local application-answer keys")
    answers.add_argument("--get", dest="answer_key", help="Print one answer value")
    report = commands.add_parser("report", help="Write a local static HTML report")
    report.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    status = commands.add_parser("update-status", help="Update application workflow status")
    status.add_argument("job_id", type=int)
    status.add_argument("status", help=f"One of: {', '.join(sorted(VALID_STATUSES))}")
    status.add_argument("--date", type=date.fromisoformat, help="Original application date (YYYY-MM-DD)")
    status.add_argument("--reason", help="Structured reason when status is skipped")
    suppression = status.add_mutually_exclusive_group()
    suppression.add_argument("--do-not-follow-up", action="store_true")
    suppression.add_argument("--allow-follow-up", action="store_true")
    applications = commands.add_parser("applications", help="List jobs with changed application statuses")
    applications.add_argument("--status", choices=sorted(VALID_STATUSES - {"not reviewed"}))
    applications.add_argument("--company")
    contact = commands.add_parser("add-contact", help="Add a manually supplied application contact")
    contact.add_argument("job_id", type=int)
    contact.add_argument("--name", required=True)
    contact.add_argument("--type", required=True, choices=sorted(VALID_CONTACT_TYPES))
    contact.add_argument("--role")
    contact.add_argument("--email")
    contact.add_argument("--profile-url")
    contact.add_argument("--source")
    contact.add_argument("--note", default="")
    contact.add_argument("--verified", action="store_true")
    follow = commands.add_parser("follow-ups", help="Show deterministic follow-up recommendations")
    follow.add_argument("--all", action="store_true")
    follow.add_argument("--due", action="store_true")
    follow.add_argument("--limit", type=int, default=20)
    follow.add_argument("--company")
    record = commands.add_parser("record-follow-up", help="Record a completed follow-up")
    record.add_argument("job_id", type=int)
    record.add_argument("--contact", type=int)
    record.add_argument("--method", choices=sorted(VALID_FOLLOW_UP_METHODS), default="other")
    record.add_argument("--note", default="")
    record.add_argument("--date", type=date.fromisoformat)
    daily_command = commands.add_parser("daily", help="Show today's application and follow-up priorities")
    daily_command.add_argument("--target", type=int, default=5)
    review = commands.add_parser("review", help="Record ranking relevance feedback")
    review.add_argument("job_id", type=int)
    review.add_argument("relevance", help=f"One of: {', '.join(sorted(VALID_REVIEWS))}")
    review.add_argument("--note", default="")
    export = commands.add_parser("export-calibration", help="Export active jobs scoring at least 65")
    export.add_argument("--output", type=Path, default=DEFAULT_CALIBRATION)
    return parser


def scan(database: Database, config_dir: Path, *, force_detail_refresh: bool = False,
         detail_timeout: float = 15.0, detail_workers: int = 10, detail_retries: int = 2,
         detail_retry_interval: float = 3600.0, detail_board_timeout: float = 120.0) -> int:
    profile, preferences, company_config = load_configuration(config_dir)
    enabled = [company for company in company_config.get("companies", []) if company.get("enabled", False)]
    database.initialize()
    scan_id = database.start_scan(len(enabled))
    fetched = saved = updated = errors = succeeded = newly_inactive = partial = 0
    aggregate_stats = {"detail_requests": 0, "cached_reused": 0, "retries": 0, "timeouts": 0}
    started = time.monotonic()
    if not enabled:
        print("No companies are enabled. Edit config/companies.yaml and set enabled: true.")
    for company in enabled:
        source = str(company.get("source", "")).lower()
        company_name = str(company.get("name", "Unknown"))
        collector_type = COLLECTORS.get(source)
        if collector_type is None:
            logging.error("Unknown source %r for %s", source, company_name)
            errors += 1
            continue
        try:
            if source == "greenhouse":
                collector = collector_type(
                    timeout=detail_timeout, retries=detail_retries, workers=detail_workers,
                    retry_interval=detail_retry_interval, force_detail_refresh=force_detail_refresh,
                    board_detail_timeout=detail_board_timeout,
                    cached_details=database.greenhouse_detail_cache(company_name),
                )
            else:
                collector = collector_type()
            jobs = collector.collect(company_name, str(company.get("identifier", "")))
            stats = getattr(collector, "stats", {})
            for key in aggregate_stats:
                aggregate_stats[key] += int(stats.get(key, 0))
            partial += int(stats.get("incomplete_details", 0) > 0)
            fetched += len(jobs)
            seen_job_ids: set[int] = set()
            company_saved = company_updated = 0
            contextual_jobs: list[tuple[int, Any, Any]] = []
            with database.connect() as connection:
                for job in jobs:
                    job_id, created = database.upsert_job(job, scan_id=scan_id, connection=connection)
                    seen_job_ids.add(job_id)
                    scored = score_job(job, profile, preferences)
                    database.save_score(job_id, scored, connection=connection)
                    contextual_jobs.append((job_id, job, scored))
                    company_saved += int(created)
                    company_updated += int(not created)
                company_inactive = database.reconcile_company_scan(
                    company_name, source, seen_job_ids, connection=connection
                )
            for job_id, job, scored in contextual_jobs:
                database.save_score(job_id, contextualize_priority(database, job_id, job, scored, profile, preferences))
            saved += company_saved
            updated += company_updated
            newly_inactive += company_inactive
            succeeded += 1
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            logging.error("Could not scan %s (%s): %s", company_name, source, exc)
            errors += 1
        except KeyboardInterrupt:
            active_70 = database.count_active_above(70)
            new_70 = database.count_active_above(70, first_seen_scan_id=scan_id)
            database.finish_scan(
                scan_id, fetched=fetched, saved=saved, updated=updated, errors=errors,
                succeeded=succeeded, newly_inactive=newly_inactive, active_70=active_70,
                new_70=new_70, outcome="interrupted", partial=partial,
            )
            print("Scan interrupted; pending requests cancelled. Completed company scans were preserved.", file=sys.stderr)
            return 130
    active_70 = database.count_active_above(70)
    new_70 = database.count_active_above(70, first_seen_scan_id=scan_id)
    database.finish_scan(
        scan_id, fetched=fetched, saved=saved, updated=updated, errors=errors,
        succeeded=succeeded, newly_inactive=newly_inactive, active_70=active_70, new_70=new_70,
        outcome="partial" if errors or partial else "success", partial=partial,
    )
    summary = {
        "Companies attempted": len(enabled), "Companies successfully scanned": succeeded,
        "Companies failed": errors, "Jobs fetched": fetched, "New jobs": saved,
        "Updated jobs": updated, "Jobs newly marked inactive": newly_inactive,
        "Active jobs scoring at least 70": active_70,
        "Newly discovered jobs scoring at least 70": new_70,
        "Greenhouse detail requests": aggregate_stats["detail_requests"],
        "Greenhouse cached details reused": aggregate_stats["cached_reused"],
        "HTTP retries": aggregate_stats["retries"], "HTTP timeouts": aggregate_stats["timeouts"],
        "Duration seconds": f"{time.monotonic() - started:.2f}",
    }
    print("Scan complete:")
    for label, value in summary.items():
        print(f"  {label}: {value}")
    return 0 if errors == 0 else 1


def reanalyze(database: Database, config_dir: Path) -> int:
    """Refresh deterministic derived intelligence without external I/O.

    The legacy overall score is deliberately retained. Application priority is
    recalculated from that stable score plus current contextual factors.
    """
    profile, preferences, _ = load_configuration(config_dir)
    stored = database.all_jobs()
    with database.connect() as connection:
        previous_overall = dict(connection.execute("SELECT job_id,overall_score FROM job_scores"))
    first_pass = []
    for job_id, job in stored:
        scored = score_job(job, profile, preferences)
        if job_id in previous_overall:
            scored = scored.model_copy(update={"overall_score": previous_overall[job_id]})
        first_pass.append((job_id, job, scored))
    # Persist classifications before computing historical conversion, so every
    # contextual score sees one coherent analysis version.
    with database.connect() as connection:
        for job_id, _, scored in first_pass:
            database.save_score(job_id, scored, connection=connection)
    analytics_rows = database.analytics_rows()
    companies: dict[str, list[dict[str, Any]]] = {}
    for row in analytics_rows:
        companies.setdefault(str(row["company"]).lower(), []).append(row)
    with database.connect() as connection:
        context = {
            int(row["job_id"]): (row["estimated_effort_minutes"],
                (int(row["times_seen"] or 0), int(row["reopened"] or 0), int(row["changed"] or 0)))
            for row in connection.execute(
                """SELECT a.job_id,a.estimated_effort_minutes,count(o.id) times_seen,
                          COALESCE(sum(o.reopened),0) reopened,COALESCE(sum(o.description_changed),0) changed
                   FROM application_status a LEFT JOIN job_observations o ON o.job_id=a.job_id
                   GROUP BY a.job_id"""
            )
        }
    with database.connect() as connection:
        for job_id, job, scored in first_pass:
            effort, observations = context.get(job_id, (None, (0, 0, 0)))
            scored = contextualize_priority(
                database, job_id, job, scored, profile, preferences,
                analytics_rows=analytics_rows, company_rows=companies.get(job.company.lower(), []),
                application_effort=effort, observation_counts=observations,
            )
            database.save_score(job_id, scored, connection=connection)
    return len(stored)


def list_jobs(database: Database, args: argparse.Namespace) -> None:
    rows = database.list_ranked_jobs(
        args.minimum_score, active=not args.inactive, new_only=args.new_only,
        company=args.company, category=args.category, recommendation=args.recommendation,
        limit=args.limit, eligibility=args.eligibility or "not_ineligible",
    )
    if not rows:
        print("No scored jobs found.")
        return
    print(f"{'ID':>5}  {'Pri':>5} {'Fit':>5} {'Comp':>5} {'Pref':>5} {'Rec':>5}  {'Eligibility':<24} Company - Title")
    for row in rows:
        print(f"{row['id']:>5}  {row['priority_score']:>5.1f} {row['fit_score']:>5.1f} {row['competitiveness_score']:>5.1f} {row['preference_score']:>5.1f} {row['recency_score']:>5.1f}  {row['defense_eligibility_status']:<24} {row['company']} - {row['title']}")


def get_daily_recommendations(database: Database, limit: int = 5) -> list[dict[str, Any]]:
    """Return today's application recommendations in their display order."""
    jobs = database.list_ranked_jobs(active=True, limit=None, eligibility="not_ineligible")
    return [job for job in jobs if job["status"] in ACTIONABLE_APPLICATION_STATUSES
            and job["relevance"] == "unreviewed"
            and job["defense_eligibility_status"] != "manual_review"][:limit]


def render_detailed_jobs(rows: list[dict[str, Any]]) -> None:
    total = len(rows)
    for rank, row in enumerate(rows, start=1):
        if rank > 1:
            print()
        print(f"===== #{row['id']} | Rank {rank}/{total} =====")
        print()
        show_job(row)


def top_jobs(database: Database, limit: int) -> None:
    render_detailed_jobs(get_daily_recommendations(database, limit))


def ranked_jobs(database: Database, limit: int) -> None:
    rows = database.list_ranked_jobs(
        active=True, limit=limit, eligibility="not_ineligible",
        statuses=ACTIONABLE_APPLICATION_STATUSES,
    )
    render_detailed_jobs(rows)


def list_applications(database: Database, args: argparse.Namespace) -> None:
    rows = database.list_applications(status=args.status, company=args.company)
    if not rows:
        print("No applications found.")
        return
    for row in rows:
        print(f"Job ID: {row['id']}")
        print(f"  Company: {row['company']}")
        print(f"  Job title: {row['title']}")
        print(f"  Application status: {row['status']}")
        print(f"  Applied date: {(row['applied_at'] or 'Unknown')[:10]}")
        print(f"  Last status update: {(row['status_updated_at'] or 'Unavailable')[:10]}")
        print(f"  Posting state: {'active' if row['is_active'] else 'inactive'}")


def _follow_up_settings(config_dir: Path) -> dict[str, Any]:
    _, preferences, _ = load_configuration(config_dir)
    return preferences.get("follow_up", {})


def follow_up_rows(database: Database, config_dir: Path, *, company: str | None = None,
                   include_unapplied: bool = False) -> list[dict[str, Any]]:
    return rank_follow_ups(database.follow_up_candidates(company, include_unapplied), _follow_up_settings(config_dir))


def print_follow_ups(rows: list[dict[str, Any]], *, show_all: bool = False,
                     due_only: bool = False, limit: int | None = 20) -> None:
    if due_only or not show_all:
        rows = [row for row in rows if row["due"]]
    if limit is not None:
        rows = rows[:limit]
    if not rows:
        print("No follow-ups are currently due.")
        return
    for row in rows:
        contact = row.get("best_contact")
        contact_text = f"{contact['name']} ({contact['contact_type']})" if contact else "None"
        elapsed = row["business_days_elapsed"] if row["business_days_elapsed"] is not None else "unknown"
        print(f"#{row['id']} | {row['company']} - {row['title']}")
        print(f"  Applied: {(row.get('applied_at') or 'unknown')[:10]} | Business days: {elapsed} | Priority: {row['priority_score']:.1f} | Follow-up: {row['follow_up_score']:.1f}")
        print(f"  Status: {row['status']} | Posting: {'active' if row['is_active'] else 'inactive'} | Previous follow-ups: {row['follow_up_count']} | Contact: {contact_text}")
        print(f"  Recommendation: {row['recommendation']}")
        print(f"  Reason: {row['reason']}")


def daily(database: Database, config_dir: Path, target: int = 5) -> None:
    submit = get_daily_recommendations(database, target)
    followups = [row for row in follow_up_rows(database, config_dir) if row["due"]]
    candidates = follow_up_rows(database, config_dir, include_unapplied=True)
    attention = [row for row in candidates if row["category"] in {"attention", "recruiter_screen"}
                 or (row["status"] in {"applied", "no response"} and not row["is_active"])
                 or row["defense_eligibility_status"] == "manual_review"]
    print("Applications to submit today")
    if not submit:
        print("  None currently selected.")
    for job in submit:
        print(f"  #{job['id']} | {job['priority_score']:.1f} | {job['company']} - {job['title']}")
    print("\nFollow-ups worth doing today")
    print_follow_ups(followups, due_only=True, limit=10)
    print("\nApplications needing status attention")
    if not attention:
        print("  None currently identified.")
    for row in attention[:10]:
        reason = "Manual eligibility review is unresolved." if row["defense_eligibility_status"] == "manual_review" else row["reason"]
        print(f"  #{row['id']} | {row['company']} - {row['title']} | {reason}")


def show_job(job: dict[str, Any]) -> None:
    def joined(name: str) -> str:
        return ", ".join(job.get(name, [])) or "None detected"

    description = " ".join(job["description"].split())
    summary = description[:800] + ("..." if len(description) > 800 else "")
    print(f"Job {job['id']}: {job['company']} - {job['title']}")
    print(f"Location: {job['location']} | Active: {'yes' if job['is_active'] else 'no'} | New: {'yes' if job['is_new'] else 'no'}")
    print(f"Application status: {job['status']} | Relevance review: {job['relevance']}")
    if job["status"] in {"recruiter screen", "technical interview", "final interview"}:
        print(f"Interview stage detected. Run: python app.py prep {job['id']}")
    print(f"Applied: {(job.get('applied_at') or 'Unknown')[:10]} | Follow-ups: {job.get('follow_up_count', 0)} | Last follow-up: {(job.get('last_follow_up_at') or 'Never')[:10]}")
    if job["review_note"]:
        print(f"Review note: {job['review_note']}")
    print(f"Discovered: {job['date_discovered']} | Last seen: {job['last_seen_at'] or 'Unknown'} | Posted: {job['date_posted'] or 'Unknown'}")
    freshness, freshness_band = freshness_score(job.get("date_posted"))
    discovered = datetime.fromisoformat(job["date_discovered"])
    discovered = discovered if discovered.tzinfo else discovered.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (datetime.now(timezone.utc) - discovered).total_seconds() / 86400)
    risk, risk_reasons = repost_risk(age_days=age_days, reopened_count=job.get("reopened_count", 0),
                                     times_seen=job.get("times_seen", 1),
                                     description_changes=job.get("description_changes", 0))
    print("Posting health")
    print(f"  Times observed: {job.get('times_seen', 1)} | Description changes: {job.get('description_changes', 0)}")
    print(f"  Freshness: {freshness:.1f} ({freshness_band}) | Repost risk: {risk.upper()}")
    for reason in risk_reasons:
        print(f"  - {reason}")
    if job.get("reopened_at"):
        print("REOPENED POSITION")
        print(f"  Previous application: {(job.get('applied_at') or 'Unknown')[:10]}")
        print(f"  Previous result: {job.get('previous_result') or 'Unknown'}")
        print(f"  Reopened: {job['reopened_at'][:10]}")
    print(f"Technical fit score: {job.get('overall_score', job['priority_score']):.1f}")
    print(f"Application priority: {job['priority_score']:.1f}")
    print("Priority factors")
    for factor in job.get("priority_factors", []):
        value = "unknown" if factor.get("value") is None else factor.get("value")
        print(f"  {factor['factor'].replace('_', ' ').title():<24} {value} ({factor['effect']})")
    print(f"Role family: {job.get('role_family', 'other')}" + (f" / {job['role_subfamily']}" if job.get('role_subfamily') else ""))
    print(f"Eligibility: {job.get('eligibility_status', 'unknown').upper()}")
    for reason in job.get("eligibility_reasons", []):
        print(f"  - {reason['message']}")
    print(f"Recommendation: {job['recommendation']} | Category: {job['detected_category'] or 'Uncategorized'} | Seniority: {job['detected_seniority'] or 'Unspecified'} | Required experience: {job['required_experience_years'] if job['required_experience_years'] is not None else 'Unknown'}")
    print(f"Matching required skills: {joined('matching_required_skills')}")
    print(f"Matching preferred skills: {joined('matching_preferred_skills')}")
    print(f"Other matching profile skills: {joined('matching_skills')}")
    print(f"Missing required skills: {joined('missing_required_skills')}")
    print(f"Missing preferred skills: {joined('missing_preferred_skills')}")
    print(f"Eligibility flags: {joined('eligibility_flags')}")
    print(f"Defense eligibility: {job['defense_eligibility_status']} | Work authorization: {job['work_authorization_eligibility']}")
    print(f"Citizenship requirement: {job['citizenship_requirement']} | Export control: {job['export_control_requirement']}")
    print(f"Clearance requirement: {job['security_clearance_requirement']} | Level: {job['required_clearance_level'] or 'None'} | Active required: {'yes' if job['active_clearance_required'] else 'no'} | Eligibility to obtain required: {'yes' if job['clearance_eligibility_required'] else 'no'}")
    print(f"Defense eligibility reasons: {joined('defense_eligibility_reasons')}")
    print(f"Eligibility evidence: {joined('eligibility_evidence_snippets')}")
    print("Positive reasons:")
    for reason in job["positive_reasons"]:
        print(f"  + {reason}")
    print("Negative reasons:")
    for reason in job["negative_reasons"]:
        print(f"  - {reason}")
    print(f"Description summary: {summary or 'No description available'}")
    print(f"Apply URL: {job['apply_url']}")


def application_package(database: Database, job: dict[str, Any], config_dir: Path) -> None:
    profile, _, _ = load_configuration(config_dir)
    requirements = extract_requirements(job["title"], job["description"], job.get("required_skills"))
    coverage = map_evidence(requirements, profile)
    variants_path = config_dir / "resume_variants.yaml"
    variants = load_yaml(variants_path).get("resumes", {}) if variants_path.exists() else {}
    resume = next((value.get("path") for value in variants.values()
                   if job.get("role_family") in value.get("role_families", [])), None)
    company_history = saturation(database.company_applications(job["company"]))
    print("APPLICATION PACKAGE")
    print(f"\nJob: {job['company']} - {job['title']}")
    print(f"Company: {job['company']}\nRole family: {job.get('role_family', 'other')}")
    print(f"\nRecommended resume: {resume or 'No matching resume variant configured'}")
    print("\nStrongest candidate evidence:")
    for item in coverage:
        if item["state"] in {"strong_evidence", "related_evidence"}:
            print(f"  - {item['requirement']}: {item['evidence']} ({item['state']})")
    print("\nImportant JD keywords: " + (", ".join(requirements) or "None detected"))
    gaps = [item["requirement"] for item in coverage if item["state"] == "no_profile_evidence"]
    print("Possible gaps: " + (", ".join(gaps) or "None detected"))
    print(f"Eligibility: {job.get('eligibility_status', 'unknown').upper()}")
    print(f"Application effort: {job.get('estimated_effort_minutes') or 'unknown'}" + (" minutes" if job.get('estimated_effort_minutes') else ""))
    print(f"Company saturation: {company_history['level'].upper()}")
    if company_history["warning"]:
        print(f"Warning: {company_history['warning']}")


def print_analytics(database: Database) -> None:
    data = summarize(database.analytics_rows())
    print("APPLICATION FUNNEL")
    for label, key in (("Total applications", "total"), ("Pending", "pending"), ("Rejected", "rejected"),
                       ("Screens/interviews", "interviews"), ("Offers", "offers"), ("Withdrawn", "withdrawn")):
        print(f"{label}: {data[key]}")
    for label, key in (("Response rate", "response_rate"), ("Interview rate", "interview_rate"), ("Offer rate", "offer_rate")):
        value = data[key]
        print(f"{label}: {'Unavailable' if value is None else f'{value:.1f}%'}")
    if data["small_sample"]: print("Note: overall sample is too small for a meaningful conversion estimate.")
    for field, groups in data["groups"].items():
        if not groups: continue
        print(f"\nBY {field.replace('_', ' ').upper()}")
        for name, group in groups.items():
            suffix = " — insufficient sample" if group["small_sample"] else f" — interview rate {group['interview_rate']:.1f}%"
            print(f"  {name}: {group['total']} applications{suffix}")


def print_prep(job: dict[str, Any]) -> None:
    topics = prep_topics(job.get("role_family", "other"), job["title"], job["description"])
    print(f"INTERVIEW PREP\n\n{job['company']} - {job['title']}\nRole family: {job.get('role_family', 'other')}\n")
    if not topics: print("No role-specific topics were confidently identified.")
    else:
        print("Relevant topics:")
        for topic in topics: print(f"  - {topic}")


def export_calibration(database: Database, output: Path) -> int:
    jobs = database.list_ranked_jobs(65, active=True, limit=None)
    fields = [
        "job_id", "company", "title", "location", "priority_score", "fit_score",
        "competitiveness_score", "preference_score", "recency_score", "recommendation",
        "category", "seniority", "required_experience", "matching_skills",
        "missing_required_skills", "eligibility_flags", "relevance_review", "review_note", "apply_url",
        "citizenship_requirement", "export_control_requirement", "security_clearance_requirement",
        "required_clearance_level", "active_clearance_required", "clearance_eligibility_required",
        "work_authorization_eligibility", "defense_eligibility_status",
        "defense_eligibility_reasons", "eligibility_evidence_snippets",
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for job in jobs:
            writer.writerow({
                "job_id": job["id"], "company": job["company"], "title": job["title"],
                "location": job["location"], "priority_score": job["priority_score"],
                "fit_score": job["fit_score"], "competitiveness_score": job["competitiveness_score"],
                "preference_score": job["preference_score"], "recency_score": job["recency_score"],
                "recommendation": job["recommendation"], "category": job["detected_category"] or "",
                "seniority": job["detected_seniority"] or "", "required_experience": job["required_experience_years"],
                "matching_skills": "; ".join(job["matching_skills"]),
                "missing_required_skills": "; ".join(job["missing_required_skills"]),
                "eligibility_flags": "; ".join(job["eligibility_flags"]),
                "relevance_review": job["relevance"], "review_note": job["review_note"],
                "apply_url": job["apply_url"],
                "citizenship_requirement": job["citizenship_requirement"],
                "export_control_requirement": job["export_control_requirement"],
                "security_clearance_requirement": job["security_clearance_requirement"],
                "required_clearance_level": job["required_clearance_level"] or "",
                "active_clearance_required": job["active_clearance_required"],
                "clearance_eligibility_required": job["clearance_eligibility_required"],
                "work_authorization_eligibility": job["work_authorization_eligibility"],
                "defense_eligibility_status": job["defense_eligibility_status"],
                "defense_eligibility_reasons": "; ".join(job["defense_eligibility_reasons"]),
                "eligibility_evidence_snippets": "; ".join(job["eligibility_evidence_snippets"]),
            })
    return len(jobs)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = build_parser().parse_args(argv)
    database = Database(args.database)
    if args.command == "init-db":
        database.initialize()
        print(f"Initialized database at {args.database}")
    elif args.command == "scan":
        return scan(
            database, args.config_dir, force_detail_refresh=args.force_detail_refresh,
            detail_timeout=args.detail_timeout, detail_workers=args.detail_workers,
            detail_retries=args.detail_retries, detail_retry_interval=args.detail_retry_interval,
            detail_board_timeout=args.detail_board_timeout,
        )
    elif args.command == "rescore":
        database.initialize()
        profile, preferences, _ = load_configuration(args.config_dir)
        stored = database.all_jobs()
        for job_id, job in stored:
            scored = contextualize_priority(database, job_id, job, score_job(job, profile, preferences), profile, preferences)
            database.save_score(job_id, scored)
        print(f"Rescored {len(stored)} stored jobs.")
    elif args.command == "reanalyze":
        database.initialize()
        count = reanalyze(database, args.config_dir)
        print(f"Reanalyzed {count} stored jobs at analysis version {ANALYSIS_VERSION}; overall scores and application history were preserved.")
    elif args.command == "list":
        database.initialize()
        list_jobs(database, args)
    elif args.command == "top":
        database.initialize()
        top_jobs(database, args.limit)
    elif args.command == "ranked":
        database.initialize()
        ranked_jobs(database, args.limit)
    elif args.command == "applications":
        database.initialize()
        list_applications(database, args)
    elif args.command == "show":
        database.initialize()
        job = database.get_job(args.job_id)
        if job is None:
            print(f"Job {args.job_id} was not found.", file=sys.stderr)
            return 1
        show_job(job)
    elif args.command == "package":
        database.initialize(); job = database.get_job(args.job_id)
        if job is None:
            print(f"Job {args.job_id} was not found.", file=sys.stderr); return 1
        application_package(database, job, args.config_dir)
    elif args.command == "set-effort":
        database.initialize()
        try:
            if not database.set_effort(args.job_id, args.minutes):
                print(f"Job {args.job_id} was not found.", file=sys.stderr); return 1
        except ValueError as exc:
            print(str(exc), file=sys.stderr); return 2
        profile, preferences, _ = load_configuration(args.config_dir)
        stored_job = next((job for job_id, job in database.all_jobs() if job_id == args.job_id), None)
        if stored_job is not None:
            rescored = score_job(stored_job, profile, preferences)
            database.save_score(args.job_id, contextualize_priority(database, args.job_id, stored_job, rescored, profile, preferences))
        print(f"Set estimated application effort for job {args.job_id} to {args.minutes} minutes.")
    elif args.command == "analytics":
        database.initialize(); print_analytics(database)
    elif args.command == "prep":
        database.initialize(); job = database.get_job(args.job_id)
        if job is None: print(f"Job {args.job_id} was not found.", file=sys.stderr); return 1
        print_prep(job)
    elif args.command == "answers":
        path = args.config_dir / "application_answers.yaml"
        if not path.exists():
            print(f"Answer bank not found at {path}. Copy application_answers.example.yaml and keep it local.", file=sys.stderr)
            return 1
        answers = load_yaml(path)
        if args.answer_key:
            if args.answer_key not in answers:
                print(f"Answer key '{args.answer_key}' was not found.", file=sys.stderr); return 1
            print(answers[args.answer_key])
        else:
            print("APPLICATION ANSWER BANK\n")
            print("Available keys:")
            for key in sorted(answers): print(f"  - {key}")
    elif args.command == "report":
        database.initialize()
        followups = follow_up_rows(database, args.config_dir)
        generate_report(
            database.list_ranked_jobs(active=True, limit=None), args.output,
            inactive_jobs=database.list_ranked_jobs(active=False, limit=None),
            follow_ups=followups,
        )
        print(f"Wrote report to {args.output}")
    elif args.command == "update-status":
        database.initialize()
        try:
            applied_at = datetime.combine(args.date, datetime_time.min, tzinfo=timezone.utc) if args.date else None
            suppress = True if args.do_not_follow_up else False if args.allow_follow_up else None
            if not database.update_status(args.job_id, args.status, applied_at, suppress, args.reason):
                print(f"Job {args.job_id} was not found.", file=sys.stderr)
                return 1
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(f"Updated job {args.job_id} to {args.status}.")
    elif args.command == "add-contact":
        database.initialize()
        try:
            contact_id = database.add_contact(
                args.job_id, name=args.name, contact_type=args.type, role_title=args.role,
                email=args.email, profile_url=args.profile_url, source=args.source,
                notes=args.note, verified=args.verified,
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(f"Added contact {contact_id} to job {args.job_id}.")
    elif args.command == "follow-ups":
        database.initialize()
        print_follow_ups(follow_up_rows(database, args.config_dir, company=args.company),
                         show_all=args.all, due_only=args.due, limit=args.limit)
    elif args.command == "record-follow-up":
        database.initialize()
        settings = _follow_up_settings(args.config_dir)
        when = datetime.combine(args.date, datetime_time.min, tzinfo=timezone.utc) if args.date else datetime.now(timezone.utc)
        next_date = add_business_days(when, int(settings.get("follow_up_spacing_business_days", 5)), settings.get("holidays", []))
        next_at = datetime.combine(next_date, datetime_time.min, tzinfo=timezone.utc)
        try:
            history_id = database.record_follow_up(
                args.job_id, method=args.method, contact_id=args.contact, note=args.note,
                followed_up_at=when, next_follow_up_at=next_at,
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(f"Recorded follow-up {history_id} for job {args.job_id}; next review {next_date.isoformat()}.")
    elif args.command == "daily":
        database.initialize()
        daily(database, args.config_dir, args.target)
    elif args.command == "review":
        database.initialize()
        try:
            if not database.update_review(args.job_id, args.relevance, args.note):
                print(f"Job {args.job_id} was not found.", file=sys.stderr)
                return 1
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(f"Reviewed job {args.job_id} as {args.relevance}.")
    elif args.command == "export-calibration":
        database.initialize()
        count = export_calibration(database, args.output)
        print(f"Exported {count} jobs to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
