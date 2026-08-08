"""Shared collector behavior and normalization helpers."""

from __future__ import annotations

import html
import logging
import random
import re
import time
from email.utils import parsedate_to_datetime
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

import requests
from bs4 import BeautifulSoup

from models.job import Job

LOGGER = logging.getLogger(__name__)
SKILL_TERMS = [
    "C++", "Python", "C", "Embedded systems", "Firmware", "Linux", "Git",
    "Zephyr RTOS", "RTOS", "STM32", "ESP32", "Teensy", "Microcontrollers",
    "CAN bus", "Hardware debugging", "Board bring-up", "OpenCV", "YOLO", "OCR",
    "NVIDIA Jetson", "Altium", "Verilog", "SystemVerilog", "UVM", "FPGA",
    "Digital logic", "Testbenches", "Simulation",
]


def clean_html(value: str | None) -> str:
    """Convert untrusted HTML to plain text; no markup is retained."""
    if not value:
        return ""
    text = value
    # Greenhouse sometimes returns HTML entity-escaped inside JSON. Parse twice
    # so unescaping cannot leave a second layer of literal tags in storage.
    for _ in range(2):
        soup = BeautifulSoup(html.unescape(text), "html.parser")
        cleaned = soup.get_text(" ", strip=True)
        if cleaned == text:
            break
        text = cleaned
    return " ".join(html.unescape(text).split())


def parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            seconds = value / 1000 if value > 10_000_000_000 else value
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, OSError):
        return None


def extract_skills(description: str) -> tuple[list[str], list[str]]:
    """Use nearby qualification wording to make a conservative skill split."""
    lower = description.lower()
    preferred_markers = ("preferred", "nice to have", "bonus")
    required: list[str] = []
    preferred: list[str] = []
    for skill in SKILL_TERMS:
        positions = [match.start() for match in re.finditer(rf"(?<!\w){re.escape(skill.lower())}(?!\w)", lower)]
        if not positions:
            continue
        if any(any(marker in lower[max(0, pos - 100):pos] for marker in preferred_markers) for pos in positions):
            preferred.append(skill)
        else:
            required.append(skill)
    return required, preferred


def extract_experience_years(description: str) -> float | None:
    patterns = (
        r"(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)(?:\s+of)?\s+(?:professional\s+)?experience",
        r"(?:minimum|at least)\s+(\d+(?:\.\d+)?)\s*(?:years?|yrs?)",
    )
    values = [float(match.group(1)) for pattern in patterns for match in re.finditer(pattern, description, re.I)]
    return max(values) if values else None


class BaseCollector(ABC):
    source: str

    def __init__(self, session: requests.Session | None = None, timeout: float = 15.0,
                 retries: int = 2, backoff_base: float = 0.5) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self.retries = max(0, min(2, retries))
        self.backoff_base = backoff_base
        self.retry_count = 0
        self.timeout_count = 0

    def request_json(self, url: str, **kwargs: Any) -> Any:
        last_error: requests.RequestException | None = None
        for attempt in range(self.retries + 1):
            response = None
            try:
                response = self.session.get(url, timeout=self.timeout, **kwargs)
                if response.status_code == 429 or 500 <= response.status_code < 600:
                    raise requests.HTTPError(
                        f"Temporary API error {response.status_code} for {url}", response=response
                    )
                response.raise_for_status()
                return response.json()
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
                last_error = exc
                if isinstance(exc, requests.Timeout):
                    self.timeout_count += 1
                status = getattr(getattr(exc, "response", None), "status_code", None)
                transient = isinstance(exc, (requests.Timeout, requests.ConnectionError)) or status == 429 or (status is not None and 500 <= status < 600)
                if not transient or attempt >= self.retries:
                    raise
                self.retry_count += 1
                retry_after = getattr(response, "headers", {}).get("Retry-After") if response is not None else None
                try:
                    delay = max(0.0, float(retry_after)) if retry_after is not None else None
                except (TypeError, ValueError):
                    try:
                        retry_time = parsedate_to_datetime(str(retry_after))
                        delay = max(0.0, (retry_time - datetime.now(retry_time.tzinfo)).total_seconds())
                    except (TypeError, ValueError, OverflowError):
                        delay = None
                if delay is None:
                    delay = self.backoff_base * (2 ** attempt) * random.uniform(0.5, 1.5)
                time.sleep(delay)
        raise last_error or requests.RequestException(f"Request failed for {url}")

    @abstractmethod
    def collect(self, company: str, identifier: str) -> list[Job]:
        """Fetch and normalize all public postings for one company."""
