> **SUPERSEDED.** This document is the original "pgBackRest Nexus" preliminary spec.
> It has been merged with [log-collector.md](log-collector.md) into the unified
> [PLAN.md](../../PLAN.md). See [conflicts-resolved.md](../conflicts-resolved.md)
> (once written) for why specific choices in this doc were changed.
> Kept here for historical context only — do not edit.

---

# Project Plan: pgBackRest Nexus (Multi-Cluster Control Plane)

## 1. Vision & Role
**Role:** Senior Database Reliability Engineer & Full-Stack Architect.
**Goal:** Build a centralized management platform for 10+ PostgreSQL clusters using pgBackRest.
**Architecture:** Hub-and-Spoke. 
- **Nexus Central (Manager):** Central API, UI, and Task Orchestrator.
- **Nexus Node (Agent):** Lightweight service on DB hosts executing CLI commands.

---

## 2. Core Feature Set

### A. Dashboard & Fleet Visibility
- **Global Health Cards:** Total Storage, 24h Success Rate, Backup Health, and Active Alerts.
- **Virtualized Fleet List:** A high-performance grid for 10+ clusters showing status (Online/Offline/Warning).
- **WAL Archiving Health (New):** - Real-time tracking of WAL archive success/failure.
    - Alerting on "WAL Archiving Lag" (time since last segment archived).
    - Detection of gaps in the WAL sequence.
- **Alerting Integration:** - **Providers:** Native support for Slack, Microsoft Teams, PagerDuty, and SMTP. - **Triggers:** Backup failure, WAL archiving lag > 15m, Integrity check fail, and "Storage Full" forecasts.

### B. Storage & Retention Intelligence
- **Retention Visualization Timeline:** - A Gantt-style chart showing Full, Differential, and Incremental backups.
    - Visual indicators of "Safety Windows" and expiration dates per backup.
- **Forecasting & Metrics:** - Time-series tracking of repository size growth.
    - **"Runway" Calculation:** Linear regression to predict "Days until Repository Full."


### C. Configuration & Settings
- **Stanza Management:** UI to create/edit stanzas.
- **Global vs. Local Config:** Manage `pgbackrest.conf` templates centrally and push them to agents.
- **Drift Detection:** Visual indicator if local config differs from the central "Gold Image."

### D. Logging & Monitoring
- **JSON Parsing:** Ingest `pgbackrest info --output=json` for state management.
- **Streaming Logs:** Use WebSockets to stream `tail -f` output during active backup/restore jobs.
- **Success/Fail History:** Filterable historical logs with error highlighting.

### E. Control Panel (The "Ops" Hub)
- **Point-in-Time Recovery (PITR):** Visual slider/calendar to generate `--type=time --target` commands.
- **Job Scheduler:** Global calendar to prevent network saturation (staggering 10+ cluster backups).
- **One-Click Actions:** Manual Backup (Full/Diff/Incr), Integrity Check, and Restore.

---

## 3. Technical Stack
- **Backend:** FastAPI (Python) for both Hub and Agent.
- **Database:** PostgreSQL (Central) for metadata, job history, and cached cluster states.
- **Frontend:** React (Vite) + Tailwind CSS + shadcn/ui.
- **Task Queue:** Celery + Redis (essential for handling long-running restores across 10+ nodes).
- **Security:** mTLS (Mutual TLS) + JWT. Agents "phone home" to the Manager.

---

## 4. Implementation Phases (Cursor Instructions)

### Phase 1: Foundation & Security
1. Scaffold a monorepo: `/manager` and `/agent`.
2. Define the **Agent Registration Flow**: Agent pings Manager with a unique ID and JWT.
3. Implement the **mTLS Communication Layer**: Ensure only verified agents can talk to the Manager.

### Phase 2: Data Ingestion & WAL Health
1. Create Pydantic models for `pgbackrest info` JSON schema.
2. **Build WAL Monitor:** Agent checks `/var/lib/postgresql/.../pg_wal` and `pgbackrest archive-get` status.
3. Manager stores WAL health metrics in the metadata DB for sparkline visualization.

### Phase 3: The Fleet UI
1. Build the "Nexus Dashboard" (Vite/React).
2. Use a "Pull Model": Manager requests JSON from Agents every 5 mins; UI loads from Manager's cache.
3. Implement the "Fleet View" grid with WAL Health status indicators.

### Phase 4: Operations & Control
1. Implement the **Task Dispatcher**: Manager sends a "Backup" command to a specific Agent ID.
2. Build the **PITR Restore UI**: A modal with a slider that converts a visual time into a pgBackRest CLI target.
3. **Safety Logic:** The Agent must be able to execute `systemctl stop postgresql` before a restore.

---

## 5. Security Guardrails
- **No Inbound SSH:** Manager must NOT require SSH access to agents.
- **RBAC:** Implement `Viewer` (Read-only) and `Admin` (Execute Restore/Delete) roles.
- **Confirmation Flow:** All "Restore" or "Delete" actions require a "Type the Cluster Name to Confirm" modal.
