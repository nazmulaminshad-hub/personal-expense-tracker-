"""Single-user authentication for the Streamlit application."""

import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path
from typing import Any, Mapping, Optional, Union


MIN_PASSWORD_LENGTH = 12
SESSION_TIMEOUT_SECONDS = 30 * 60
SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 1
DK_LENGTH = 32


def hash_password(password: str) -> str:
    """Hash a password with scrypt and a cryptographically random salt."""
    if not isinstance(password, str):
        raise TypeError("password must be a string")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            "password must be at least {} characters".format(
                MIN_PASSWORD_LENGTH
            )
        )

    salt = secrets.token_bytes(16)
    derived_key = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=DK_LENGTH,
    )
    return "scrypt${}${}${}${}${}".format(
        SCRYPT_N,
        SCRYPT_R,
        SCRYPT_P,
        salt.hex(),
        derived_key.hex(),
    )


def verify_password(password: str, encoded_hash: str) -> bool:
    """Verify a password against an encoded scrypt hash."""
    if not isinstance(password, str) or not isinstance(encoded_hash, str):
        return False
    try:
        parts = encoded_hash.split("$")
        if len(parts) != 6 or parts[0] != "scrypt":
            return False
        _, n_text, r_text, p_text, salt_hex, expected_hex = parts
        n = int(n_text)
        r = int(r_text)
        p = int(p_text)
        if n < 16384 or r < 8 or p < 1:
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(expected_hex)
        if len(salt) != 16 or len(expected) != DK_LENGTH:
            return False
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError, OverflowError):
        return False


def load_password_hash(secrets_config: Optional[Mapping[str, Any]] = None) -> Optional[str]:
    """Load the password hash from environment or Streamlit secrets."""
    environment_hash = os.environ.get("EXPENSE_TRACKER_PASSWORD_HASH")
    if environment_hash:
        return environment_hash

    if secrets_config is not None:
        auth_config = secrets_config.get("auth")
        if isinstance(auth_config, Mapping):
            configured_hash = auth_config.get("password_hash")
            if isinstance(configured_hash, str) and configured_hash:
                return configured_hash
    return None


def is_session_authenticated(session_state: Mapping[str, Any]) -> bool:
    """Return True only for an unexpired authenticated session."""
    if session_state.get("authenticated") is not True:
        return False
    expires_at = session_state.get("auth_expires_at")
    if not isinstance(expires_at, (int, float)):
        return False
    return float(expires_at) > time.time()


def establish_authenticated_session(session_state: Any) -> None:
    """Mark the current session authenticated for a limited period."""
    session_state["authenticated"] = True
    session_state["auth_expires_at"] = time.time() + SESSION_TIMEOUT_SECONDS


def clear_authenticated_session(session_state: Any) -> None:
    """Clear authentication fields from the current session."""
    session_state.pop("authenticated", None)
    session_state.pop("auth_expires_at", None)


def save_password_hash(
    encoded_hash: str,
    project_root: Union[str, Path],
) -> Path:
    """Save the password hash to the gitignored Streamlit secrets file."""
    if not isinstance(encoded_hash, str):
        raise TypeError("encoded_hash must be a string")

    project_path = Path(project_root)
    secrets_directory = project_path / ".streamlit"
    secrets_directory.mkdir(parents=True, exist_ok=True)
    secrets_path = secrets_directory / "secrets.toml"

    if secrets_path.is_file():
        existing_text = secrets_path.read_text(encoding="utf-8")
        if "password_hash" in existing_text:
            raise ValueError("A password hash is already configured")
        if "[auth]" in existing_text:
            lines = existing_text.splitlines()
            auth_index = lines.index("[auth]")
            insert_at = auth_index + 1
            while insert_at < len(lines) and lines[insert_at].strip() and not lines[insert_at].lstrip().startswith("["):
                insert_at += 1
            lines.insert(insert_at, 'password_hash = "{}"'.format(encoded_hash))
            new_text = "\n".join(lines) + "\n"
        else:
            separator = "" if existing_text.endswith("\n") or not existing_text else "\n"
            new_text = existing_text + separator + (
                '[auth]\n'
                'password_hash = "{}"\n'
            ).format(encoded_hash)
    else:
        new_text = (
            '[auth]\n'
            'password_hash = "{}"\n'
        ).format(encoded_hash)

    secrets_path.write_text(new_text, encoding="utf-8")
    if os.name != "nt":
        os.chmod(secrets_path, 0o600)
    return secrets_path


def require_authentication(
    project_root: Union[str, Path],
    secrets_config: Optional[Mapping[str, Any]] = None,
) -> bool:
    """Render setup/login/logout controls and protect the application."""
    import streamlit as st

    if is_session_authenticated(st.session_state):
        with st.sidebar:
            if st.button("Logout"):
                clear_authenticated_session(st.session_state)
                st.rerun()
        return True

    configured_hash = load_password_hash(secrets_config)

    if configured_hash is None:
        st.title("Initial Account Setup")
        st.info(
            "Create a local password to protect this application. "
            "The password is stored only as a salted scrypt hash."
        )
        with st.form("initial_account_setup"):
            password = st.text_input("Create Password", type="password")
            confirmation = st.text_input("Confirm Password", type="password")
            submitted = st.form_submit_button("Create Secure Account")

        if submitted:
            if len(password) < MIN_PASSWORD_LENGTH:
                st.error(
                    "Password must be at least {} characters.".format(
                        MIN_PASSWORD_LENGTH
                    )
                )
            elif password != confirmation:
                st.error("Password and confirmation do not match.")
            else:
                try:
                    encoded_hash = hash_password(password)
                    save_password_hash(encoded_hash, project_root)
                except (OSError, TypeError, ValueError) as error:
                    st.error("Could not configure the account: {}".format(error))
                else:
                    establish_authenticated_session(st.session_state)
                    st.success("Secure account created.")
                    st.rerun()
        return False

    st.title("Personal Expense Tracker")
    with st.form("login_form"):
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login")

    if submitted:
        if verify_password(password, configured_hash):
            establish_authenticated_session(st.session_state)
            st.rerun()
        else:
            st.error("Login failed. Check the password and try again.")
    return False
