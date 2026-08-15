# JobBot

JobBot is a private, local Python CLI for discovering public engineering jobs and deciding where to spend application time. It collects Greenhouse, Lever, and Ashby boards, normalizes postings, stores lifecycle history in SQLite, compares jobs with a local candidate profile, and tracks the application funnel. It never submits an application, sends a message, signs into a job site, or calls an external LLM or paid AI API.

## Architecture and privacy

Collectors produce normalized `Job` models. Deterministic ranking modules handle eligibility, role classification, evidence, posting health, priority, analytics, and interview preparation. `storage/database.py` owns idempotent SQLite schema creation and migrations; `app.py` provides the CLI and readable detailed views.

The following personal artifacts are intentionally ignored: `config/candidate_profile.yaml`, `config/application_answers.yaml`, `config/resume_variants.yaml`, `resumes/`, databases and backups under `data/`, generated reports, `.env`, and `.venv`. Only sanitized `*.example.yaml` files belong in Git. Answer-bank values stay in YAML and are never logged or stored in SQLite. Resume files are only recommended by path and are never modified.

Before the first v2 schema migration of an existing database, JobBot creates a timestamped local copy under `data/backups/`. Migrations are idempotent and preserve jobs, scores, application state/history, contacts, follow-ups, and scan history.

## Setup

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item config\candidate_profile.example.yaml config\candidate_profile.yaml
Copy-Item config\resume_variants.example.yaml config\resume_variants.yaml
Copy-Item config\application_answers.example.yaml config\application_answers.yaml
python app.py init-db
```

Configure enabled public boards in `config/companies.yaml` with `name`, `source` (`greenhouse`, `lever`, or `ashby`), public board `identifier`, and `enabled: true`. No source needs credentials.

The candidate profile contains degree information, `expert_skills`, `developing_skills`, `projects_and_technologies`, work-authorization facts, and optional `target_role_weights`. Role weights express preference, never eligibility. Copy and edit the generic example; do not commit the real profile.

## Scores: fit versus application value

These meanings are deliberately separate:

```text
overall_score  = how well this candidate technically/professionally matches this job
priority_score = how worthwhile it is for this candidate to apply right now
```

The existing overall calculation remains the documented weighted combination of fit, competitiveness, preference, and legacy recency signals. It is not replaced or mutated by the priority engine.

Priority uses configurable weights in `config/preferences.yaml`. Technical fit is largest, followed by target role preference and freshness; smoothed historical conversion, posting health, company saturation, and known application effort contribute smaller amounts. Unknown inputs receive neutral credit. Every value, weight, and effect is stored and displayed. Eligibility is a gate: ineligible postings receive no actionable priority; manual-review postings receive a visible configurable penalty.

`top` means “the jobs most worth applying to today.” It defaults to five, sorts by `priority_score`, renders the complete `show` view, and excludes inactive, ineligible, applied, rejected, withdrawn, skipped, and otherwise non-actionable jobs. Manual-review jobs may appear with a warning.

```powershell
python app.py scan
python app.py top
python app.py top --limit 10
python app.py show 123
```

## Eligibility and role intelligence

The conservative eligibility engine inspects title, description, and reusable collector authorization metadata for student/enrollment and graduation constraints, degree and experience requirements, work authorization/sponsorship, citizenship, clearance, export control/ITAR, onsite/relocation/travel, and similar hard constraints. Its result is `eligible`, `ineligible`, `manual_review`, or `unknown`, with structured reason codes and evidence. Ambiguous wording is never treated as definitively ineligible.

Weighted title-plus-description rules classify firmware, embedded firmware, embedded Linux, BSP/drivers, kernel, controls/motor control, FPGA, ASIC RTL, ASIC design verification, physical design, DFT, formal verification, post-silicon validation, hardware test/validation, computer vision/ML, systems, general software, and other. A weak isolated keyword is insufficient when the rest of a posting contradicts it.

## Posting health

Each scan records a compact observation: timestamp, active state, description fingerprint, canonical URL, requisition ID, whether content changed, and whether an inactive job reopened. Full descriptions are not duplicated. `show` reports first/last seen data, observation and change counts, configurable 0–100 freshness, and cautious `LOW`, `MODERATE`, or `HIGH` repost risk with evidence. JobBot never calls a posting fake or a ghost job.

If an inactive posting returns, JobBot records the reopening without changing or resubmitting any prior application. When history exists, `show` identifies the reopened position and its previous application/result.

## Candidate evidence and application packages

Requirement coverage normalizes aliases such as C/Embedded C, SystemVerilog/SV, CAN/CAN bus, RTOS, device drivers, and common hardware interfaces. Each requirement is labeled `strong_evidence`, `related_evidence`, `developing`, or `no_profile_evidence`. The last state means only that the local profile contains no evidence; JobBot does not claim the candidate lacks the skill and never invents projects.

Configure local resume paths and role families in `config/resume_variants.yaml`, then generate a read-only package:

```powershell
python app.py package 123
python app.py set-effort 123 15
```

The package shows the job, role family, recommended resume path, strongest candidate evidence, important JD terms, possible evidence gaps, eligibility, and company saturation. Effort is unknown and neutral until manually supplied as estimated minutes.

Company history reports total, active, recent-window applications, and role-family distribution. Configurable saturation warnings flag several active applications or unusually broad unrelated targeting, but never prohibit an application.

## Application tracking and skip reasons

Statuses are `not reviewed`, `saved`, `applied`, `rejected`, `recruiter screen`, `technical interview`, `final interview`, `offer`, `no response`, `skipped`, and `withdrawn`.

```powershell
python app.py update-status 123 applied
python app.py update-status 123 applied --date 2026-08-01
python app.py update-status 456 skipped --reason student_only
python app.py update-status 457 skipped --reason application_unavailable
python app.py applications
```

Structured skip reasons are: `ineligible`, `student_only`, `graduation_window`, `experience_requirement`, `citizenship`, `clearance`, `export_control`, `sponsorship`, `location`, `salary`, `duplicate`, `stale`, `application_closed`, `application_unavailable`, `already_applied_elsewhere`, `not_interested`, and `other`. Omitting `--reason` remains backward compatible.

Manual contacts and follow-ups remain local:

```powershell
python app.py add-contact 123 --name "Example Recruiter" --type recruiter
python app.py follow-ups --due
python app.py record-follow-up 123 --method email
python app.py daily --target 5
```

## Analytics, calibration, and interview preparation

`python app.py analytics` reports application, pending, rejection, screen/interview, offer, and withdrawal counts plus response/interview/offer rates. It groups results by overall-fit band, priority band, role family, company, collector, freshness, eligibility, application age, and skip reason. Groups below the configurable minimum sample are explicitly labeled insufficient; percentages are not presented as statistically confident predictions.

Historical conversion uses a minimum sample and Beta-style smoothing toward a neutral baseline. Unknown/sparse role families remain neutral, and a short run of rejections cannot collapse priority.

`python app.py prep 123` selects local preparation topics from embedded and ASIC/FPGA banks only when the role family and posting support them. Interview statuses cause `show` to suggest this command; nothing changes automatically.

## Local answer bank

`config/application_answers.yaml` is a gitignored convenience file and is never written to the database.

```powershell
python app.py answers
python app.py answers --get work_authorization
```

The listing prints keys only. `--get` prints the selected value for direct use. Clipboard copying is intentionally omitted to avoid an unnecessary cross-platform dependency.

## Other commands and reliability

```powershell
python app.py list --minimum-score 70
python app.py list --new --company "Example Robotics"
python app.py list --inactive
python app.py rescore
python app.py review 123 strong-match
python app.py report
python app.py export-calibration
```

A posting becomes inactive only after it is absent from two successful scans of its company; collector failures do not count. Scans use bounded retries/timeouts, Greenhouse detail requests are cached and parallelized, and incomplete public eligibility inspection routes to manual review. Source data can still be incomplete or inconsistent, and all heuristic results should be checked against the original posting.

Run the offline test suite with:

```powershell
python -m pytest
```
