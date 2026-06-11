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

ALL_PERMISSIONS = (
    CONTRACT_PERMISSIONS
    | CONTRACT_FILE_PERMISSIONS
    | PROJECT_PERMISSIONS
    | ASSISTANT_PERMISSIONS
    | WORKFLOW_PERMISSIONS
    | PLAYBOOK_PERMISSIONS
    | APPROVAL_PERMISSIONS
    | OBLIGATION_PERMISSIONS
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
    },
    APPROVER_ROLE_NAME: {
        "contract:read",
        "contract:approve",
        "approval:read",
        "approval:decide",
        "contract_file:read",
    },
}


def has_permission(user_permissions: Iterable[str], required_permission: str) -> bool:
    permissions = set(user_permissions)
    return required_permission in permissions or "*" in permissions
