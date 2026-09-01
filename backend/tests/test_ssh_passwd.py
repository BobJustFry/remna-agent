import unittest

from app.services.ssh_passwd import (
    PasswdDialog,
    generate_login_password,
    looks_like_password_expired,
)


class LookExpiredTests(unittest.TestCase):
    def test_warning_and_no_tty(self) -> None:
        text = (
            "WARNING: Your password has expired.\n"
            "Password change required but no TTY available.\n"
        )
        self.assertTrue(looks_like_password_expired(text))

    def test_must_change_now(self) -> None:
        self.assertTrue(
            looks_like_password_expired(
                "You must change your password now and login again!"
            )
        )

    def test_normal_id(self) -> None:
        self.assertFalse(looks_like_password_expired("0\nroot\n"))


class DialogTests(unittest.TestCase):
    def test_reuse_current_password(self) -> None:
        d = PasswdDialog(current_password="oldpass", generated_password="Gen#1Abcdefghijkl")
        send, buf = d.consume(
            "WARNING: Your password has expired.\n"
            "You must change your password now and login again!\n"
            "Current password: "
        )
        self.assertEqual(send, "oldpass")
        send, buf = d.consume("New password: ")
        self.assertEqual(send, "oldpass")
        send, buf = d.consume("Retype new password: ")
        self.assertEqual(send, "oldpass")
        send, buf = d.consume("passwd: password updated successfully\n")
        self.assertIsNone(send)
        self.assertTrue(d.success)
        self.assertEqual(d.new_password, "oldpass")

    def test_reject_reuse_then_generated(self) -> None:
        d = PasswdDialog(current_password="oldpass", generated_password="Gen#1Abcdefghijkl")
        d.consume("Current password: ")
        d.consume("New password: ")
        send, buf = d.consume(
            "BAD PASSWORD: you tried to use the same password\nNew password: "
        )
        self.assertEqual(send, "Gen#1Abcdefghijkl")
        self.assertTrue(d.switched_to_generated)
        send, _ = d.consume("Retype new password: ")
        self.assertEqual(send, "Gen#1Abcdefghijkl")
        d.consume("passwd: password updated successfully\n")
        self.assertTrue(d.success)

    def test_retype_not_confused_with_new(self) -> None:
        d = PasswdDialog(current_password="oldpass", generated_password="Gen#1Abcdefghijkl")
        d.consume("Current password: ")
        d.consume("New password: ")
        send, _ = d.consume("Retype new UNIX password: ")
        self.assertEqual(send, "oldpass")
        self.assertEqual(d.new_sends, 2)

    def test_unix_current_prompt(self) -> None:
        d = PasswdDialog(current_password="x", generated_password="Gen#1Abcdefghijkl")
        send, _ = d.consume("(current) UNIX password: ")
        self.assertEqual(send, "x")


class GenerateTests(unittest.TestCase):
    def test_classes(self) -> None:
        pw = generate_login_password()
        self.assertGreaterEqual(len(pw), 12)
        self.assertTrue(any(c.islower() for c in pw))
        self.assertTrue(any(c.isupper() for c in pw))
        self.assertTrue(any(c.isdigit() for c in pw))


if __name__ == "__main__":
    unittest.main()
