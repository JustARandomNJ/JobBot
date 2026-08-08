"""Greenhouse public Job Board API collector."""

from __future__ import annotations

from typing import Any
import json
import logging
import re
import requests
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FuturesTimeout, as_completed
from datetime import datetime, timedelta, timezone

from collectors.base import BaseCollector, clean_html, extract_experience_years, extract_skills, parse_datetime
from models.job import Job


class GreenhouseCollector(BaseCollector):
    source = "greenhouse"

    def __init__(self, *args: Any, workers: int = 10, retry_interval: float = 3600,
                 board_detail_timeout: float = 120.0,
                 force_detail_refresh: bool = False, cached_details: dict[str, dict[str, Any]] | None = None,
                 **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.workers = max(1, workers)
        self.retry_interval = max(0.0, retry_interval)
        self.board_detail_timeout = max(1.0, board_detail_timeout)
        self.force_detail_refresh = force_detail_refresh
        self.cached_details = cached_details or {}
        self.detail_requests = 0
        self.cached_reused = 0
        self.incomplete_details = 0

    @property
    def stats(self) -> dict[str, int]:
        return {"detail_requests": self.detail_requests, "cached_reused": self.cached_reused,
                "retries": self.retry_count, "timeouts": self.timeout_count,
                "incomplete_details": self.incomplete_details}

    @staticmethod
    def _marker(item: dict[str, Any]) -> str:
        # The listing payload's content/update marker determines whether cached
        # eligibility fields still describe this exact external posting.
        return json.dumps([item.get("updated_at"), item.get("content")], ensure_ascii=False, sort_keys=True)

    def _can_reuse(self, item: dict[str, Any], cached: dict[str, Any] | None) -> bool:
        if self.force_detail_refresh or not cached or cached.get("detail_source_marker") != self._marker(item):
            return False
        if cached.get("eligibility_text_complete") is True:
            return True
        attempted = parse_datetime(cached.get("detail_inspection_attempted_at"))
        return attempted is not None and datetime.now(timezone.utc) < attempted + timedelta(seconds=self.retry_interval)

    def collect(self, company: str, identifier: str) -> list[Job]:
        url = f"https://boards-api.greenhouse.io/v1/boards/{identifier}/jobs"
        payload = self.request_json(url, params={"content": "true"})
        items = payload.get("jobs", [])

        def inspect(item: dict[str, Any]) -> Job:
            external_id = str(item.get("id", ""))
            cached = self.cached_details.get(external_id)
            marker = self._marker(item)
            if self._can_reuse(item, cached):
                self.cached_reused += 1
                metadata = {key: cached.get(key) for key in (
                    "eligibility_text", "eligibility_text_sources", "eligibility_text_complete",
                    "detail_inspection_status", "detail_inspection_failure", "detail_inspection_attempted_at",
                    "detail_retry_eligible_at", "detail_source_marker") if key in cached}
                return self._normalize(company, item, eligibility_metadata=metadata)
            detail_complete = True
            attempted_at = datetime.now(timezone.utc)
            self.detail_requests += 1
            try:
                detail = self.request_json(
                    f"{url}/{item.get('id')}", params={"questions": "true"}
                )
            except requests.RequestException as exc:
                logging.warning("Could not inspect public Greenhouse fields for job %s: %s", item.get("id"), exc)
                detail, detail_complete = item, False
                self.incomplete_details += 1
                # An unchanged posting keeps its last known successful evidence.
                if cached and cached.get("detail_source_marker") == marker and cached.get("eligibility_text_complete") is True:
                    eligibility_text = cached.get("eligibility_text", "")
                    eligibility_sources = cached.get("eligibility_text_sources", [])
                else:
                    eligibility_text, eligibility_sources = self._public_eligibility_text(item)
                metadata = {
                    "eligibility_text": eligibility_text, "eligibility_text_sources": eligibility_sources,
                    "eligibility_text_complete": bool(cached and cached.get("detail_source_marker") == marker and cached.get("eligibility_text_complete") is True),
                    "detail_inspection_status": "failed", "detail_inspection_failure": f"{type(exc).__name__}: {exc}",
                    "detail_inspection_attempted_at": attempted_at.isoformat(),
                    "detail_retry_eligible_at": (attempted_at + timedelta(seconds=self.retry_interval)).isoformat(),
                    "detail_source_marker": marker,
                }
                return self._normalize(company, item, eligibility_metadata=metadata)
            eligibility_text, eligibility_sources = self._public_eligibility_text({**item, **detail})
            metadata = {
                "eligibility_text": eligibility_text, "eligibility_text_sources": eligibility_sources,
                "eligibility_text_complete": True, "detail_inspection_status": "complete",
                "detail_inspection_failure": None, "detail_inspection_attempted_at": attempted_at.isoformat(),
                "detail_retry_eligible_at": None, "detail_source_marker": marker,
            }
            return self._normalize(company, {**item, **detail}, eligibility_metadata=metadata)

        # Public detail reads are independent. A conservative bounded pool keeps
        # a large board practical without placing unbounded load on Greenhouse.
        executor = ThreadPoolExecutor(max_workers=self.workers)
        futures: dict[Future[Job], dict[str, Any]] = {executor.submit(inspect, item): item for item in items}
        jobs: list[Job] = []
        completed: set[Future[Job]] = set()
        try:
            for future in as_completed(futures, timeout=self.board_detail_timeout):
                jobs.append(future.result())
                completed.add(future)
        except FuturesTimeout:
            now = datetime.now(timezone.utc)
            unfinished = [(future, item) for future, item in futures.items() if future not in completed]
            logging.warning("Greenhouse detail deadline reached; preserving %d uninspected jobs for manual review", len(unfinished))
            for future, item in unfinished:
                future.cancel()
                self.incomplete_details += 1
                marker = self._marker(item)
                cached = self.cached_details.get(str(item.get("id", "")))
                keep_cached = bool(cached and cached.get("detail_source_marker") == marker and cached.get("eligibility_text_complete") is True)
                metadata = {
                    "eligibility_text": cached.get("eligibility_text", "") if keep_cached else self._public_eligibility_text(item)[0],
                    "eligibility_text_sources": cached.get("eligibility_text_sources", []) if keep_cached else self._public_eligibility_text(item)[1],
                    "eligibility_text_complete": keep_cached,
                    "detail_inspection_status": "failed", "detail_inspection_failure": "board detail deadline exceeded",
                    "detail_inspection_attempted_at": now.isoformat(),
                    "detail_retry_eligible_at": (now + timedelta(seconds=self.retry_interval)).isoformat(),
                    "detail_source_marker": marker,
                }
                jobs.append(self._normalize(company, item, eligibility_metadata=metadata))
            executor.shutdown(wait=False, cancel_futures=True)
        except KeyboardInterrupt:
            for future in futures:
                future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        else:
            executor.shutdown(wait=True)
        return jobs

    @staticmethod
    def _public_eligibility_text(item: dict[str, Any]) -> tuple[str, list[str]]:
        """Retain only public fields relevant to deterministic eligibility."""
        terms = re.compile(r"clearance|citizenship|citizen|U\.?S\.? person|ITAR|EAR|export.control|permanent resident|protected individual", re.I)
        parts: list[str] = []
        sources: list[str] = []
        for field in ("questions", "compliance", "demographic_questions", "location_questions"):
            value = item.get(field)
            if value is None:
                continue
            for entry in value if isinstance(value, list) else [value]:
                if isinstance(entry, dict):
                    minimal = {key: entry.get(key) for key in ("label", "description", "required") if entry.get(key) is not None}
                    raw = json.dumps(minimal, ensure_ascii=False)
                else:
                    raw = str(entry)
                text = clean_html(raw)
                if terms.search(text):
                    parts.append(text)
                    sources.append(field)
        return " ".join(parts), sorted(set(sources))

    def _normalize(self, company: str, item: dict[str, Any], *, eligibility_text_complete: bool = True,
                   eligibility_metadata: dict[str, Any] | None = None) -> Job:
        description = clean_html(item.get("content"))
        eligibility_text, eligibility_sources = self._public_eligibility_text(item)
        eligibility_metadata = eligibility_metadata or {
            "eligibility_text": eligibility_text, "eligibility_text_sources": eligibility_sources,
            "eligibility_text_complete": eligibility_text_complete,
        }
        required, preferred = extract_skills(description)
        location = (item.get("location") or {}).get("name") or "Unspecified"
        return Job(
            source=self.source, external_id=str(item.get("id", "")), title=item.get("title"),
            company=company, location=location, description=description,
            apply_url=item.get("absolute_url") or "", date_posted=None,
            required_skills=required, preferred_skills=preferred,
            required_experience_years=extract_experience_years(description),
            remote_status="remote" if "remote" in location.lower() else "onsite",
            source_metadata={
                "departments": item.get("departments", []), "offices": item.get("offices", []),
                "updated_at": parse_datetime(item.get("updated_at")).isoformat() if parse_datetime(item.get("updated_at")) else None,
                **eligibility_metadata,
            },
        )
