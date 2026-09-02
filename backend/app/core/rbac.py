from collections.abc import Iterable

CONTRACT_PERMISSIONS = {
    "contract:read",
    "contract:create",
    "contract:update",
    "contract:redline",
    "contract:approve",
    "contract:sign",
    "contract:renew",
    "contract:archive",
    "contract:lifecycle_override",
}

CONTRACT_FILE_PERMISSIONS = {
    "contract_file:read",
    "contract_file:create",
    "contract_file:update",
    "contract_file:delete",
    "contract_file:share",
}

PROJECT_PERMISSIONS = {
    "project:read",
    "project:create",
    "project:update",
    "project:delete",
    "project:share",
}

ASSISTANT_PERMISSIONS = {"assistant:use", "assistant:use_ai_tools"}

WORKFLOW_PERMISSIONS = {
    "workflow:read",
    "workflow:create",
    "workflow:update",
    "workflow:share",
}

PLAYBOOK_PERMISSIONS = {
    "playbook:read",
    "playbook:create",
    "playbook:update",
    "playbook:delete",
    "playbook:run",
    "playbook:publish",
}

APPROVAL_PERMISSIONS = {"approval:read", "approval:decide", "approval:admin"}
OBLIGATION_PERMISSIONS = {"obligation:read", "obligation:update"}
ADMIN_PERMISSIONS = {"admin_panel:access"}
USER_PERMISSIONS = {"user:read", "user:update_role", "user:approve"}

# Legal Intake (the front door). Deliberately split so employees can file and
# track their OWN requests without being able to enumerate everyone's (which
# would leak HR/litigation content):
#   create — file + read own requests (all employees)
#   read   — staff-wide queue: list/detail/timeline/SLA-legs + manage actions
#            (the staff gate; triage removed)
#   update — stage/handoff/tasks/work-status
INTAKE_PERMISSIONS = {
    "intake:create",
    "intake:read",
    "intake:update",
}

# Trademarks (IP portfolio) module. search/extract/integrations_manage are
# split from read/create because they consume paid external API quota
# (Signa, TMSearch.ai, Serper) - gated to reviewer+ by default.
TRADEMARK_PERMISSIONS = {
    "trademark:read",
    "trademark:create",
    "trademark:update",
    "trademark:search",
    "trademark:extract",
    "trademark:integrations_manage",
}

# Legal-notice register:
#   read   — see the register + a notice's timeline
#   create — file a received notice / draft an outbound one
#   update — edit, assign, change status, add notes
# Deleting is gated on admin_panel:access instead, since it destroys the
# timeline a notice's handling is evidenced by.
NOTICE_PERMISSIONS = {
    "notice:read",
    "notice:create",
    "notice:update",
}

ALL_PERMISSIONS = (
    CONTRACT_PERMISSIONS
    | CONTRACT_FILE_PERMISSIONS
    | PROJECT_PERMISSIONS
    | ASSISTANT_PERMISSIONS
    | WORKFLOW_PERMISSIONS
    | PLAYBOOK_PERMISSIONS
    | APPROVAL_PERMISSIONS
    | OBLIGATION_PERMISSIONS
    | INTAKE_PERMISSIONS
    | TRADEMARK_PERMISSIONS
    | NOTICE_PERMISSIONS
    | ADMIN_PERMISSIONS
    | USER_PERMISSIONS
)

ADMIN_ROLE_NAME = "admin"
MEMBER_ROLE_NAME = "member"
LEGAL_REVIEWER_ROLE_NAME = "legal_reviewer"
APPROVER_ROLE_NAME = "approver"

DEFAULT_ROLE_PERMISSIONS: dict[str, set[str]] = {
    ADMIN_ROLE_NAME: set(ALL_PERMISSIONS),
    MEMBER_ROLE_NAME: {
        "contract:read",
        "contract:create",
        "contract:update",
        "contract_file:read",
        "contract_file:create",
        "project:read",
        "assistant:use",
        "workflow:read",
        "obligation:read",
        "intake:create",  # any employee can file + track their own requests
        "trademark:read",
        "trademark:create",
    },
    LEGAL_REVIEWER_ROLE_NAME: {
        "contract:read",
        "contract:update",
        "contract:redline",
        "contract:lifecycle_override",
        "contract_file:read",
        "contract_file:create",
        "project:read",
        "assistant:use",
        "assistant:use_ai_tools",
        "playbook:read",
        "playbook:run",
        "obligation:read",
        "intake:create",
        "intake:read",
        "intake:update",
        "trademark:read",
        "trademark:create",
        "trademark:update",
        "trademark:search",
        "trademark:extract",
        "trademark:integrations_manage",
        # The notice register holds adverse legal communications, so it starts
        # least-privilege: legal staff only. Widen to MEMBER deliberately if
        # non-legal staff (mailroom, finance) should file what they receive.
        "notice:read",
        "notice:create",
        "notice:update",
    },
    APPROVER_ROLE_NAME: {
        "contract:read",
        "contract:approve",
        "approval:read",
        "approval:decide",
        "contract_file:read",
        "intake:create",
        "intake:read",
        "notice:read",
    },
}


def has_permission(user_permissions: Iterable[str], required_permission: str) -> bool:
    # RBAC disabled by request: every authenticated user passes every
    # permission check, regardless of role.
    #
    # Read this before assuming any admin-gated path still holds: is_org_admin()
    # in core/access.py consults this function first, so *every* authenticated
    # user is now an org admin. That silently opens the ~22 call sites that give
    # admins a shortcut, including:
    #   * clearance_permits() in contracts/access.py — so confidentiality/MAC
    #     classification is NOT enforced while this stands, even though the code
    #     reads as though it is;
    #   * _require_admin() on the debug router (dev/local only, but still).
    #
    # Genuinely still enforced, independently of this function:
    #   * org_id tenant isolation — scoped per query, never routed through here;
    #   * ethical walls (walls/service.py) — they override every ALLOW and bind
    #     admins too, by design, so the disable doesn't reach them;
    #   * delegation-of-authority (authority/service.py) — no admin shortcut.
    #
    # To restore RBAC, revert this to:
    #   permissions = set(user_permissions)
    #   return required_permission in permissions or "*" in permissions
    return True
