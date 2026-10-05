"""Which permissions open which screen — the default screen access a role gets.

Copied from migration 0043 (which seeds it for organisations that existed when
it ran), minus the Matters screens (removed in 0062/0065) and plus Templates
(0068). The app needs it at run time too: an organisation created after the
migrations — every fresh install — otherwise gets no screen access at all and
sees no Legal Intake, no Contracts, nothing (menu_security/bootstrap.py).
Keep the two in step when a screen is added.
"""

_ALL = None  # sentinel: no permission gate today -> seed DELETE unconditionally

_TRADEMARK_READ = frozenset({"trademark:read", "trademark:search", "trademark:integrations_manage"})
_TRADEMARK_WRITE = frozenset({"trademark:create", "trademark:update", "trademark:extract"})
_PLAYBOOK_READ = frozenset({"playbook:read"})
_PLAYBOOK_WRITE = frozenset(
    {"playbook:create", "playbook:update", "playbook:delete", "playbook:run", "playbook:publish"}
)

SCREEN_CATALOG: list[tuple[str, str, str, str, frozenset[str] | None, frozenset[str] | None]] = [
    # code, name, module, route_path, read_perms, write_perms (None == ALL)
    ("home", "Ask Aegis", "Intelligence", "/", frozenset(), _ALL),
    ("my_work", "My Work", "Workspace", "/my-work", frozenset(), _ALL),
    ("notifications", "Notifications", "Workspace", "/notifications", frozenset(), _ALL),
    ("search", "Search", "Workspace", "/search", frozenset(), _ALL),
    ("prompts", "Prompt Library", "Intelligence", "/prompts", frozenset(), _ALL),
    (
        "assistant",
        "Assistant",
        "Intelligence",
        "/assistant",
        frozenset({"assistant:use"}),
        frozenset({"assistant:use_ai_tools"}),
    ),
    ("contract_brain", "Contract Brain", "Intelligence", "/brain", frozenset({"contract:read"}), frozenset()),
    (
        "tabular_reviews",
        "Tabular Review",
        "Intelligence",
        "/tabular-reviews",
        frozenset({"contract:read"}),
        frozenset({"contract:update"}),
    ),
    (
        "tabular_review_detail",
        "Tabular Review detail",
        "Intelligence",
        "/tabular-reviews/[id]",
        frozenset({"contract:read"}),
        frozenset({"contract:update"}),
    ),
    ("playbooks", "Playbooks", "Intelligence", "/playbooks", _PLAYBOOK_READ, _PLAYBOOK_WRITE),
    ("playbook_detail", "Playbook detail", "Intelligence", "/playbooks/[id]", _PLAYBOOK_READ, _PLAYBOOK_WRITE),
    ("playbook_builder", "Playbook builder", "Intelligence", "/playbooks/build", _PLAYBOOK_READ, _PLAYBOOK_WRITE),
    (
        "contracts",
        "Contracts",
        "Workspace",
        "/contracts",
        frozenset({"contract:read"}),
        frozenset({"contract:create", "contract:update"}),
    ),
    (
        "contract_detail",
        "Contract detail",
        "Workspace",
        "/contracts/[id]",
        frozenset({"contract:read"}),
        frozenset({"contract:create", "contract:update"}),
    ),
    (
        "intake",
        "Legal Intake",
        "Workspace",
        "/intake",
        frozenset({"intake:read"}),
        frozenset({"intake:create", "intake:update"}),
    ),
    (
        "delegations",
        "Delegations",
        "Workspace",
        "/delegations",
        frozenset({"delegation:manage"}),
        frozenset({"delegation:manage"}),
    ),
    (
        "approvals",
        "Approvals",
        "Lifecycle",
        "/approvals",
        frozenset({"approval:read"}),
        frozenset({"approval:decide", "approval:admin"}),
    ),
    (
        "signatures",
        "Signatures",
        "Lifecycle",
        "/signatures",
        frozenset({"contract:read"}),
        frozenset({"contract:sign"}),
    ),
    (
        "obligations",
        "Obligations",
        "Lifecycle",
        "/obligations",
        frozenset({"obligation:read"}),
        frozenset({"obligation:update"}),
    ),
    ("sla", "SLA", "Lifecycle", "/sla", frozenset({"intake:read"}), frozenset({"intake:update"})),
    (
        "notices",
        "Notices",
        "Lifecycle",
        "/notices",
        frozenset({"notice:read"}),
        frozenset({"notice:create", "notice:update", "admin_panel:access"}),
    ),
    (
        "notice_detail",
        "Notice detail",
        "Lifecycle",
        "/notices/[id]",
        frozenset({"notice:read"}),
        frozenset({"notice:create", "notice:update", "admin_panel:access"}),
    ),
    ("renewals", "Renewals", "Lifecycle", "/renewals", frozenset({"contract:read"}), frozenset({"contract:renew"})),
    ("trademarks", "Trademarks", "Workspace", "/trademarks", _TRADEMARK_READ, _TRADEMARK_WRITE),
    (
        "trademark_detail",
        "Trademark detail",
        "Workspace",
        "/trademarks/[id]",
        _TRADEMARK_READ,
        _TRADEMARK_WRITE,
    ),
    (
        "trademark_calendar",
        "Trademark calendar",
        "Workspace",
        "/trademarks/calendar",
        _TRADEMARK_READ,
        _TRADEMARK_WRITE,
    ),
    (
        "trademark_intake",
        "Trademark intake",
        "Workspace",
        "/trademarks/intake",
        _TRADEMARK_READ,
        _TRADEMARK_WRITE,
    ),
    (
        "trademark_extract",
        "Trademark extract",
        "Workspace",
        "/trademarks/extract",
        _TRADEMARK_READ,
        _TRADEMARK_WRITE,
    ),
    (
        "trademark_integrations",
        "Trademark integrations",
        "Workspace",
        "/trademarks/integrations",
        _TRADEMARK_READ,
        _TRADEMARK_WRITE,
    ),
    (
        "trademark_reports",
        "Trademark reports",
        "Workspace",
        "/trademarks/reports",
        _TRADEMARK_READ,
        _TRADEMARK_WRITE,
    ),
    (
        "workflow_builder",
        "Workflows",
        "Admin",
        "/workflow-builder",
        frozenset({"workflow:read"}),
        frozenset({"workflow:create", "workflow:update", "workflow:share"}),
    ),
    (
        "workflow_builder_detail",
        "Workflow detail",
        "Admin",
        "/workflow-builder/[id]",
        frozenset({"workflow:read"}),
        frozenset({"workflow:create", "workflow:update", "workflow:share"}),
    ),
    (
        "ai_usage",
        "AI Usage & Cost",
        "Admin",
        "/ai-usage",
        frozenset({"admin_panel:access"}),
        frozenset({"admin_panel:access"}),
    ),
    (
        "jobs",
        "Background Jobs",
        "Admin",
        "/jobs",
        frozenset({"admin_panel:access"}),
        frozenset({"admin_panel:access"}),
    ),
    (
        "admin",
        "Roles & teams",
        "Admin",
        "/admin",
        frozenset({"admin_panel:access"}),
        frozenset({"admin_panel:access"}),
    ),
    (
        "org_structure",
        "Org structure",
        "Admin",
        "/org-structure",
        frozenset({"admin_panel:access"}),
        frozenset({"admin_panel:access"}),
    ),
    (
        "screen_access",
        "Screen access",
        "Admin",
        "/screen-access",
        frozenset({"admin_panel:access"}),
        frozenset({"admin_panel:access"}),
    ),
    ("templates", "Templates", "Intelligence", "/templates", _PLAYBOOK_READ, _PLAYBOOK_WRITE),
]
