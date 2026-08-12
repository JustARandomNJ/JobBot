"""Small, explicit SQLite data-access layer."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from models.job import Job, JobScore

VALID_STATUSES = {
    "not reviewed", "saved", "applied", "rejected", "recruiter screen",
    "technical interview", "final interview", "offer", "no response", "skipped", "withdrawn",
}
VALID_REVIEWS = {"unreviewed", "strong match", "possible", "poor match", "irrelevant"}
VALID_CONTACT_TYPES = {"recruiter", "hiring manager", "team member", "referral", "general recruiting contact", "unknown"}
VALID_FOLLOW_UP_METHODS = {"email", "linkedin", "phone", "referral", "other"}
JSON_FIELDS = {
    "required_skills", "preferred_skills", "source_metadata", "matching_skills",
    "matching_required_skills", "matching_preferred_skills", "missing_required_skills",
    "missing_preferred_skills", "eligibility_flags", "positive_reasons", "negative_reasons",
    "defense_eligibility_reasons", "eligibility_evidence_snippets",
}


def normalize_url(url: str) -> str:
    """Normalize URL identity while dropping common tracking parameters."""
    if not url:
        return ""
    parts = urlsplit(url.strip())
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_")))
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), query, ""))


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY,
                    source TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    company TEXT NOT NULL,
                    location TEXT NOT NULL,
                    employment_type TEXT,
                    description TEXT NOT NULL,
                    apply_url TEXT NOT NULL,
                    normalized_apply_url TEXT NOT NULL,
                    salary_text TEXT,
                    date_posted TEXT,
                    date_discovered TEXT NOT NULL,
                    required_skills TEXT NOT NULL,
                    preferred_skills TEXT NOT NULL,
                    required_experience_years REAL,
                    seniority TEXT,
                    remote_status TEXT,
                    source_metadata TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    last_seen_at TEXT,
                    missing_scan_count INTEGER NOT NULL DEFAULT 0,
                    closed_at TEXT,
                    first_seen_scan_id INTEGER,
                    last_seen_scan_id INTEGER,
                    UNIQUE(source, external_id, company, title, normalized_apply_url)
                );
                CREATE TABLE IF NOT EXISTS job_scores (
                    job_id INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
                    fit_score REAL NOT NULL,
                    competitiveness_score REAL NOT NULL,
                    preference_score REAL NOT NULL,
                    recency_score REAL NOT NULL,
                    priority_score REAL NOT NULL,
                    detected_category TEXT,
                    detected_seniority TEXT,
                    matching_skills TEXT NOT NULL,
                    matching_required_skills TEXT NOT NULL DEFAULT '[]',
                    matching_preferred_skills TEXT NOT NULL DEFAULT '[]',
                    missing_required_skills TEXT NOT NULL,
                    missing_preferred_skills TEXT NOT NULL,
                    eligibility_flags TEXT NOT NULL,
                    positive_reasons TEXT NOT NULL DEFAULT '[]',
                    negative_reasons TEXT NOT NULL DEFAULT '[]',
                    rejected INTEGER NOT NULL,
                    explanation TEXT NOT NULL,
                    recommendation TEXT NOT NULL,
                    citizenship_requirement TEXT NOT NULL DEFAULT 'none',
                    export_control_requirement TEXT NOT NULL DEFAULT 'none',
                    security_clearance_requirement TEXT NOT NULL DEFAULT 'none',
                    required_clearance_level TEXT,
                    active_clearance_required INTEGER NOT NULL DEFAULT 0,
                    clearance_eligibility_required INTEGER NOT NULL DEFAULT 0,
                    work_authorization_eligibility TEXT NOT NULL DEFAULT 'eligible',
                    defense_eligibility_status TEXT NOT NULL DEFAULT 'no_special_requirement',
                    defense_eligibility_reasons TEXT NOT NULL DEFAULT '[]',
                    eligibility_evidence_snippets TEXT NOT NULL DEFAULT '[]',
                    scored_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS application_status (
                    job_id INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
                    status TEXT NOT NULL DEFAULT 'not reviewed',
                    updated_at TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    applied_at TEXT,
                    last_follow_up_at TEXT,
                    follow_up_count INTEGER NOT NULL DEFAULT 0,
                    next_follow_up_at TEXT,
                    do_not_follow_up INTEGER NOT NULL DEFAULT 0,
                    application_date_unknown INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS application_contacts (
                    id INTEGER PRIMARY KEY,
                    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    role_title TEXT,
                    contact_type TEXT NOT NULL,
                    email TEXT,
                    profile_url TEXT,
                    source TEXT,
                    notes TEXT NOT NULL DEFAULT '',
                    verified INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS follow_up_history (
                    id INTEGER PRIMARY KEY,
                    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    contact_id INTEGER REFERENCES application_contacts(id) ON DELETE SET NULL,
                    method TEXT NOT NULL,
                    followed_up_at TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS scan_history (
                    id INTEGER PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    companies_attempted INTEGER NOT NULL DEFAULT 0,
                    jobs_fetched INTEGER NOT NULL DEFAULT 0,
                    jobs_saved INTEGER NOT NULL DEFAULT 0,
                    errors INTEGER NOT NULL DEFAULT 0,
                    companies_succeeded INTEGER NOT NULL DEFAULT 0,
                    companies_failed INTEGER NOT NULL DEFAULT 0,
                    jobs_updated INTEGER NOT NULL DEFAULT 0,
                    jobs_newly_inactive INTEGER NOT NULL DEFAULT 0,
                    active_jobs_70_plus INTEGER NOT NULL DEFAULT 0,
                    new_jobs_70_plus INTEGER NOT NULL DEFAULT 0,
                    outcome TEXT NOT NULL DEFAULT 'running',
                    companies_partial INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS relevance_reviews (
                    job_id INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
                    relevance TEXT NOT NULL DEFAULT 'unreviewed',
                    note TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );
                """
            )
            self._migrate_columns(connection)
            connection.execute(
                "UPDATE application_status SET application_date_unknown=1 "
                "WHERE status='applied' AND applied_at IS NULL"
            )
            connection.execute("UPDATE scan_history SET outcome='success' WHERE outcome='running' AND completed_at IS NOT NULL")
            connection.execute(
                "UPDATE scan_history SET outcome='interrupted', completed_at=started_at "
                "WHERE outcome='running' AND completed_at IS NULL"
            )
            now = datetime.now(timezone.utc).isoformat()
            connection.execute("UPDATE jobs SET last_seen_at=COALESCE(last_seen_at, updated_at, date_discovered)")
            connection.execute(
                """INSERT OR IGNORE INTO relevance_reviews (job_id, relevance, note, updated_at)
                   SELECT id, 'unreviewed', '', ? FROM jobs""", (now,)
            )

    @staticmethod
    def _migrate_columns(connection: sqlite3.Connection) -> None:
        migrations = {
            "jobs": {
                "is_active": "INTEGER NOT NULL DEFAULT 1", "last_seen_at": "TEXT",
                "missing_scan_count": "INTEGER NOT NULL DEFAULT 0", "closed_at": "TEXT",
                "first_seen_scan_id": "INTEGER", "last_seen_scan_id": "INTEGER",
            },
            "job_scores": {
                "detected_category": "TEXT", "detected_seniority": "TEXT",
                "matching_required_skills": "TEXT NOT NULL DEFAULT '[]'",
                "matching_preferred_skills": "TEXT NOT NULL DEFAULT '[]'",
                "positive_reasons": "TEXT NOT NULL DEFAULT '[]'",
                "negative_reasons": "TEXT NOT NULL DEFAULT '[]'",
                "citizenship_requirement": "TEXT NOT NULL DEFAULT 'none'",
                "export_control_requirement": "TEXT NOT NULL DEFAULT 'none'",
                "security_clearance_requirement": "TEXT NOT NULL DEFAULT 'none'",
                "required_clearance_level": "TEXT",
                "active_clearance_required": "INTEGER NOT NULL DEFAULT 0",
                "clearance_eligibility_required": "INTEGER NOT NULL DEFAULT 0",
                "work_authorization_eligibility": "TEXT NOT NULL DEFAULT 'eligible'",
                "defense_eligibility_status": "TEXT NOT NULL DEFAULT 'no_special_requirement'",
                "defense_eligibility_reasons": "TEXT NOT NULL DEFAULT '[]'",
                "eligibility_evidence_snippets": "TEXT NOT NULL DEFAULT '[]'",
            },
            "scan_history": {
                "companies_succeeded": "INTEGER NOT NULL DEFAULT 0",
                "companies_failed": "INTEGER NOT NULL DEFAULT 0",
                "jobs_updated": "INTEGER NOT NULL DEFAULT 0",
                "jobs_newly_inactive": "INTEGER NOT NULL DEFAULT 0",
                "active_jobs_70_plus": "INTEGER NOT NULL DEFAULT 0",
                "new_jobs_70_plus": "INTEGER NOT NULL DEFAULT 0",
                "outcome": "TEXT NOT NULL DEFAULT 'running'",
                "companies_partial": "INTEGER NOT NULL DEFAULT 0",
            },
            "application_status": {
                "applied_at": "TEXT", "last_follow_up_at": "TEXT",
                "follow_up_count": "INTEGER NOT NULL DEFAULT 0", "next_follow_up_at": "TEXT",
                "do_not_follow_up": "INTEGER NOT NULL DEFAULT 0",
                "application_date_unknown": "INTEGER NOT NULL DEFAULT 0",
            },
        }
        for table, columns in migrations.items():
            existing = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
            for name, definition in columns.items():
                if name not in existing:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def upsert_job(self, job: Job, scan_id: int | None = None,
                   connection: sqlite3.Connection | None = None) -> tuple[int, bool]:
        now = datetime.now(timezone.utc).isoformat()
        normalized_apply_url = normalize_url(job.apply_url)
        values = (
            job.source, job.external_id, job.title, job.company, job.location,
            job.employment_type, job.description, job.apply_url, normalized_apply_url,
            job.salary_text, job.date_posted.isoformat() if job.date_posted else None,
            job.date_discovered.isoformat(), json.dumps(job.required_skills),
            json.dumps(job.preferred_skills), job.required_experience_years, job.seniority,
            job.remote_status, json.dumps(job.source_metadata), now,
        )
        with (self.connect() if connection is None else nullcontext(connection)) as connection:
            existing = connection.execute(
                """SELECT id FROM jobs WHERE source=? AND external_id=? AND company=?
                   AND title=? AND normalized_apply_url=?""",
                (job.source, job.external_id, job.company, job.title, normalized_apply_url),
            ).fetchone()
            if existing is not None:
                job_id = int(existing["id"])
                connection.execute(
                    """UPDATE jobs SET location=?, employment_type=?, description=?, apply_url=?,
                       salary_text=?, date_posted=?, required_skills=?, preferred_skills=?,
                       required_experience_years=?, seniority=?, remote_status=?, source_metadata=?,
                       updated_at=?, is_active=1, last_seen_at=?, missing_scan_count=0,
                       closed_at=NULL, last_seen_scan_id=? WHERE id=?""",
                    (
                        job.location, job.employment_type, job.description, job.apply_url,
                        job.salary_text, job.date_posted.isoformat() if job.date_posted else None,
                        json.dumps(job.required_skills), json.dumps(job.preferred_skills),
                        job.required_experience_years, job.seniority, job.remote_status,
                        json.dumps(job.source_metadata), now, now, scan_id, job_id,
                    ),
                )
                return job_id, False
            cursor = connection.execute(
                """INSERT INTO jobs (
                    source, external_id, title, company, location, employment_type,
                    description, apply_url, normalized_apply_url, salary_text, date_posted,
                    date_discovered, required_skills, preferred_skills,
                    required_experience_years, seniority, remote_status, source_metadata, updated_at,
                    is_active, last_seen_at, missing_scan_count, first_seen_scan_id, last_seen_scan_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, 0, ?, ?)""",
                (*values, now, scan_id, scan_id),
            )
            job_id = int(cursor.lastrowid)
            connection.execute(
                "INSERT INTO application_status (job_id, status, updated_at) VALUES (?, 'not reviewed', ?)",
                (job_id, now),
            )
            connection.execute(
                "INSERT INTO relevance_reviews (job_id, relevance, note, updated_at) VALUES (?, 'unreviewed', '', ?)",
                (job_id, now),
            )
            return job_id, True

    def save_score(self, job_id: int, score: JobScore,
                   connection: sqlite3.Connection | None = None) -> None:
        values = (
            job_id, score.fit_score, score.competitiveness_score, score.preference_score,
            score.recency_score, score.priority_score, score.detected_category, score.detected_seniority,
            json.dumps(score.matching_skills), json.dumps(score.matching_required_skills),
            json.dumps(score.matching_preferred_skills),
            json.dumps(score.missing_required_skills), json.dumps(score.missing_preferred_skills),
            json.dumps(score.eligibility_flags), json.dumps(score.positive_reasons),
            json.dumps(score.negative_reasons), int(score.rejected), score.explanation,
            score.recommendation, score.citizenship_requirement, score.export_control_requirement,
            score.security_clearance_requirement, score.required_clearance_level,
            int(score.active_clearance_required), int(score.clearance_eligibility_required),
            score.work_authorization_eligibility, score.defense_eligibility_status,
            json.dumps(score.defense_eligibility_reasons), json.dumps(score.eligibility_evidence_snippets),
            datetime.now(timezone.utc).isoformat(),
        )
        with (self.connect() if connection is None else nullcontext(connection)) as connection:
            connection.execute(
                """INSERT INTO job_scores (
                    job_id, fit_score, competitiveness_score, preference_score, recency_score,
                    priority_score, detected_category, detected_seniority, matching_skills,
                    matching_required_skills, matching_preferred_skills, missing_required_skills,
                    missing_preferred_skills, eligibility_flags, positive_reasons, negative_reasons,
                    rejected, explanation, recommendation, citizenship_requirement,
                    export_control_requirement, security_clearance_requirement, required_clearance_level,
                    active_clearance_required, clearance_eligibility_required, work_authorization_eligibility,
                    defense_eligibility_status, defense_eligibility_reasons, eligibility_evidence_snippets, scored_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                  fit_score=excluded.fit_score, competitiveness_score=excluded.competitiveness_score,
                  preference_score=excluded.preference_score, recency_score=excluded.recency_score,
                  priority_score=excluded.priority_score, matching_skills=excluded.matching_skills,
                  detected_category=excluded.detected_category, detected_seniority=excluded.detected_seniority,
                  matching_required_skills=excluded.matching_required_skills,
                  matching_preferred_skills=excluded.matching_preferred_skills,
                  missing_required_skills=excluded.missing_required_skills,
                  missing_preferred_skills=excluded.missing_preferred_skills,
                  eligibility_flags=excluded.eligibility_flags, rejected=excluded.rejected,
                  positive_reasons=excluded.positive_reasons, negative_reasons=excluded.negative_reasons,
                  explanation=excluded.explanation, recommendation=excluded.recommendation,
                  citizenship_requirement=excluded.citizenship_requirement,
                  export_control_requirement=excluded.export_control_requirement,
                  security_clearance_requirement=excluded.security_clearance_requirement,
                  required_clearance_level=excluded.required_clearance_level,
                  active_clearance_required=excluded.active_clearance_required,
                  clearance_eligibility_required=excluded.clearance_eligibility_required,
                  work_authorization_eligibility=excluded.work_authorization_eligibility,
                  defense_eligibility_status=excluded.defense_eligibility_status,
                  defense_eligibility_reasons=excluded.defense_eligibility_reasons,
                  eligibility_evidence_snippets=excluded.eligibility_evidence_snippets,
                  scored_at=excluded.scored_at""",
                values,
            )

    def _decode_rows(self, rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            for field in JSON_FIELDS:
                if field in item:
                    item[field] = json.loads(item[field] or "[]")
            item["is_active"] = bool(item.get("is_active"))
            item["is_new"] = item.get("first_seen_scan_id") is not None and item.get("first_seen_scan_id") == item.get("latest_scan_id")
            result.append(item)
        return result

    def list_ranked_jobs(
        self, minimum_score: float = 0, *, active: bool | None = True,
        new_only: bool = False, company: str | None = None, category: str | None = None,
        recommendation: str | None = None, limit: int | None = 50,
        eligibility: str | None = None, statuses: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["s.priority_score >= ?"]
        params: list[Any] = [minimum_score]
        if active is not None:
            clauses.append("j.is_active = ?")
            params.append(int(active))
        if new_only:
            clauses.append("j.first_seen_scan_id = (SELECT max(id) FROM scan_history WHERE completed_at IS NOT NULL)")
        if company:
            clauses.append("lower(j.company) LIKE ?")
            params.append(f"%{company.lower()}%")
        if category:
            clauses.append("(lower(COALESCE(s.detected_category, '')) = ? OR lower(j.title) LIKE ?)")
            params.extend((category.lower(), f"%{category.lower()}%"))
        if recommendation:
            clauses.append("lower(s.recommendation) = ?")
            params.append(recommendation.lower())
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            clauses.append(f"a.status IN ({placeholders})")
            params.extend(sorted(statuses))
        if eligibility == "eligible":
            clauses.append("s.defense_eligibility_status IN ('eligible', 'no_special_requirement')")
        elif eligibility == "manual_review":
            clauses.append("s.defense_eligibility_status = 'manual_review'")
        elif eligibility == "ineligible":
            clauses.append("s.defense_eligibility_status IN ('ineligible_citizenship', 'ineligible_clearance')")
        elif eligibility == "not_ineligible":
            clauses.append("s.defense_eligibility_status NOT IN ('ineligible_citizenship', 'ineligible_clearance')")
        limit_sql = "" if limit is None else " LIMIT ?"
        if limit is not None:
            params.append(limit)
        with self.connect() as connection:
            rows = connection.execute(
                f"""SELECT j.*, s.*, a.status,a.applied_at,a.last_follow_up_at,a.follow_up_count,
                          a.next_follow_up_at,a.do_not_follow_up,r.relevance, r.note AS review_note,
                          (SELECT max(id) FROM scan_history WHERE completed_at IS NOT NULL) AS latest_scan_id
                   FROM jobs j
                   JOIN job_scores s ON s.job_id=j.id
                   JOIN application_status a ON a.job_id=j.id
                   JOIN relevance_reviews r ON r.job_id=j.id
                   WHERE {' AND '.join(clauses)}
                   ORDER BY s.priority_score DESC,
                            CASE WHEN r.relevance='unreviewed' THEN 0 ELSE 1 END,
                            j.date_posted DESC{limit_sql}""",
                params,
            ).fetchall()
        return self._decode_rows(rows)

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT j.*, s.*, a.status,a.applied_at,a.last_follow_up_at,a.follow_up_count,
                          a.next_follow_up_at,a.do_not_follow_up,r.relevance, r.note AS review_note,
                          (SELECT max(id) FROM scan_history WHERE completed_at IS NOT NULL) AS latest_scan_id
                   FROM jobs j JOIN job_scores s ON s.job_id=j.id
                   JOIN application_status a ON a.job_id=j.id
                   JOIN relevance_reviews r ON r.job_id=j.id WHERE j.id=?""", (job_id,),
            ).fetchone()
        return self._decode_rows([row])[0] if row else None

    def list_applications(self, *, status: str | None = None,
                          company: str | None = None) -> list[dict[str, Any]]:
        """Return jobs with changed application statuses, newest activity first."""
        clauses = ["a.status <> 'not reviewed'"]
        params: list[Any] = []
        if status is not None:
            clauses.append("a.status = ?")
            params.append(status)
        if company:
            clauses.append("lower(j.company) LIKE ?")
            params.append(f"%{company.lower()}%")
        with self.connect() as connection:
            rows = connection.execute(
                f"""SELECT j.id, j.company, j.title, j.is_active, a.status,
                           a.applied_at, a.updated_at AS status_updated_at
                    FROM jobs j
                    JOIN application_status a ON a.job_id = j.id
                    WHERE {' AND '.join(clauses)}
                    ORDER BY max(COALESCE(a.applied_at, ''), a.updated_at) DESC, j.id DESC""",
                params,
            ).fetchall()
        result = [dict(row) for row in rows]
        for item in result:
            item["is_active"] = bool(item["is_active"])
        return result

    def all_jobs(self) -> list[tuple[int, Job]]:
        """Return every stored posting for deterministic rescoring."""
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM jobs ORDER BY id").fetchall()
        jobs: list[tuple[int, Job]] = []
        for row in rows:
            item = dict(row)
            for field in ("required_skills", "preferred_skills", "source_metadata"):
                item[field] = json.loads(item[field] or ("{}" if field == "source_metadata" else "[]"))
            allowed = set(Job.model_fields)
            jobs.append((int(item["id"]), Job(**{key: value for key, value in item.items() if key in allowed})))
        return jobs

    def eligibility_counts(self) -> dict[str, int]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT defense_eligibility_status, count(*) AS count FROM job_scores GROUP BY defense_eligibility_status"
            ).fetchall()
        return {str(row["defense_eligibility_status"]): int(row["count"]) for row in rows}

    def greenhouse_detail_cache(self, company: str) -> dict[str, dict[str, Any]]:
        """Return eligibility metadata keyed strictly by Greenhouse external ID."""
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT external_id, source_metadata FROM jobs WHERE source='greenhouse' AND company=?",
                (company,),
            ).fetchall()
        return {str(row["external_id"]): json.loads(row["source_metadata"] or "{}") for row in rows}

    def update_review(self, job_id: int, relevance: str, note: str = "") -> bool:
        normalized = relevance.strip().lower().replace("_", " ").replace("-", " ")
        if normalized not in VALID_REVIEWS:
            raise ValueError(f"Unknown review. Choose one of: {', '.join(sorted(VALID_REVIEWS))}")
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE relevance_reviews SET relevance=?, note=?, updated_at=? WHERE job_id=?",
                (normalized, note.strip(), datetime.now(timezone.utc).isoformat(), job_id),
            )
            return cursor.rowcount == 1

    def reconcile_company_scan(self, company: str, source: str, seen_job_ids: set[int],
                               connection: sqlite3.Connection | None = None) -> int:
        """Advance missing counts only after a successful company scan."""
        with (self.connect() if connection is None else nullcontext(connection)) as connection:
            rows = connection.execute(
                "SELECT id, missing_scan_count FROM jobs WHERE company=? AND source=? AND is_active=1",
                (company, source),
            ).fetchall()
            missing = [row for row in rows if int(row["id"]) not in seen_job_ids]
            newly_inactive = sum(int(row["missing_scan_count"]) + 1 >= 2 for row in missing)
            now = datetime.now(timezone.utc).isoformat()
            for row in missing:
                count = int(row["missing_scan_count"]) + 1
                connection.execute(
                    """UPDATE jobs SET missing_scan_count=?, is_active=?, closed_at=? WHERE id=?""",
                    (count, int(count < 2), now if count >= 2 else None, int(row["id"])),
                )
            return newly_inactive

    def count_active_above(self, minimum_score: float, first_seen_scan_id: int | None = None) -> int:
        sql = "SELECT count(*) FROM jobs j JOIN job_scores s ON s.job_id=j.id WHERE j.is_active=1 AND s.priority_score>=?"
        params: list[Any] = [minimum_score]
        if first_seen_scan_id is not None:
            sql += " AND j.first_seen_scan_id=?"
            params.append(first_seen_scan_id)
        with self.connect() as connection:
            return int(connection.execute(sql, params).fetchone()[0])

    def update_status(self, job_id: int, status: str, applied_at: datetime | None = None,
                      do_not_follow_up: bool | None = None) -> bool:
        normalized = status.strip().lower().replace("_", "-").replace("-", " ")
        if normalized not in VALID_STATUSES:
            raise ValueError(f"Unknown status. Choose one of: {', '.join(sorted(VALID_STATUSES))}")
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT status,applied_at,application_date_unknown FROM application_status WHERE job_id=?", (job_id,)
            ).fetchone()
            if existing is None:
                return False
            application_date = existing["applied_at"]
            date_unknown = int(existing["application_date_unknown"])
            if normalized == "applied" and application_date is None:
                if applied_at is not None:
                    application_date = applied_at.isoformat()
                    date_unknown = 0
                elif not date_unknown and existing["status"] != "applied":
                    application_date = datetime.now(timezone.utc).isoformat()
            suppression = int(do_not_follow_up) if do_not_follow_up is not None else connection.execute(
                "SELECT do_not_follow_up FROM application_status WHERE job_id=?", (job_id,)
            ).fetchone()[0]
            cursor = connection.execute(
                """UPDATE application_status SET status=?,updated_at=?,applied_at=?,
                   do_not_follow_up=?,application_date_unknown=? WHERE job_id=?""",
                (normalized, datetime.now(timezone.utc).isoformat(), application_date, suppression, date_unknown, job_id),
            )
            return cursor.rowcount == 1

    def add_contact(self, job_id: int, *, name: str, contact_type: str, role_title: str | None = None,
                    email: str | None = None, profile_url: str | None = None,
                    source: str | None = None, notes: str = "", verified: bool = False) -> int:
        normalized = contact_type.strip().lower().replace("_", "-").replace("-", " ")
        if normalized not in VALID_CONTACT_TYPES:
            raise ValueError(f"Unknown contact type. Choose one of: {', '.join(sorted(VALID_CONTACT_TYPES))}")
        with self.connect() as connection:
            if connection.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone() is None:
                raise ValueError(f"Job {job_id} was not found.")
            cursor = connection.execute(
                """INSERT INTO application_contacts
                   (job_id,name,role_title,contact_type,email,profile_url,source,notes,verified,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (job_id, name.strip(), role_title, normalized, email, profile_url, source,
                 notes.strip(), int(verified), datetime.now(timezone.utc).isoformat()),
            )
            return int(cursor.lastrowid)

    def contacts_for_job(self, job_id: int) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM application_contacts WHERE job_id=? ORDER BY id", (job_id,)
            ).fetchall()
        result = [dict(row) for row in rows]
        for item in result:
            item["verified"] = bool(item["verified"])
        return result

    def record_follow_up(self, job_id: int, *, method: str = "other", contact_id: int | None = None,
                         note: str = "", followed_up_at: datetime | None = None,
                         next_follow_up_at: datetime | None = None) -> int:
        normalized = method.strip().lower()
        if normalized not in VALID_FOLLOW_UP_METHODS:
            raise ValueError(f"Unknown method. Choose one of: {', '.join(sorted(VALID_FOLLOW_UP_METHODS))}")
        when = followed_up_at or datetime.now(timezone.utc)
        with self.connect() as connection:
            application = connection.execute("SELECT 1 FROM application_status WHERE job_id=?", (job_id,)).fetchone()
            if application is None:
                raise ValueError(f"Job {job_id} was not found.")
            if contact_id is not None and connection.execute(
                "SELECT 1 FROM application_contacts WHERE id=? AND job_id=?", (contact_id, job_id)
            ).fetchone() is None:
                raise ValueError(f"Contact {contact_id} does not belong to job {job_id}.")
            cursor = connection.execute(
                "INSERT INTO follow_up_history (job_id,contact_id,method,followed_up_at,note) VALUES (?,?,?,?,?)",
                (job_id, contact_id, normalized, when.isoformat(), note.strip()),
            )
            connection.execute(
                """UPDATE application_status SET follow_up_count=follow_up_count+1,
                   last_follow_up_at=?, next_follow_up_at=?, updated_at=? WHERE job_id=?""",
                (when.isoformat(), next_follow_up_at.isoformat() if next_follow_up_at else None,
                 when.isoformat(), job_id),
            )
            return int(cursor.lastrowid)

    def follow_up_history(self, job_id: int) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM follow_up_history WHERE job_id=? ORDER BY followed_up_at,id", (job_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def follow_up_candidates(self, company: str | None = None,
                             include_unapplied: bool = False) -> list[dict[str, Any]]:
        params: list[Any] = []
        company_sql = "" if include_unapplied else " AND a.status NOT IN ('not reviewed','saved')"
        if company:
            company_sql = " AND lower(j.company) LIKE ?"
            params.append(f"%{company.lower()}%")
        with self.connect() as connection:
            rows = connection.execute(
                f"""SELECT j.id,j.company,j.title,j.is_active,j.closed_at,s.priority_score,
                           s.defense_eligibility_status,a.status,a.updated_at AS status_updated_at,
                           a.applied_at,a.last_follow_up_at,a.follow_up_count,a.next_follow_up_at,
                           a.do_not_follow_up,r.relevance
                    FROM jobs j JOIN job_scores s ON s.job_id=j.id
                    JOIN application_status a ON a.job_id=j.id
                    JOIN relevance_reviews r ON r.job_id=j.id WHERE 1=1{company_sql}""", params
            ).fetchall()
            contact_rows = connection.execute("SELECT * FROM application_contacts ORDER BY id").fetchall()
            history_rows = connection.execute("SELECT * FROM follow_up_history ORDER BY followed_up_at,id").fetchall()
        result = [dict(row) for row in rows]
        contacts: dict[int, list[dict[str, Any]]] = {}
        for row in contact_rows:
            item = dict(row)
            item["verified"] = bool(item["verified"])
            contacts.setdefault(int(item["job_id"]), []).append(item)
        histories: dict[int, list[dict[str, Any]]] = {}
        for row in history_rows:
            item = dict(row)
            histories.setdefault(int(item["job_id"]), []).append(item)
        for item in result:
            item["is_active"] = bool(item["is_active"])
            item["do_not_follow_up"] = bool(item["do_not_follow_up"])
            item["contacts"] = contacts.get(int(item["id"]), [])
            item["follow_up_history"] = histories.get(int(item["id"]), [])
        return result

    def start_scan(self, companies_attempted: int) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO scan_history (started_at, companies_attempted) VALUES (?, ?)",
                (datetime.now(timezone.utc).isoformat(), companies_attempted),
            )
            return int(cursor.lastrowid)

    def finish_scan(
        self, scan_id: int, *, fetched: int, saved: int, updated: int, errors: int,
        succeeded: int, newly_inactive: int, active_70: int, new_70: int,
        outcome: str = "success", partial: int = 0,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """UPDATE scan_history SET completed_at=?, jobs_fetched=?, jobs_saved=?, jobs_updated=?,
                   errors=?, companies_succeeded=?, companies_failed=?, jobs_newly_inactive=?,
                   active_jobs_70_plus=?, new_jobs_70_plus=?, outcome=?, companies_partial=?
                   WHERE id=?""",
                (datetime.now(timezone.utc).isoformat(), fetched, saved, updated, errors,
                 succeeded, errors, newly_inactive, active_70, new_70, outcome, partial, scan_id),
            )
