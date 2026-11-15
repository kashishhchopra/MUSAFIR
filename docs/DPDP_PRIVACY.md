# Privacy management (DPDP-oriented) - design, inventory and open legal items

> **This is software, not a legal opinion.** MUSAFIR implements the *mechanisms* the Digital Personal
> Data Protection Act, 2023 and the DPDP Rules, 2025 point towards (notice, purpose-specific consent,
> withdrawal, access/export, erasure, grievance, security). It does **not** certify compliance.
> The Act and Rules commence in phases; as of 15 Nov 2026 the substantive provisions (Sections 3-17 and
> Rules 3, 5-16) are scheduled to commence 18 months after 13 Nov 2025 - **verify the current
> commencement notifications, amendments and corrigenda with legal counsel** before making any claim.
> The dashboard itself says so (`PRIVACY_LEGAL_NOTE`).

## 1. What already existed (kept, not duplicated)

| Existing | Where | How it is reused |
|---|---|---|
| Privacy report, "delete my location history", per-tourist retention days | `services/privacy.py`, `GET/PATCH /tourists/{id}/privacy`, `DELETE /tourists/{id}/location-history`, `PrivacyCard.jsx` | Unchanged. `privacy.py` gained location listing / selective deletion; the scheduled purge now honours open emergencies and holds. |
| Hourly retention scheduler job (`retention_purge_tick`) | `main.py`, APScheduler + `JobLock` | Same job id, schedule and lock - it now calls the central engine, which runs the existing location purge plus the other categories. |
| `tracking_enabled` switch | `Tourist`, `POST /tourists/{id}/tracking` | It **is** the location consent. Withdrawing consent flips it; switching it by hand writes a consent record. `POST /location` now refuses pings while it is off. |
| Sessions: JWT + `sessions_valid_from` epoch + refresh denylist | `api/deps.py`, `api/auth.py` | Deletion bumps the epoch (all tokens die) and login refuses the account while deletion is pending. |
| Audit log (own DB session) | `services/audit.py` | Every privacy action is audited by opaque `subject_ref`, never by contents. |
| `EncryptedString` (Fernet) | `core/crypto.py` | Export files are encrypted at rest with it. |
| Guardian links, push subscriptions, devices | existing models | Switched off by consent withdrawal / deletion. |

## 2. Personal-data inventory

Defined once, in code (`services/privacy_registry.py`), and used for the inventory screen, the export and
the erasure engine so they cannot drift. Retention values are **configuration**
(`PRIVACY_RETENTION_DEFAULTS`, editable by an admin) - a blank period means *none established*, and the
engine never deletes under a blank period.

| # | Category | Tables | Why collected | Necessary? | Who can read | Shared | Proposed retention | On account deletion |
|---|---|---|---|---|---|---|---|---|
| 1 | Tourist profile (name, nationality, ID document no., phone, photo, hotel, trip dates, itinerary) | `tourists`, `liveness_verifications` | Issue/verify the Digital ID | Required | You; admin; responder/admin via QR (role-limited) | Responders | **None established** - flagged for legal review | Deleted (row de-identified only if a retained record still points at it) |
| 2 | Contact info / emergency contacts | `tourists.emergency_contacts`, `medical_profiles` | Notify someone you chose | Optional | You; responders on SOS/QR | Your contact | None established | Deleted |
| 3 | Location history | `location_pings` | Safety scoring, anomaly detection | Optional (consent) | You; admin | No | **90 days after trip end** (existing default; per-tourist 1-365) - confirm | Deleted; cached last position cleared |
| 4 | Live / emergency location | `emergency_location_pings`, `incidents.lat/lng` | Direct responders during an SOS | Required for SOS | Admin, assigned responders | Handling station | None established | Trail of **resolved** incidents deleted; open incident retained |
| 5 | SOS & incident records | `incidents`, `incident_events`, `alerts`, `fall_events` | Emergency response record | Required | Admin, responders | Police stations (incl. transfers) | None established | Resolved: de-identified (link removed, text blanked, coords rounded ~1 km). **Open: retained** with reason |
| 6 | Digital identity | `tourist_id_tokens`, `id_blocks`, `tourist_id_scans` | Verify you in the field | Required | You; scanner role | No (anchor holds a hash only) | None established | Tokens + chain deleted; scan log de-identified (responder-side record kept) |
| 7 | E-FIR drafts | `efirs` | Police report you asked for | Optional | Admin | Police | **Held** pending legal review | Retained as a documented exception (admins only) |
| 8 | Guardian info | `trip_guardians` | Family live-status link | Optional (consent) | You, the guardian | Guardian | None established | Deleted; links revoked immediately |
| 9 | Device & session | `devices`, `push_subscriptions`, `revoked_tokens`, `password_reset_tokens` | Wearables, push, auth | Optional / security | System | Browser push service | Tokens: at their own expiry (technical) | Deleted |
| 10 | Notification log | `notification_logs` | Delivery proof, retries | Required | Admin | SMS/push provider | None established | Contact + text removed, row kept without a person |
| 11 | Security & audit logs | `audit_logs`, `deletion_jobs` | Security, accountability | Required | Admin | No | None established | E-mail / digital ID replaced; counts only in job rows |
| 12 | Consent records, grievances | `consent_records`, `privacy_grievances` | Prove consent; handle complaints | Required | You; admin | No | None established | Link to you removed; opaque `subject_ref` kept. Open grievances retained until resolved |
| 13 | Medical profile | `medical_profiles` | Medical emergency help | Optional | You; responders via QR only | Responder | None established | Deleted |

## 3. Components

**Consent** - `consent_records` is an append-only ledger (consent id, purpose, data categories, status,
consent version, notice version, `consented_at`, `withdrawn_at`, source, evidence of the affirmative
action). Purposes come from `PRIVACY_PURPOSES`; each is `kind: consent` (grantable/withdrawable) or
`kind: notice` (needed for the service / non-consent basis to be confirmed - shown, never pre-ticked,
not pretended to be withdrawable). Registration has a privacy step with every box **unticked**; two
statements are required (notice read, 18+), optional purposes are separate. Withdrawal is a real effect:
location → collection stops (`POST /location` → 409) · guardian → links revoked and new ones refused ·
notifications → push subscriptions deactivated. History is kept; erasure is a separate action.

**Retention** - `retention_policies` (seeded from config, admin-editable), `retention_exceptions` (reason,
basis, review date, overdue flag, release), `deletion_jobs` (counts only). `privacy_retention.run()` is
idempotent, records a failed job and carries on, and is retried by the next tick. Open emergencies and
holds are never deleted and are counted as *retained*.

**Location privacy** - list/range/page; delete by ids, date range, or all; confirmation dialog; open
emergency data kept (and reported); SOS never disabled; own rows only.

**Export** - password re-entry → snapshot built from the registry → Fernet-encrypted file in
`PRIVACY_EXPORT_DIR` → owner-only authenticated download (others get 404, admins 403) → expires after
`PRIVACY_EXPORT_TTL_HOURS`, cleaned by the scheduler → never e-mailed. JSON, or a zip of CSVs.
Excludes password hashes, QR/guardian/device/push secrets, the duress PIN and anyone else's data.

**Account deletion** - preview → password → typed `DELETE` → sessions revoked, tracking stopped, login
blocked → background processing (scheduler retries failures, bounded) → status `Deletion requested /
in progress / completed / partially retained / failed`. Nothing is called "completed" until it finished.
What happens per category follows the live policy (`delete | anonymize | retain`), with open emergencies,
open grievances and holds always kept and re-checked every tick. Backups and external processors are
**reported, not pretended**: see the status page text and `PRIVACY_EXTERNAL_PROCESSORS`.

**Grievances** - id, status, due date (`PRIVACY_GRIEVANCE_RESPONSE_DAYS`), response history, resolution time.

## 4. API

Public: `GET /api/privacy/notice`, `GET /api/privacy/deletion-status/{request_id}`.
Tourist (own record only): `GET .../privacy/overview`, `GET|POST .../consents`, `GET .../consents/history`,
`POST .../consents/{purpose}/withdraw`, `GET .../location-history`, `POST .../location-history/delete`,
`POST|GET .../data-export`, `GET .../data-export/{id}[/download]`, `GET .../account-deletion[/preview]`,
`POST .../account-deletion`, `POST .../account-deletion/{rid}/confirm|cancel`, `POST|GET .../grievances`,
`GET .../grievances/{gid}`, `POST .../grievances/{gid}/reply` (all under `/api/tourists/{id}/`).
Admin: `/api/admin/privacy/{inventory, retention/policies, retention/exceptions, retention/jobs,
retention/run, deletion-requests, deletion-requests/process, grievances}`.
Existing and kept: `GET|PATCH /tourists/{id}/privacy`, `DELETE /tourists/{id}/location-history`.

## 5. Security measures

Backend authorization on every route (tourist = own record only; admins cannot read tourist consent,
location, export or deletion by id) · password re-authentication, rate-limited, for export and deletion ·
append-only consent (no edit/delete endpoint) · encrypted export files, short-lived, owner-scoped ·
audit rows hold counts and opaque refs, never coordinates, contents or tokens · secrets excluded from
exports by a deny-list · registry test fails the build if a new table with a `tourist_id` is not covered.

## 6. Children (under 18)

The system collects **no date of birth** and has no child accounts. Registration records an explicit
"I am 18 or older" declaration. There is no behavioural monitoring or advertising. Because location
tracking of a minor and parental consent have specific requirements, **do not onboard minors** until
legal review has produced a verifiable-parental-consent design; the declaration is not age verification.

## 7. Still requires legal review (not decided by this software)

1. Commencement dates and whether each provision is currently in force; amendments/corrigenda.
2. Lawful basis per purpose - which are consent, which are "legitimate uses" (e.g. emergency response),
   and the wording of the notice (languages, form, content required by the Rules).
3. Every retention period, and whether any statute requires keeping SOS/incident, e-FIR or audit records.
4. Grievance response time and the Data Protection Officer / contact details to publish.
5. Children: age assurance and verifiable parental consent, if minors are to be supported at all.
6. Cross-border transfer, processors (SMS/push vendors, map/LLM providers) and their contracts.
7. Backup lifecycle (`PRIVACY_BACKUP_ROTATION_DAYS`, `PRIVACY_BACKUP_NOTE`) and breach-notification process.
8. Whether being refused login during a pending deletion is acceptable, and the grace period.

## 8. Known limitations

* No external processor has an automated erase API wired in; configured processors are reported as
  "manual notification required".
* Backups are not edited (and cannot be, from the application); they age out with rotation.
* Retention handlers exist for location, SOS (de-identify), notification log, audit log, session tokens
  and exports. Other categories are erased on account deletion but have no timer until a handler is added.
* The failed-lookup / re-auth rate limiter is in-memory per process unless `REDIS_URL` is set.
* Existing data is not retro-fitted: tourists who registered earlier show consent as **Not set** and are
  *not* treated as having withdrawn; they are asked to review.
