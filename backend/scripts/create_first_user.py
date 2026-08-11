"""
One-time bootstrap for a brand-new deployment.

Migrations seed roles and permissions, but deliberately seed zero
users -- nobody should ship a codebase with a hardcoded default
admin/password in it. That leaves a real chicken-and-egg problem
though: POST /api/v1/users is gated by `users.manage`, which requires
being logged in as someone who already has it. This script is the way
out of that loop, and only that: it refuses to run if ANY user already
exists, so it cannot be used to mint a backdoor admin account later in
a live system. Ongoing user management goes through POST /api/v1/users
as ChemistOwner/Administrator, not this script.

Usage:
    python -m scripts.create_first_user --full-name "Lucy Kangai" \
        --username lucy --role ChemistOwner
    (prompts for a password interactively; never pass it on the CLI,
    it would land in shell history)
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import func, select

from app.core.database import AsyncSessionLocal
from app.core.security import hash_password
from app.models.role import Role
from app.models.user import User

VALID_ROLES = ("Employee", "Administrator", "ChemistOwner")


async def create_first_user(
    full_name: str,
    username: str,
    role_name: str,
    password: str,
    security_question: str,
    security_answer: str,
) -> None:
    async with AsyncSessionLocal() as db:
        existing_count = await db.scalar(select(func.count()).select_from(User))
        if existing_count and existing_count > 0:
            print(
                f"Refusing to run: {existing_count} user(s) already exist. "
                "Create additional users via POST /api/v1/users as an "
                "already-logged-in ChemistOwner/Administrator instead.",
                file=sys.stderr,
            )
            raise SystemExit(1)

        role_result = await db.execute(select(Role).where(Role.name == role_name))
        role = role_result.scalar_one_or_none()
        if role is None:
            print(
                f"Role '{role_name}' not found. Have migrations been run "
                f"(`alembic upgrade head`)? Valid roles: {', '.join(VALID_ROLES)}",
                file=sys.stderr,
            )
            raise SystemExit(1)

        user = User(
            full_name=full_name,
            username=username,
            hashed_password=hash_password(password),
            role_id=role.id,
            security_question=security_question,
            # Stripped for the same reason UserCreate.security_answer
            # is (see app/schemas/_text.py's NonBlankName) -- the
            # normal user-creation path and this bootstrap path must
            # hash the same normalized value, or reset_password_via_
            # security_question's own .strip() on the recovery input
            # would only match one of the two.
            security_answer_hash=hash_password(security_answer.strip()),
        )
        db.add(user)
        await db.commit()
        print(f"Created '{username}' ({full_name}) as {role_name}. You can now log in.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full-name", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--role", required=True, choices=VALID_ROLES)
    args = parser.parse_args()

    password = getpass.getpass("Password (min 8 characters): ")
    if len(password) < 8:
        print("Password must be at least 8 characters.", file=sys.stderr)
        raise SystemExit(1)
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords did not match.", file=sys.stderr)
        raise SystemExit(1)

    print()
    print("A security question is required -- it's the only way to recover")
    print("this specific account's password later without another admin's help.")
    security_question = input(
        "Security question (e.g. 'What was your first pet's name?'): "
    ).strip()
    if not security_question:
        print("A security question is required.", file=sys.stderr)
        raise SystemExit(1)
    security_answer = getpass.getpass("Answer: ")
    if not security_answer:
        print("An answer is required.", file=sys.stderr)
        raise SystemExit(1)

    asyncio.run(
        create_first_user(
            args.full_name, args.username, args.role, password, security_question, security_answer
        )
    )


if __name__ == "__main__":
    main()
