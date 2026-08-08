# Job Ranker

Job Ranker is a local Python command-line application that fetches public job postings from configured Greenhouse, Lever, and Ashby boards, normalizes them, ranks them against a candidate profile, and saves the results in SQLite. It produces a simple static HTML report and tracks application status.

It intentionally does **not** submit applications, log in to job sites, scrape LinkedIn or Indeed, generate resumes, send email, use browser automation, or use an LLM. Scores are ranking heuristics—not probabilities of receiving an offer.

## Windows setup

From PowerShell in the project folder, create and activate a virtual environment:

```powershell
py -3.11 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

The temporary execution-policy command is only needed if PowerShell blocks the activation script. It affects the current PowerShell process only.

## Configure your profile and preferences

The supplied files are ready to edit:

- `config/candidate_profile.yaml` lists established skills, developing skills, degree keywords, and project themes.
- `config/preferences.yaml` contains categories, locations, employment preferences, eligibility rules, scoring weights, and recommendation thresholds.
- `config/companies.yaml` lists public job boards to scan.

Configuration is YAML, so preserve indentation and use spaces rather than tabs.

### Add companies

Replace or copy a placeholder entry in `config/companies.yaml`, provide its real identifier, and set `enabled: true`.

```yaml
companies:
  - name: A Greenhouse Company
    source: greenhouse
    identifier: company-token
    enabled: true
  - name: A Lever Company
    source: lever
    identifier: company-site-name
    enabled: true
  - name: An Ashby Company
    source: ashby
    identifier: company-board-name
    enabled: true
```

Find public identifiers in the company careers URL:

- Greenhouse: `boards.greenhouse.io/<company-token>`
- Lever: `jobs.lever.co/<company-site-name>`
- Ashby: `jobs.ashbyhq.com/<company-board-name>`

Only public APIs are called. No credentials should be added to these files.

## Use the application

Initialize the SQLite database:

```powershell
python app.py init-db
```

Fetch enabled boards, deduplicate, score, and store postings:

```powershell
python app.py scan
python app.py scan --force-detail-refresh
```

The intended daily workflow starts with a scan and a concise decision-support view:

```powershell
python app.py scan
python app.py daily
```

Then inspect and record actions explicitly:

```powershell
python app.py show JOB_ID
python app.py update-status JOB_ID applied
python app.py update-status JOB_ID applied --date 2026-08-07
python app.py follow-ups
python app.py record-follow-up JOB_ID --contact CONTACT_ID --method email --note "Sent brief follow-up"
```

List the highest-ranked jobs first, optionally with a cutoff:

```powershell
python app.py list
python app.py list --minimum-score 70
python app.py list --new --limit 25
python app.py list --company Zoox --category firmware
python app.py list --recommendation "Apply immediately"
```

Lists show active jobs by default and return at most 50 rows. Use `--inactive` to inspect closed jobs. Filters can be combined. A posting remains active after its first missing successful company scan and is marked inactive after its second consecutive miss; collector failures do not count as misses.

Inspect the full deterministic score breakdown:

```powershell
python app.py show 12
```

Generate a static report at `data/report.html`:

```powershell
python app.py report
Invoke-Item .\data\report.html
```

Update a job using the numeric ID shown by `list`:

```powershell
python app.py update-status 12 applied
python app.py update-status 12 "technical interview"
```

The first `applied` update records the application timestamp. Moving to another status and back does not replace the original date. Existing applied records whose date predates this feature remain unknown until manually backfilled with `--date`.

Optional contacts are entered manually; the application does not scrape LinkedIn, discover personal email addresses, or infer addresses from names:

```powershell
python app.py add-contact 12 --name "Jane Doe" --type recruiter --email "jane@example.com" --verified
python app.py follow-ups --due --limit 10
python app.py follow-ups --all --company Zoox
```

Follow-up recommendations use business days, application age, the original priority score, review state, posting state, contact quality, and recorded follow-up history. They are deterministic decision support—not probabilities or evidence that contacting someone will improve an application's outcome. No command sends email, LinkedIn messages, or any other communication.

Record ranking relevance separately from application status:

```powershell
python app.py review 12 strong-match --note "Strong MCU and RTOS overlap"
python app.py review 15 possible
python app.py review 18 poor-match
python app.py review 20 irrelevant
```

Export active jobs scoring at least 65 to `data/calibration.csv`:

```powershell
python app.py export-calibration
```

Supported statuses are Not reviewed, Saved, Applied, Rejected, Recruiter screen, Technical interview, Final interview, Offer, No response, Skipped, and Withdrawn. The application stores data in `data/jobs.db` by default. Use global options before the command to choose another location, for example `python app.py --database data/test.db init-db`.

## How scoring works

Each job receives four independent 0–100 scores:

- **Fit** combines skill overlap, title category, project technologies, degree relevance, and qualifications.
- **Competitiveness** considers required-skill coverage, requested experience, entry-level/new-graduate signals, and preferred-skill gaps.
- **Preference** considers primary/stretch/backup category, location, remote status, employment type, and avoided roles.
- **Recency** declines from full credit for postings at most seven days old toward zero at 90 days. An unknown posting date receives a conservative neutral score.

The combined score is:

```text
priority_score =
    0.40 * fit_score
  + 0.25 * competitiveness_score
  + 0.20 * preference_score
  + 0.15 * recency_score
```

Transparent eligibility rules reject obvious leadership and unrelated roles and roles asking for five or more years by default. Requests for two to three years are flagged rather than rejected. These values and keywords can be changed in `preferences.yaml`. Rejected roles are retained with a low score and a Skip recommendation so the decision remains inspectable.

Skill extraction is deliberately conservative and rule-based. Public APIs do not consistently distinguish required from preferred qualifications, so Job Ranker uses nearby wording such as “preferred,” “nice to have,” and “bonus.” Always read the original posting before applying.

## Tests

All network behavior is mocked; the test suite does not depend on live job boards.

```powershell
python -m pytest
```

## Reliability and safety

HTTP calls have configurable timeouts and bounded retries. Greenhouse detail failures retain the board posting for manual eligibility review, while successful eligibility inspections are cached until the posting changes. Use `--detail-timeout`, `--detail-workers`, `--detail-retries`, `--detail-retry-interval`, and `--detail-board-timeout` to tune scans. Errors are logged and scanning continues with the next configured company. Descriptions are converted from untrusted HTML to plain text, and all report content is HTML-escaped. The report does not execute posting markup. Job Ranker stores no login credentials and never submits applications.

## Current limitations

- Company board identifiers must be added manually.
- API fields differ, so employment type, salary, and posting date may be unavailable.
- Qualification and experience extraction relies on text patterns and can miss unusual wording.
- A posting edited at its source is rescored, but this MVP does not preserve an edit history.
- There is no scheduler, graphical interface, or automatic notification.

## Suggested improvements

Useful next steps include richer skill aliases, better qualification-section parsing, configurable API retries with backoff, saved searches, CSV export, archival of closed jobs, a scheduled Windows Task Scheduler command, and a small local dashboard. Automatic application submission should remain out of scope unless designed as a separate, explicitly controlled system.
