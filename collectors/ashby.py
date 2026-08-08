"""Ashby public Job Posting API collector."""

from __future__ import annotations

from typing import Any

from collectors.base import BaseCollector, clean_html, extract_experience_years, extract_skills, parse_datetime
from models.job import Job


class AshbyCollector(BaseCollector):
    source = "ashby"

    def collect(self, company: str, identifier: str) -> list[Job]:
        payload = self.request_json(f"https://api.ashbyhq.com/posting-api/job-board/{identifier}")
        return [self._normalize(company, item) for item in payload.get("jobs", []) if item.get("isListed", True)]

    def _normalize(self, company: str, item: dict[str, Any]) -> Job:
        description = clean_html(item.get("descriptionHtml") or item.get("descriptionPlain"))
        required, preferred = extract_skills(description)
        location = item.get("location") or "Unspecified"
        workplace = item.get("workplaceType")
        return Job(
            source=self.source, external_id=str(item.get("id") or item.get("jobUrl") or ""),
            title=item.get("title"), company=company, location=location,
            employment_type=item.get("employmentType"), description=description,
            apply_url=item.get("applyUrl") or item.get("jobUrl") or "",
            salary_text=item.get("compensation"), date_posted=parse_datetime(item.get("publishedAt")),
            required_skills=required, preferred_skills=preferred,
            required_experience_years=extract_experience_years(description),
            remote_status=workplace or ("remote" if item.get("isRemote") else "onsite"),
            source_metadata={"department": item.get("department"), "team": item.get("team")},
        )

