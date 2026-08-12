# JobBot

JobBot is a local Python command-line tool for discovering and prioritizing public job postings. It collects jobs from configured company boards, normalizes and deduplicates them, scores each role against a locally configured candidate profile and preferences, stores the results in SQLite, and provides workflows for reviewing jobs, tracking applications, and planning follow-ups.

JobBot is decision support, not an application bot. It does not submit applications, sign in to job sites, scrape LinkedIn or Indeed, send messages, or use an LLM. Its scores are transparent ranking heuristics, not predictions of an offer.

## Supported job sources

JobBot includes collectors for the public job-board APIs used by:

- Greenhouse
- Lever
- Ashby

Add enabled boards to `config/companies.yaml` using the identifier from the company's public careers URL:

```yaml
companies:
  - name: Example Robotics
    source: greenhouse
    identifier: example-robotics
    enabled: true
  - name: Example Systems
    source: lever
    identifier: example-systems
    enabled: true
  - name: Example Automation
    source: ashby
    identifier: example-automation
    enabled: true
```

Only public endpoints are used; no API keys or login credentials belong in this configuration.

## Setup

From PowerShell in the project directory:

```powershell
py -3.11 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item config\candidate_profile.example.yaml config\candidate_profile.yaml
```

Edit the copied candidate profile, `config/preferences.yaml`, and `config/companies.yaml`. The real `config/candidate_profile.yaml` is ignored by Git; the tracked example contains generic placeholder data only.

Initialize the local database once:

```powershell
python app.py init-db
```

## Typical workflow

Scan enabled boards, review the ranked results, inspect a job, and record an application:

```powershell
python app.py scan
python app.py list
python app.py top
python app.py top --limit 10
python app.py show 123
python app.py update-status 123 applied
```

`top` defaults to the five highest-ranked actionable jobs. It shows the same detailed scoring breakdown as `show` for each result and excludes inactive postings, ineligible jobs, and jobs whose application status has progressed beyond `not reviewed` or `saved`.

Other useful listing commands include:

```powershell
python app.py list --minimum-score 70
python app.py list --new --limit 25
python app.py list --company "Example Robotics"
python app.py list --category firmware
python app.py list --recommendation "Apply immediately"
python app.py list --inactive
```

Active jobs are listed by default. A posting is marked inactive after it is absent from two successful scans of its company board; collector failures do not count as misses.

## Ranking and scoring

Every scored job receives four independent scores from 0 to 100:

- Fit: skill overlap, title category, project technologies, degree relevance, and qualifications.
- Competitiveness: required-skill coverage, requested experience, early-career signals, and preferred-skill gaps.
- Preference: preferred categories, location, remote status, employment type, and avoided roles.
- Recency: full credit for recently posted jobs, declining toward zero at 90 days; an unknown date receives a neutral score.

The default combined score is:

```text
priority_score =
    0.40 * fit_score
  + 0.25 * competitiveness_score
  + 0.20 * preference_score
  + 0.15 * recency_score
```

Eligibility rules separately flag or exclude roles based on configurable experience, citizenship, export-control, clearance, leadership, and unrelated-role criteria. Scoring and extraction are deterministic and rule-based. Always read the original posting before applying.

Use `show` for the complete score, matched and missing skills, eligibility evidence, posting summary, and application URL:

```powershell
python app.py show 123
```

## Application status tracking

JobBot tracks these statuses: `not reviewed`, `saved`, `applied`, `rejected`, `recruiter screen`, `technical interview`, `final interview`, `offer`, `no response`, `skipped`, and `withdrawn`.

Update a job by its numeric ID:

```powershell
python app.py update-status 123 saved
python app.py update-status 123 applied
python app.py update-status 123 applied --date 2026-08-01
python app.py update-status 123 "technical interview"
```

The first `applied` update records the application timestamp. An explicit `--date` can backfill the original application date.

The `applications` command lists every job whose status has changed from `not reviewed`, newest activity first. It supports exact status and case-insensitive partial company filters:

```powershell
python app.py applications
python app.py applications --status applied
python app.py applications --company "Allen Control Systems"
```

## Follow-up tracking

Contacts and follow-ups are entered manually. JobBot never discovers contact details or sends communications.

```powershell
python app.py add-contact 123 --name "Example Recruiter" --type recruiter --email "recruiter@example.com" --verified
python app.py follow-ups
python app.py follow-ups --due --limit 10
python app.py follow-ups --all --company "Example Robotics"
python app.py record-follow-up 123 --contact 1 --method email --note "Sent a brief follow-up"
python app.py update-status 123 applied --do-not-follow-up
```

Follow-up recommendations consider business days since applying, application and posting state, original priority, contact quality, suppression settings, and prior follow-up history. `daily` combines application priorities, due follow-ups, and statuses needing attention:

```powershell
python app.py daily
python app.py daily --target 5
```

## Calibration and reports

Record relevance feedback independently of application status:

```powershell
python app.py review 123 strong-match --note "Strong overlap with the role"
python app.py review 124 possible
python app.py review 125 poor-match
python app.py review 126 irrelevant
```

Export active jobs scoring at least 65 for offline calibration, or create a local static HTML report:

```powershell
python app.py export-calibration
python app.py export-calibration --output data\calibration.csv
python app.py report
Invoke-Item .\data\report.html
```

The default database, report, and calibration export under `data/` are ignored by Git because they may contain private job-search data.

## Reliability and limitations

Scans use configurable timeouts and bounded retries. Greenhouse detail inspection is parallelized and cached; incomplete detail requests retain the board posting for manual eligibility review. Run `python app.py scan --help` for reliability controls, including detail timeout, worker, retry, refresh, and board-timeout options.

Descriptions from job boards are converted from untrusted HTML to plain text, and report output is HTML-escaped. Current limitations include manually configured board identifiers, inconsistent source fields, heuristic text extraction, and no scheduler, graphical interface, notifications, or automatic application submission.

## Tests

The test suite mocks network behavior and does not require live job boards:

```powershell
python -m pytest
```
