from collectors.ashby import AshbyCollector
from collectors.greenhouse import GreenhouseCollector
from collectors.lever import LeverCollector


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.payload)


def test_greenhouse_normalizes_and_sanitizes_html() -> None:
    session = FakeSession({"jobs": [{"id": 1, "title": "Firmware Engineer", "content": "<p>C++ firmware</p><script>bad()</script>", "absolute_url": "https://example.test/apply", "location": {"name": "Remote"}, "updated_at": "2026-08-05T12:00:00Z"}]})
    jobs = GreenhouseCollector(session=session).collect("Acme", "acme")
    assert jobs[0].source == "greenhouse"
    assert "<p>" not in jobs[0].description
    assert "C++" in jobs[0].required_skills
    assert jobs[0].date_posted is None
    assert jobs[0].source_metadata["updated_at"] == "2026-08-05T12:00:00+00:00"
    assert session.calls[0][1]["timeout"] == 15.0


def test_greenhouse_collects_minimum_public_clearance_question_text() -> None:
    listing = {"jobs": [{"id": 491, "title": "Flight Software Engineer", "content": "&lt;p&gt;Firmware&lt;/p&gt;", "absolute_url": "https://example.test/491"}]}
    detail = {"id": 491, "title": "Flight Software Engineer", "content": "&lt;p&gt;Firmware&lt;/p&gt;", "absolute_url": "https://example.test/491", "questions": [
        {"label": "Name", "required": True, "fields": []},
        {"label": "CLEARANCE ELIGIBILITY - This position requires eligibility to obtain and maintain a U.S. security clearance.", "description": "Do you hold or can you obtain a clearance?", "required": True, "fields": [{"name": "secret", "values": [{"label": "Yes"}]}]},
    ]}
    class SequenceSession(FakeSession):
        def __init__(self):
            super().__init__(None)
            self.responses = [listing, detail]
        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return FakeResponse(self.responses.pop(0))
    job = GreenhouseCollector(session=SequenceSession()).collect("Anduril", "anduril")[0]
    assert job.description == "Firmware"
    assert "requires eligibility" in job.source_metadata["eligibility_text"]
    assert "secret" not in job.source_metadata["eligibility_text"]
    assert job.source_metadata["eligibility_text_sources"] == ["questions"]


def test_lever_and_ashby_missing_fields_are_safe() -> None:
    lever = LeverCollector(session=FakeSession([{"id": "l1", "text": "Engineer"}])).collect("Acme", "acme")
    ashby = AshbyCollector(session=FakeSession({"jobs": [{"id": "a1", "title": "Engineer"}]})).collect("Acme", "acme")
    assert lever[0].location == "Unspecified"
    assert ashby[0].description == ""
