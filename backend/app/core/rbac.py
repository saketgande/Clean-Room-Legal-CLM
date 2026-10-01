from collections.abc import Iterable

from app.core.config import settings

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

# Org-unit hierarchy + delegation self-service:
#   org_unit:read     — view the org-unit tree (every picker, incl. the
#                        self-service delegation screen, needs this)
#   delegation:manage — create/list/revoke one's OWN delegations
# Both are granted to every default role since they gate read-only/self-scoped
# actions, not administration of the hierarchy itself (that stays behind
# admin_panel:access).
ORG_STRUCTURE_PERMISSIONS = {"org_unit:read", "delegation:manage"}

# Menu/screen-level security (VIEW/ADD/EDIT/DELETE):
#   menu:read            — fetch one's own resolved menu tree / own
#                           screen-access resolution / the action-level
#                           reference list. Granted to every default role
#                           since the sidebar is fetched on every page load
#                           by every user; gating it on admin_panel:access
#                           would blank the navigation for non-admins.
#   screen_access:read   — list the screen catalog and existing role->screen
#                           grants (the admin screen's reads). Admin only.
#   screen_access:manage — create/modify/revoke a screen-access grant.
#                           Admin only.
MENU_SECURITY_PERMISSIONS = {"menu:read", "screen_access:read", "screen_access:manage"}

# Condition-driven approval chains (feature 004):
#   approval_chain:read         — view chain instances, materialized required-
#                                  approver lists, condition explanations and
#                                  history. Granted broadly (every default
#                                  role) since any user may be a requester or
#                                  an assigned approver on a chain instance.
#   approval_chain:manage       — define/modify/deactivate chain definitions,
#                                  steps, base requirements and condition
#                                  rules; also the FR-20 "which role has no
#                                  eligible holder" blocked-step view.
#                                  Admin only.
#   approval_chain:decide       — record an approve/reject decision against a
#                                  materialized requirement (FR-15). Granted
#                                  to the roles that already act as approvers
#                                  today (mirrors approval:decide's holders).
#   approval_chain:recalculate  — invoke the explicit "recalculate required
#                                  approvers" action (FR-9), deliberately
#                                  distinct from approval_chain:decide so an
#                                  ordinary approver can never recalculate
#                                  their own chain instance. Admin only.
APPROVAL_CHAIN_PERMISSIONS = {
    "approval_chain:read",
    "approval_chain:manage",
    "approval_chain:decide",
    "approval_chain:recalculate",
}

ALL_PERMISSIONS = (
    CONTRACT_PERMISSIONS
    | CONTRACT_FILE_PERMISSIONS
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
    | ORG_STRUCTURE_PERMISSIONS
    | MENU_SECURITY_PERMISSIONS
    | APPROVAL_CHAIN_PERMISSIONS
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
        "assistant:use",
        "workflow:read",
        "obligation:read",
        "intake:create",  # any employee can file + track their own requests
        "trademark:read",
        "trademark:create",
        "org_unit:read",
        "delegation:manage",
        "menu:read",
        "approval_chain:read",
    },
    LEGAL_REVIEWER_ROLE_NAME: {
        "contract:read",
        "contract:update",
        "contract:redline",
        "contract:lifecycle_override",
        "contract_file:read",
        "contract_file:create",
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
        "org_unit:read",
        "delegation:manage",
        "menu:read",
        "approval_chain:read",
        "approval_chain:decide",
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
        "org_unit:read",
        "delegation:manage",
        "menu:read",
        "approval_chain:read",
        "approval_chain:decide",
    },
}


def has_permission(user_permissions: Iterable[str], required_permission: str) -> bool:
    # DISABLE_RBAC turns every check into a pass so local dev can walk every
    # screen without seeding roles. It is not a dev-only accident waiting to
    # ship: validate_runtime_settings() refuses to boot a staging/production
    # environment while it is set, because is_org_admin() in core/access.py
    # consults this function, which would make every authenticated user an org
    # admin and silently void the ~22 admin shortcuts -- including
    # clearance_permits() in contracts/access.py, so confidentiality/MAC
    # classification would read as enforced while letting everything through.
    #
    # Unaffected either way (never routed through here): org_id tenant
    # isolation, ethical walls (walls/service.py, which bind admins too), and
    # delegation-of-authority (authority/service.py).
    if settings.disable_rbac:
        return True
    permissions = set(user_permissions)
    return required_permission in permissions or "*" in permissions
