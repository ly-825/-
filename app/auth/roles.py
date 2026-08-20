SUPERADMIN = "superadmin"
OWNER = "owner"
EMPLOYEE = "employee"

ACCOUNT_ROLES = frozenset({SUPERADMIN, OWNER, EMPLOYEE})
PC_ADMIN_ROLES = frozenset({SUPERADMIN, OWNER})


def can_manage_role(actor_role: str, target_role: str) -> bool:
    if actor_role == SUPERADMIN:
        return target_role in {OWNER, EMPLOYEE}
    if actor_role == OWNER:
        return target_role == EMPLOYEE
    return False
