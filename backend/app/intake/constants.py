"""Shared intake constants — imported by service / teams / routing / agents to
avoid circular imports."""

# Triage removed: a filed request is 'open' and flows to its workflow. The old
# 'awaiting_triage' + 'in_review' split (nobody-looked vs being-worked) only
# existed because of triage, so they collapse to a single 'open'.
STATUSES = {"open", "escalated", "approved", "closed"}
TERMINAL_STATUSES = {"approved", "closed"}
OPEN_STATUSES = {"open", "escalated"}
# Legacy values still tolerated on read from older rows (migrated to 'open').
LEGACY_OPEN_STATUSES = {"awaiting_triage", "in_review"}
PRIORITIES = ["Low", "Medium", "High", "Critical"]

SPINE_HEAD = ["new"]
SPINE_DEFAULT_MID = ["assigned", "review"]
SPINE_TAIL = ["complete"]
STAGE_LABELS = {
    "new": "Submitted",
    "assigned": "Assigned",
    "review": "In Review",
    "complete": "Complete",
}

# SLA posture thresholds (fraction of window elapsed)
AT_RISK = 0.70
OVERDUE = 1.0
