"""Write seed/accounts.env with a random password for every employee (only if it does not exist yet)."""
import secrets
import string

import common as c

path = c.SEED / "accounts.env"
if path.exists():
    raise SystemExit(f"{path} already exists - not overwritten")
alphabet = string.ascii_letters + string.digits
lines = ["# Lab employee passwords (git-ignored). Same password for directory, files and mail."]
for uid in c.PEOPLE:
    lines.append(f"PW_{uid}=" + "".join(secrets.choice(alphabet) for _ in range(14)) + "-Lab")
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"{path} written ({len(c.PEOPLE)} accounts)")
