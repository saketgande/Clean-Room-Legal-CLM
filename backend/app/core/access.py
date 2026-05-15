from app.auth.models import User
from app.core.rbac import ADMIN_ROLE_NAME, has_permission


def is_org_admin(user: User) -> bool:
    if has_permission(user.permission_values, "admin_panel:access"):
        return True
    return any(role.name == ADMIN_ROLE_NAME for role in user.roles)
