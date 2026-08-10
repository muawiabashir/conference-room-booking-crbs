"""Create the first system administrator on a fresh production database.

Usage:  .venv/bin/python createadmin.py [--email a@b.org] [--name "Full Name"]

Every other account should be created through Users & Roles in the web UI,
which issues a temporary password and forces a change at first sign-in.
"""
import argparse
import sys
from getpass import getpass

from sqlalchemy import select

from app.database import Base, SessionLocal, engine
from app.models import Role, User
from app.security import hash_password, password_policy_errors


def prompt(label, value):
    while not value:
        value = input(label).strip()
    return value


def main():
    parser = argparse.ArgumentParser(description="Create a system administrator account.")
    parser.add_argument("--email")
    parser.add_argument("--name")
    args = parser.parse_args()

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        email = prompt("Admin email: ", args.email).lower()
        if db.scalar(select(User).where(User.email == email)):
            sys.exit("A user with that email already exists.")

        existing_admins = db.scalars(
            select(User).where(User.role == Role.SYSTEM_ADMIN)).all()
        if existing_admins:
            print("Warning: %d administrator(s) already exist (%s)."
                  % (len(existing_admins), ", ".join(u.email for u in existing_admins)))
            if input("Create another? [y/N] ").strip().lower() != "y":
                sys.exit("Cancelled.")

        full_name = prompt("Full name: ", args.name)

        password = getpass("Password: ")
        errors = password_policy_errors(password)
        if errors:
            sys.exit("Password " + "; ".join(errors) + ".")
        if getpass("Confirm password: ") != password:
            sys.exit("Passwords do not match.")

        db.add(User(email=email, full_name=full_name, role=Role.SYSTEM_ADMIN,
                    password_hash=hash_password(password)))
        db.commit()
        print("Created system administrator %s." % email)
    finally:
        db.close()


if __name__ == "__main__":
    main()
