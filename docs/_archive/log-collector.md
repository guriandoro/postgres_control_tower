> **SUPERSEDED.** This document is the original "Diagnostic Hub / Log Collector"
> preliminary spec. It has been merged with [pgbackrest-ui-plan.md](pgbackrest-ui-plan.md)
> into the unified [PLAN.md](../../PLAN.md). See [conflicts-resolved.md](../conflicts-resolved.md)
> (once written) for why specific choices in this doc were changed
> (notably: Vector.dev and Loki were dropped in favor of a single Python agent
> shipping logs into Postgres).
> Kept here for historical context only — do not edit.

---

# PROJECT SPECIFICATION: LOG COLLECTOR & DIAGNOSTIC HUB

## 1. PROJECT VISION
The **Diagnostic Hub** transforms raw, fragmented logs from complex PostgreSQL/Patroni clusters into a **unified, searchable narrative**. By enforcing a **Global UTC Standard** and providing visual stability timelines, it eliminates diagnostic guesswork. 

## 2. UNIFIED LOG INGESTION (THE SCOPE)
* **PostgreSQL:** Errors, slow queries, and autovacuum traces.
* **Patroni:** Leadership changes and state transitions.
* **etcd:** Consensus health and **Raft Index Sync** status.
* **pgBackRest:** Backup and archiver logs.
* **OS/Kernel:** OOM Killer events and Disk I/O bottlenecks.

## 3. CORE "SUPPORT HELPER" FEATURES
* **UTC Multi-Node Correlation:** Synchronized chronological stream across all nodes.
* **Automated RCA Suggester:** Pattern-based root cause identification.
* **Role & Stability Analytics:** Gantt-style timeline for Patroni/etcd roles and durations.
* **Clock Drift Monitor:** Real-time alerts for server time deviations from UTC.

## 4. UI/UX ARCHITECTURE
* **The Pulse:** 5-minute auto-refresh with an **Instant Snap (`Zap`)** for real-time status.
* **The Surgeon:** Deep-dive log explorer with component filtering and UTC synchronization.

## 5. TECHNICAL ARCHITECTURE
* **Agent:** Vector.dev (UTC Normalization).
* **Aggregator:** Grafana Loki.
* **Backend:** Python/Go Logic Layer.
* **Frontend:** React (Dark Mode).

## 6. SUPPORT IMPACT
* Eliminates time-zone math.
* Visualizes leadership stability.
* Provides data for SLA and Post-Mortems.
