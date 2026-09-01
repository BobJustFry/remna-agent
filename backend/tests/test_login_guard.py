import unittest

from fastapi import HTTPException

from app.services import login_guard


class LoginGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        login_guard._buckets.clear()

    def test_lockout_after_limit(self) -> None:
        ip, user = "203.0.113.9", "admin"
        for _ in range(login_guard.FAIL_LIMIT):
            login_guard.register_login_failure(ip, user)
        with self.assertRaises(HTTPException) as ctx:
            login_guard.assert_login_allowed(ip, user)
        self.assertEqual(ctx.exception.status_code, 429)

    def test_success_clears(self) -> None:
        ip, user = "203.0.113.10", "admin"
        login_guard.register_login_failure(ip, user)
        login_guard.register_login_success(ip, user)
        login_guard.assert_login_allowed(ip, user)


if __name__ == "__main__":
    unittest.main()
