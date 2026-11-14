
## 16. Rejected Observables Management
 
- [ ] **16.1 — Implement a standalone `RejectedObservable` model.**
  Create a record for observables rejected during analysis (not yet promoted to full `Observable` entities).
  Each must link to its source `Alert`, an optional "Reason" (dropdown/Enum), and a timestamp.
  The entity is for audit and suppression, not for cross-case correlation.
- [ ] **16.2 — Integrated Suppression Check.**
  Inject logic into the Extraction Engine to filter out values that exist in the `RejectedObservable`
  table for the same organization/source. This prevents rejected items from reappearing in analyst queues.
- [ ] **16.3 — Administrative Review & Promotion.**
  Allow admins to view a list of rejected records and "Promote" them.
  Promotion should not auto-create an `Observable` but re-enable the record for manual review
  (i.e., move it from the rejection list back to an "ignored" or "flagged" state).
- [ ] **16.4 — Organization-specific visibility.**
  Ensure that rejected items are only visible/actionable within their own organization (except for SuperAdmins).
  SuperAdmins can see all records across all organizations.
- [ ] **16.5 — Promotion Confirmation.**
  Ensure the "Promote" action triggers a confirmation modal to prevent accidental promotion of large sets of data.
