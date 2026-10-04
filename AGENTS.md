# Project Specification: Amalthea

**Core Objective:** An open-source, developer-friendly Security Case Management and Orchestration platform. Amalthea bridges the gap between pure documentation/case logging tools (like TheHive) and heavy automation workflows (like Tines) by treating **Alerts, Cases, Observables, and Automated Playbook Triggers as deeply interconnected, first-class objects**.

---

## 1. System Architecture Overview
Amalthea is built around a decoupled **headless API-first design** with a lightweight, real-time UI layer.
*   **Backend:** Django 5.x + DRF + Django Channels (ASGI, Daphne) for API and real-time features, with Celery/Redis for asynchronous automation and webhooks.
*   **Database:** PostgreSQL (Core state, relations, audit logs) + Redis (Task queue for asynchronous playbooks/automations, Channels channel layer, and WebSockets).
*   **Frontend:** Django templates (server-rendered) with HTMX for interactivity, Tailwind CSS, and Django Channels WebSockets for realtime updates; keyboard-driven UI (Linear/Obsidian-inspired), dark theme.

---

## 2. Core Functional Modules

### Module A: Universal Ingestion (The "Gossamer" Feed)
Instead of forcing structured schemas on external tools, Amalthea uses a schema-agnostic receiver.
*   **The Endpoint:** Exposed HTTP POST `/api/v1/alerts/webhook/{source_id}`.
*   **The Logic:** Accepts raw JSON payloads from any security tool (SIEM, EDR, CloudTrail, phishing boxes). 
*   **Mapping Engine:** A simple, built-in JSON-path mapping layer that pulls core values out of raw telemetry (e.g., mapping `src_ip`, `target_user`, or `file_hash`) into a standardized internal `Alert` schema without losing the raw original context.

### Module B: Incident Lifecycle & Case Tracking
Tracks the journey from an unverified alert to a closed case file.
*   **Alert-to-Case Escalation:** Alerts can be manually triaged or automatically bundled into a `Case` based on specific correlating attributes (e.g., identical destination IP within a 10-minute window).
*   **Collaborative Live Ledger:** Real-time multi-analyst note-taking powered by Markdown. Every comment, task assignment, and state transition must emit an event via WebSockets to keep the UI synced across the team.

### Module C: Smart Observables (The Forensic Layer)
Every artifact extracted from a case (IP addresses, domains, file hashes, usernames) is an `Observable`.
*   Unlike basic ticketing tools, an Observable is a standalone database entity.
*   If `192.168.1.50` appears across 4 different cases over 6 months, Amalthea automatically links those cases together inside the UI to instantly highlight an advanced persistent pattern.

### Module D: The Orchestration Gateway (The Bridging Logic)
This is where Amalthea differentiates itself from standard case logs. Instead of relying on a completely separate SOAR tool, the case management engine actively drives the automation.
*   **Event-Driven Triggers:** State changes inside a case (e.g., "Alert Ingested", "Observable of type FILE_HASH added", "Case Status changed to Critical") immediately emit an internal system hook.
*   **Action Execution:** These triggers fire asynchronous backend workers (via Celery/Redis) to execute predefined Python scripts or outbound API requests (e.g., querying VirusTotal, checking Active Directory for a user, or blocking an IP in a firewall).
*   **Feedback Loop:** The results of these automated actions are automatically pushed directly back into the case ledger as structured timeline entries or appended notes.

---

## 3. Core Database Schema (Entity-Relationship Blueprint)

To build the foundational database layer, implement these five core relational models:

[ Alert ] 1 -------- 0..1 [ Case ] 1 -------- 0..* [ Task ]
1
|
+---------------- 0..* [ Observable ]
|
+---------------- 0..* [ AutomationRun ]

*   **Alert:** `id (UUID)`, `title`, `severity`, `source`, `raw_payload (JSONB)`, `status (New/Triaged/Dismissed)`, `case_id (FK, Nullable)`.
*   **Case:** `id (UUID)`, `title`, `description (Markdown)`, `severity`, `status (Open/Investigating/Containment/Closed)`, `owner_id (FK)`, `created_at`.
*   **Observable:** `id (UUID)`, `case_id (FK)`, `type (IP/Domain/Hash/User)`, `value (String)`, `enrichment_data (JSONB)`, `created_at`.
*   **Task:** `id (UUID)`, `case_id (FK)`, `title`, `status (Todo/InProgress/Done)`, `assigned_to (FK)`.
*   **AutomationRun:** `id (UUID)`, `case_id (FK)`, `playbook_name`, `status (Pending/Running/Success/Failed)`, `output_log (Text)`, `triggered_by_observable_id (FK, Nullable)`.

---

## 4. Minimum Viable Product (MVP) Success Criteria
A coding agent should prioritize getting this single end-to-end loop running before polishing the UI:
1.  **Ingest:** Send a mock raw JSON webhook (e.g., a simulated phishing alert) to `/api/v1/alerts/webhook/`.
2.  **Escalate:** Trigger an API call that promotes that alert into an active `Case`.
3.  **Extract:** Parse out an email address observable from that case file.
4.  **Automate:** Automatically trigger an outbound API request when that observable is created, then cleanly append the result back into the case timeline documentation.
