"""Merge approver groups, intake teams and "pools" into one Teams list.

Who does a piece of work was decided in several places: intake teams (also
called pools) for owners and review steps, approver groups for approval steps,
a fixed department list in the workflow builder, and hard-coded token→group
maps in the engine that guessed between them by name. Now a workflow step
names a team directly (``config.team_id``) and approvals point at a team.

This migration, per org:
- turns every approver group into a team (joining an existing team of the
  same name), keeping its members;
- relinks approval rungs and "group" access grants to those teams;
- rewrites workflow steps' role/department tokens into ``team_id`` — the
  token→team table below is used ONCE, here, and nowhere at runtime;
- marks one team as the default intake team (replacing the Tier 1/Tier 2
  complexity fallback);
- drops the approver group tables.
Downgrade restores the empty structure only.
"""

import json
import re
import uuid

import sqlalchemy as sa

from alembic import op

revision = "0056_merge_into_teams"
down_revision = "0055_drop_intake_gates"
branch_labels = None
depends_on = None

# One-time translation of the old step tokens (library role tokens and the
# builder's fixed department names) to default team keys.
_DEFAULT_TEAMS = {
    "legal_counsel": "Legal Counsel", "paralegals": "Paralegals", "finance": "Finance",
    "procurement": "Procurement", "compliance": "Compliance", "executive": "Executive",
}
_TOKEN_TO_TEAM = {
    "attorney": "legal_counsel", "legal_counsel": "legal_counsel", "legal & ip": "legal_counsel",
    "paralegal": "paralegals", "gc": "executive", "board": "executive", "executive": "executive",
    "legal_ops": "compliance", "compliance": "compliance", "quality & compliance": "compliance",
    "privacy / dpo": "compliance", "risk & compliance": "compliance",
    "finance": "finance", "finance & tax": "finance", "procurement": "procurement",
}
_REQUESTER = {"requester", "the requester", "business owner"}


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:50] or "team"


def upgrade() -> None:
    op.add_column("intake_team", sa.Column("is_default_intake", sa.Boolean(), nullable=False,
                                           server_default=sa.false()))
    op.add_column("approval_request", sa.Column(
        "approver_team_id", sa.String(36),
        sa.ForeignKey("intake_team.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_approval_request_approver_team_id", "approval_request", ["approver_team_id"])
    if not op.get_context().as_sql:
        _move_data(op.get_bind())
    op.drop_column("approval_request", "approver_group_id")
    op.execute("DROP TABLE IF EXISTS approver_group_member")
    op.execute("DROP TABLE IF EXISTS approver_group")


def _move_data(bind) -> None:
    now = sa.text("now()")
    teams: dict[tuple[str, str], str] = {}  # (org_id, lower name or key) -> team id

    def load(org_id):
        for tid, key, name in bind.execute(sa.text(
                "SELECT id, key, name FROM intake_team WHERE org_id=:o"), {"o": org_id}):
            teams[(org_id, key.lower())] = tid
            teams[(org_id, name.lower())] = tid

    def ensure(org_id, name, description=None, key=None, active=True):
        if not any(k[0] == org_id for k in teams):
            load(org_id)
        tid = teams.get((org_id, name.lower())) or (key and teams.get((org_id, key)))
        if tid:
            return tid
        base = key or _slug(name)
        k, n = base, 2
        while (org_id, k) in teams:
            k, n = f"{base}_{n}", n + 1
        tid = str(uuid.uuid4())
        bind.execute(sa.text(
            "INSERT INTO intake_team (id, org_id, key, name, description, active, strategy, sort_order,"
            " created_at, updated_at) VALUES (:id,:o,:k,:n,:d,:a,'least_loaded',100,:t,:t)"),
            {"id": tid, "o": org_id, "k": k, "n": name, "d": description, "a": active,
             "t": bind.execute(sa.select(now)).scalar()})
        teams[(org_id, k)] = tid
        teams[(org_id, name.lower())] = tid
        return tid

    group_to_team: dict[str, str] = {}
    for gid, org_id, name, desc, active in bind.execute(sa.text(
            "SELECT id, org_id, name, description, is_active FROM approver_group")).all():
        tid = ensure(org_id, name, desc, active=active)
        group_to_team[gid] = tid
        for (uid,) in bind.execute(sa.text(
                "SELECT user_id FROM approver_group_member WHERE group_id=:g"), {"g": gid}).all():
            bind.execute(sa.text(
                "INSERT INTO intake_team_member (id, org_id, team_id, user_id, capacity, active,"
                " created_at, updated_at) VALUES (:id,:o,:t,:u,0,true,now(),now())"
                " ON CONFLICT (team_id, user_id) DO NOTHING"),
                {"id": str(uuid.uuid4()), "o": org_id, "t": tid, "u": uid})

    for gid, tid in group_to_team.items():
        bind.execute(sa.text("UPDATE approval_request SET approver_team_id=:t WHERE approver_group_id=:g"),
                     {"t": tid, "g": gid})
        bind.execute(sa.text("UPDATE resource_grant SET principal_type='team', principal_id=:t"
                             " WHERE principal_type='group' AND principal_id=:g"), {"t": tid, "g": gid})

    # One default intake team per org: the old Tier 2 counsel pool if present.
    for (org_id,) in bind.execute(sa.text("SELECT DISTINCT org_id FROM intake_team")).all():
        tid = bind.execute(sa.text(
            "SELECT id FROM intake_team WHERE org_id=:o ORDER BY (key='tier2') DESC,"
            " (lower(name)='legal counsel') DESC, sort_order, name LIMIT 1"), {"o": org_id}).scalar()
        bind.execute(sa.text("UPDATE intake_team SET is_default_intake=(id=:t) WHERE org_id=:o"),
                     {"t": tid, "o": org_id})

    for table in ("workflow", "workflow_run"):
        for row_id, org_id, steps in bind.execute(sa.text(f"SELECT id, org_id, steps FROM {table}")).all():
            steps = json.loads(steps) if isinstance(steps, str) else (steps or [])
            for st in steps:
                cfg = (st or {}).get("config") or {}
                token = next((str(cfg[k]) for k in ("approver_role", "assignee_role", "dept", "escalate_role")
                              if cfg.get(k)), "")
                for k in ("approver_role", "assignee_role", "dept", "escalate_role"):
                    cfg.pop(k, None)
                t = token.strip().lower()
                if not cfg.get("team_id") and t:
                    if t in _REQUESTER:
                        cfg.setdefault("assign_by", "The requester")
                    elif t in _TOKEN_TO_TEAM:
                        key = _TOKEN_TO_TEAM[t]
                        cfg["team_id"] = ensure(org_id, _DEFAULT_TEAMS[key], key=key)
                st["config"] = cfg
            bind.execute(sa.text(f"UPDATE {table} SET steps=CAST(:s AS JSON) WHERE id=:id"),
                         {"s": json.dumps(steps), "id": row_id})


def downgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS approver_group (
            id VARCHAR(36) PRIMARY KEY, org_id VARCHAR(36) NOT NULL, name VARCHAR(255) NOT NULL,
            description TEXT, is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_by_user_id VARCHAR(36), updated_by_user_id VARCHAR(36),
            created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT uq_approver_group_org_name UNIQUE (org_id, name));
        CREATE TABLE IF NOT EXISTS approver_group_member (
            group_id VARCHAR(36) REFERENCES approver_group(id) ON DELETE CASCADE,
            user_id VARCHAR(36) REFERENCES "user"(id) ON DELETE CASCADE,
            PRIMARY KEY (group_id, user_id));
        ALTER TABLE approval_request ADD COLUMN IF NOT EXISTS approver_group_id VARCHAR(36)
            REFERENCES approver_group(id);
        """
    )
    op.drop_index("ix_approval_request_approver_team_id", table_name="approval_request")
    op.drop_column("approval_request", "approver_team_id")
    op.drop_column("intake_team", "is_default_intake")
