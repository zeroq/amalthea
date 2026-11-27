"""The case ledger's single write path, and the read path that resyncs a dropped client.

Two rules that look like plumbing are the actual product here:

* **A ledger entry that never committed is not a ledger entry.** The publish is therefore a
  `transaction.on_commit` hook, never a bare call — the same rule the automation dispatch follows
  for `execute_run.delay` (AC6.6). A rolled-back case edit emits nothing, so no client can render
  an event the database does not have.
* **A committed entry the team cannot see live is half an entry.** Every `TimelineEvent` row in the
  product is created by `append_timeline_event`, so the publish cannot be forgotten at a call site
  — the choke-point scan in `tests/conformance/test_realtime.py` fails the build if a site goes
  back to `TimelineEvent.objects.create`.

No signals: this repo's culture is explicit calls, and a guard can enforce explicit calls where it
cannot enforce a signal's ordering.

`events_after` is the read half of the same contract. The server's only ordering promise is
`(date, id)` — the keyset pagination tuple the timeline index is built for — so the ordering is
defined once, next to the write, instead of once per consumer.
"""

from .event_log import append_timeline_event
from .read import events_after

__all__ = ["append_timeline_event", "events_after"]
