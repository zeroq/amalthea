"""Built-in enrichment probe — a no-network action for offline verification.

This is the documented example script. It exercises the real dispatch, the real loader
resolution, the real `AutomationRun` lifecycle and the real timeline write, with no
socket involved.

It takes `**values` rather than a named parameter on purpose: `run_python_action`
forwards the same interpolation names an `http` action gets, so an action declaring a
narrower signature would fail on a perfectly normal binding — a confusing way to learn
the calling convention.
"""

NAME = "enrichment_probe"
DESCRIPTION = "A no-network enrichment probe that echoes the subject and type for verification."
TRIGGER_HINT = "observable.created"


def run(**values: object) -> str:
    """Entry point. `values` carries the same interpolation names an http action gets."""
    subject = (
        values.get("observable") or values.get("case_id") or values.get("case") or "(no subject)"
    )
    kind = values.get("observable_type") or "case"
    return f"enrichment probe: no network lookup performed for {kind} {subject!r}"
