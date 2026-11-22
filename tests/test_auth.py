import os
import tempfile
import unittest
from pathlib import Path

from expense_tracker.auth import (
    clear_authenticated_session,
    establish_authenticated_session,
    hash_password,
    is_session_authenticated,
    load_password_hash,
    save_password_hash,
    verify_password,
)


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop("EXPENSE_TRACKER_PASSWORD_HASH", None)

    def tearDown(self):
        os.environ.pop("EXPENSE_TRACKER_PASSWORD_HASH", None)

    def test_password_hash_round_trip(self):
        encoded = hash_password("A-very-secure-test-password")

        self.assertTrue(verify_password("A-very-secure-test-password", encoded))
        self.assertFalse(verify_password("Wrong-password", encoded))
        self.assertNotIn("A-very-secure-test-password", encoded)

    def test_short_password_is_rejected(self):
        with self.assertRaises(ValueError):
            hash_password("short")

    def test_missing_password_hash_is_reported(self):
        self.assertIsNone(load_password_hash({}))
        self.assertIsNone(load_password_hash({"auth": {}}))

    def test_environment_hash_is_loaded(self):
        os.environ["EXPENSE_TRACKER_PASSWORD_HASH"] = "scrypt$test"
        self.assertEqual(
            load_password_hash({"auth": {}}),
            "scrypt$test",
        )

    def test_invalid_encoded_hash_is_rejected(self):
        self.assertFalse(verify_password("A-very-secure-password", "invalid"))
        self.assertFalse(
            verify_password(
                "A-very-secure-password",
                "scrypt$2$8$1$" + ("00" * 16) + "$" + ("00" * 32),
            )
        )

    def test_authenticated_session_expires(self):
        session = {}
        self.assertFalse(is_session_authenticated(session))

        establish_authenticated_session(session)
        self.assertTrue(is_session_authenticated(session))

        session["auth_expires_at"] = 0
        self.assertFalse(is_session_authenticated(session))

    def test_logout_clears_session(self):
        session = {}
        establish_authenticated_session(session)
        clear_authenticated_session(session)

        self.assertFalse(is_session_authenticated(session))
        self.assertNotIn("authenticated", session)
        self.assertNotIn("auth_expires_at", session)

    def test_save_password_hash_creates_gitignored_secrets_file(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_root = Path(temporary_directory)
            secrets_path = save_password_hash(
                "scrypt$32768$8$1$00$00",
                project_root,
            )

            self.assertTrue(secrets_path.is_file())
            content = secrets_path.read_text(encoding="utf-8")
            self.assertIn("[auth]", content)
            self.assertIn("password_hash", content)

    def test_save_password_hash_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_root = Path(temporary_directory)
            save_password_hash("scrypt$32768$8$1$00$00", project_root)
            with self.assertRaises(ValueError):
                save_password_hash("scrypt$32768$8$1$11$11", project_root)


if __name__ == "__main__":
    unittest.main()
