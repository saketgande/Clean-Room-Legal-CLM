"""Shared intake constants — imported by service / teams / routing / agents to
avoid circular imports."""

STATUSES = {"awaiting_triage", "in_review", "escalated", "approved", "closed"}
TERMINAL_STATUSES = {"approved", "closed"}
OPEN_STATUSES = {"awaiting_triage", "in_review", "escalated"}
PRIORITIES = ["Low", "Medium", "High", "Critical"]

SPINE_HEAD = ["new", "triage"]
SPINE_DEFAULT_MID = ["assigned", "review"]
SPINE_TAIL = ["complete"]
STAGE_LABELS = {
    "new": "Submitted",
    "triage": "AI Triage",
    "assigned": "Assigned",
    "review": "In Review",
    "complete": "Complete",
}

# SLA posture thresholds (fraction of window elapsed)
AT_RISK = 0.70
OVERDUE = 1.0

# Confidence gate (Part 0 / research): below this the agent may NOT auto-send.
AUTO_SEND_THRESHOLD = 0.75
