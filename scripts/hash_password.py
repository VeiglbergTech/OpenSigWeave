#!/usr/bin/env python3
"""Generate a scrypt password hash for LOCAL_ADMIN_PASSWORD_HASH."""
from getpass import getpass
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from auth_config import hash_local_admin_password

password = getpass("Local administrator password (minimum 12 characters): ")
confirmation = getpass("Confirm password: ")
if password != confirmation:
    raise SystemExit("Passwords do not match.")
print(hash_local_admin_password(password))
