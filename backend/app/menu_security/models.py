from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import relationship

from app.core.database import (
    ActorTrackedMixin,
    Base,
    IdMixin,
    OrgScopedMixin,
    SoftDeleteMixin,
    TableNameMixin,
    TimestampMixin,
)


class Screen(TableNameMixin, IdMixin, ActorTrackedMixin, SoftDeleteMixin, TimestampMixin, Base):
    """One row per Next.js page route (FR-1). Platform-wide structural
    catalog — deliberately NOT org-scoped (FR-24: "the inventory of page
    routes is shared across the platform; only the grants against it are
    org-scoped"). Created and maintained exclusively by Alembic migrations;
    there is no CRUD endpoint for this table.
    """

    code = Column(String(80), nullable=False, unique=True, index=True)
    name = Column(String(160), nullable=False)
    module = Column(String(80), nullable=False)
    route_path = Column(String(200), nullable=False, unique=True, index=True)


class ActionLevel(TableNameMixin, IdMixin, ActorTrackedMixin, SoftDeleteMixin, TimestampMixin, Base):
    """Small seeded reference table: VIEW/ADD/EDIT/DELETE in a strict rank
    order (FR-4). Never written at runtime beyond the migration seed.
    """

    code = Column(String(10), nullable=False, unique=True)
    rank = Column(Integer, nullable=False, unique=True)

    __table_args__ = (
        CheckConstraint("code IN ('VIEW', 'ADD', 'EDIT', 'DELETE')", name="code"),
        CheckConstraint("rank BETWEEN 1 AND 4", name="rank"),
    )


class MenuItem(TableNameMixin, IdMixin, ActorTrackedMixin, SoftDeleteMixin, TimestampMixin, Base):
    """A node in the application's navigable menu tree (FR-2). Structural
    application data (FR-24) — a grouping node (``menu_type='group'``,
    ``screen_id IS NULL``) or a node linking to exactly one screen
    (``menu_type='screen_link'``, ``screen_id`` set). Seeded and maintained
    exclusively by Alembic migrations; not user-editable, so no cycle
    prevention logic is needed beyond the no-self-parent CHECK.
    """

    parent_id = Column(String(36), ForeignKey("menu_item.id", ondelete="RESTRICT"), nullable=True)
    label = Column(String(160), nullable=False)
    icon = Column(Text, nullable=True)
    sequence_order = Column(Integer, nullable=False, default=0)
    menu_type = Column(String(20), nullable=False)
    screen_id = Column(String(36), ForeignKey("screen.id", ondelete="RESTRICT"), nullable=True, index=True)

    __table_args__ = (
        CheckConstraint("menu_type IN ('group', 'screen_link')", name="menu_type"),
        CheckConstraint(
            "(menu_type = 'screen_link' AND screen_id IS NOT NULL) "
            "OR (menu_type = 'group' AND screen_id IS NULL)",
            name="screen_link",
        ),
        CheckConstraint("(parent_id IS NULL OR parent_id <> id)", name="no_self_parent"),
        Index("ix_menu_item_parent_id", "parent_id"),
        Index("ix_menu_item_parent_seq", "parent_id", "sequence_order"),
    )


class RoleScreenAccess(
    TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, SoftDeleteMixin, TimestampMixin, Base
):
    """A role's maximum action level on a screen, optionally scoped to an org
    unit (FR-5, FR-18). The only org-scoped table of this feature's four.
    Revocation IS the soft delete (``deleted_at``/``deleted_by_user_id``), as
    with feature 002's ``UserRoleGrant``.

    ``role_id`` and ``org_unit_id`` are declared BY STRING (``"role.id"``,
    ``"org_unit.id"``) rather than importing ``app.auth.models`` /
    ``app.org_structure.models`` to avoid an import cycle. ``role`` and
    ``action_level`` are eagerly loaded (``lazy="joined"``) because the
    resolver (``app.core.screen_access``) reads
    ``row.role.allows_hierarchy_rollup`` on every candidate row.
    """

    role_id = Column(String(36), ForeignKey("role.id", ondelete="CASCADE"), nullable=False, index=True)
    screen_id = Column(String(36), ForeignKey("screen.id", ondelete="CASCADE"), nullable=False, index=True)
    org_unit_id = Column(
        String(36), ForeignKey("org_unit.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    max_action_level_id = Column(
        String(36), ForeignKey("action_level.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    role = relationship("Role", lazy="joined")
    action_level = relationship("ActionLevel", lazy="joined")

    __table_args__ = (
        Index(
            "uq_role_screen_access_scope",
            "role_id",
            "screen_id",
            "org_unit_id",
            unique=True,
            postgresql_where=text("org_unit_id IS NOT NULL AND deleted_at IS NULL"),
            sqlite_where=text("org_unit_id IS NOT NULL AND deleted_at IS NULL"),
        ),
        Index(
            "uq_role_screen_access_orgwide",
            "role_id",
            "screen_id",
            unique=True,
            postgresql_where=text("org_unit_id IS NULL AND deleted_at IS NULL"),
            sqlite_where=text("org_unit_id IS NULL AND deleted_at IS NULL"),
        ),
        Index("ix_role_screen_access_lookup", "org_id", "screen_id", "role_id", "deleted_at"),
        Index("ix_role_screen_access_org_unit", "org_unit_id"),
    )
