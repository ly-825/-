import unittest

from app.auth.roles import EMPLOYEE, OWNER, SUPERADMIN, can_manage_role


class AuthRoleTest(unittest.TestCase):
    def test_account_management_matrix(self) -> None:
        self.assertTrue(can_manage_role(SUPERADMIN, OWNER))
        self.assertTrue(can_manage_role(SUPERADMIN, EMPLOYEE))
        self.assertTrue(can_manage_role(OWNER, EMPLOYEE))
        self.assertFalse(can_manage_role(OWNER, OWNER))
        self.assertFalse(can_manage_role(OWNER, SUPERADMIN))
        self.assertFalse(can_manage_role(EMPLOYEE, EMPLOYEE))
        self.assertFalse(can_manage_role(SUPERADMIN, SUPERADMIN))


if __name__ == "__main__":
    unittest.main()
