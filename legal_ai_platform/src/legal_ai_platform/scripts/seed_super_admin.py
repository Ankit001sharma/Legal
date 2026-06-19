"""Bootstrap the first super-admin user."""

from __future__ import annotations

import argparse
import uuid

from legal_ai_platform.auth.passwords import hash_password
from legal_ai_platform.auth.principal import UserRole
from legal_ai_platform.config import get_settings
from legal_ai_platform.db.models import User
from legal_ai_platform.db.session import get_db_session, init_db


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the initial super-admin user")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    args = parser.parse_args()

    settings = get_settings()
    init_db(settings.database_url)

    with get_db_session(settings.database_url) as db:
        existing = db.query(User).filter(User.role == UserRole.SUPER_ADMIN.value).first()
        if existing:
            print(f"Super admin already exists: {existing.email}")
            return
        user = User(
            id=str(uuid.uuid4()),
            email=args.email.lower(),
            password_hash=hash_password(args.password),
            role=UserRole.SUPER_ADMIN.value,
            tenant_id=None,
        )
        db.add(user)
        print(f"Created super admin: {user.email} ({user.id})")


if __name__ == "__main__":
    main()
