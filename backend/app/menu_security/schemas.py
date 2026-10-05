"""Pydantic schemas for the menu_security domain.

Shapes are frozen in specs/003-menu-screen-security/plan.md's "Interface
freeze" section — copy exactly, do not improvise different names/fields.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

ActionLevelCode = Literal["VIEW", "ADD", "EDIT", "DELETE"]
MenuNodeType = Literal["group", "screen_link"]


# ---------- Action levels ----------


class ActionLevelResponse(BaseModel):
    id: str
    code: ActionLevelCode
    rank: int


# ---------- Screens ----------


class ScreenResponse(BaseModel):
    id: str
    code: str
    name: str
    module: str
    route_path: str
    is_enforced: bool


# ---------- Menu tree ----------


class MenuNode(BaseModel):
    id: str
    parent_id: str | None
    label: str
    icon: str | None
    menu_type: MenuNodeType
    sequence_order: int
    screen_id: str | None
    screen_code: str | None
    route_path: str | None
    action_level: ActionLevelCode | None
    children: list["MenuNode"] = []


# Pydantic v2 self-referencing model: must rebuild after the class body so the
# forward reference to "MenuNode" resolves.
MenuNode.model_rebuild()


class MenuTreeResponse(BaseModel):
    org_unit_id: str | None
    nodes: list[MenuNode]


# ---------- My screen access ----------


class ScreenAccessEntry(BaseModel):
    screen_id: str
    screen_code: str
    route_path: str
    action_level: ActionLevelCode
    rank: int


class MyScreenAccessResponse(BaseModel):
    org_unit_id: str | None
    screens: list[ScreenAccessEntry]


# ---------- Screen-access grants ----------


class ScreenGrantCreate(BaseModel):
    role_id: str
    screen_id: str
    org_unit_id: str | None = None
    action_level: ActionLevelCode


class ScreenGrantUpdate(BaseModel):
    action_level: ActionLevelCode


class ScreenGrantResponse(BaseModel):
    id: str
    org_id: str
    role_id: str
    role_name: str
    role_is_builtin_admin: bool
    allows_hierarchy_rollup: bool
    screen_id: str
    screen_code: str
    screen_name: str
    org_unit_id: str | None
    org_unit_name: str | None
    max_action_level_id: str
    max_action_level: ActionLevelCode
    max_action_rank: int
    is_locked: bool
    is_active: bool
    revoked_at: datetime | None
    revoked_by_user_id: str | None
    created_at: datetime
    created_by_user_id: str | None
    updated_at: datetime
    updated_by_user_id: str | None
