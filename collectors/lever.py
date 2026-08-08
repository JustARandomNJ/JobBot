"""Lever public Postings API collector."""

from __future__ import annotations

from typing import Any

from collectors.base import BaseCollector, clean_html, extract_experience_years, extract_skills, parse_datetime
from models.job import Job


class LeverCollector(BaseCollector):
    source = "lever"

    def collect(self, company: str, identifier: str) -> list[Job]:
        payload = self.request_json(f"https://api.lever.co/v0/postings/{identifier}", params={"mode": "json"})
        return [self._normalize(company, item) for item in payload]

    def _normalize(self, company: str, item: dict[str, Any]) -> Job:
        lists = item.get("lists") or []
        list_text = " ".join(clean_html(entry.get("content")) for entry in lists)
        description = " ".join(filter(None, [clean_html(item.get("descriptionPlain") or item.get("description")), list_text, clean_html(item.get("additionalPlain") or item.get("additional"))]))
        required, preferred = extract_skills(description)
        categories = item.get("categories") or {}
        location = categories.get("location") or "Unspecified"
        commitment = categories.get("commitment")
        workplace = item.get("workplaceType")
        return Job(
            source=self.source, external_id=str(item.get("id", "")), title=item.get("text"),
            company=company, location=location, employment_type=commitment, description=description,
            apply_url=item.get("applyUrl") or item.get("hostedUrl") or "",
            salary_text=str(item.get("salaryRange")) if item.get("salaryRange") else None,
            date_posted=parse_datetime(item.get("createdAt")), required_skills=required,
            preferred_skills=preferred, required_experience_years=extract_experience_years(description),
            remote_status=workplace or ("remote" if "remote" in location.lower() else "onsite"),
            source_metadata={"team": categories.get("team"), "department": categories.get("department")},
        )

