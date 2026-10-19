# Meridian Labs — enterprise test environment

A small but realistic company IT environment for testing the **Need-to-Know Guard**. It runs entirely in Docker on one laptop (≈ 2 GB RAM) and contains:

| Service | What it is | Address |
|---|---|---|
| **Directory** (OpenLDAP) | one source of truth for accounts and groups, like Active Directory | `ldap://localhost:389` |
| Directory admin (phpLDAPadmin) | web UI for the directory | http://localhost:8082 |
| **File server** (Nextcloud 34 + PostgreSQL) | team folders per department, personal folders, shares, public links; users come from the directory | http://localhost:8080 |
| **Mail server** (docker-mailserver: Postfix + Dovecot) | mailboxes, mailing lists, forwarding rules; TLS with the lab's own CA | SMTP 587, IMAPS 993 |
| **Webmail** (Roundcube) | read the employees' mail in a browser | http://localhost:8081 |

All ports listen on `127.0.0.1` only. Everything in the lab — company, people, numbers, documents, emails — is fictional.

```
                         ┌──────────────── Directory (LDAP) ────────────────┐
                         │ 19 people · 8 groups (departments, managers, all) │
                         └───────┬─────────────────────────────┬───────────┘
                     users+groups│                             │ same accounts (synced from company.yaml)
             ┌───────────────────▼──────┐          ┌───────────▼──────────────┐
             │ File server (Nextcloud)  │          │ Mail server (Postfix +   │
             │ 7 team folders, homes,   │          │ Dovecot) · 10 lists ·    │
             │ 10 shares, 2 public links│          │ 1 forwarding rule        │
             └───────────────────┬──────┘          └───────────┬──────────────┘
                       WebDAV/OCS│                             │IMAPS
                         ┌───────▼─────────────────────────────▼───────┐
                         │ Portal (../portal): assistant + guard +      │
                         │ admin auditor                                │
                         └──────────────────────────────────────────────┘
```

## The company

**Meridian Labs** (`meridian.test`): a regional logistics company with 18 office staff in 6 departments, plus one former employee whose account was never disabled.

| Department | People |
|---|---|
| Executive | Daniel Reyes (CEO), Priya Nair (COO) |
| Human Resources | Sarah Kim, Tom Becker, Lina Ortiz |
| Finance | Mark Chen (CFO), Grace Li, Omar Haddad, Alex Turner *(transferred from Marketing on 1 Oct)* |
| Marketing | Julia Rossi, Grace Lin, Ben Carter, Aisha Bello *(new hire, 13 Oct)* |
| IT | Kevin Park, Nora Weiss |
| Operations | Carlos Mendes, Emma Novak, Ravi Shah, *Ethan Brooks (resigned 30 Sep, account still active)* |

Content: **77 documents** (Word, Excel, Markdown) and **68 emails** in threads (replies, CC, forwards), delivered as 271 mailbox copies. The story covers a confidential acquisition ("Project Falcon"), a 2027 restructuring, budget cuts, payroll, performance reviews, a product launch event, a mail migration and more.

## Planted access-control problems

| Case | Source | What happened | Fix |
|---|---|---|---|
| C01 | files | HR shared the *Compensation 2026* folder (salary bands, salary review, severance) with **all-staff** | remove the share |
| C02 | files | a performance review has a **public link** without expiry | delete the link |
| C03 | files | the restructuring **draft** was uploaded to *Announcements* (readable by everyone) | move it to *Executive* |
| C04 | directory + mail | Alex moved to Finance but is still in the **marketing group and marketing@ list** | remove the membership |
| C05 | files | the *Q4 Campaign* folder is still **shared with Emma** after the launch event; it now holds 2027 plans and agency rates | remove the share |
| C06 | directory | Aisha is a **new hire** with no work history (legitimate; tests cold start) | none |
| C07 | directory | Ethan **resigned** but his account is active and still receives ops@ and all-staff mail | disable the account (HR/IAM) |
| C08 | files | *Project Falcon* was shared with the **whole Finance group**; only the CFO is on the deal team | share with the CFO only |
| C09 | mail | payroll adjustments sent to **Grace Lin** (Marketing) instead of **Grace Li** (Finance) — autocomplete | purge from her mailbox |
| C10 | mail | the restructuring draft was sent to **all-staff@** instead of the executive team | purge from mailboxes |
| C11 | mail | a **forwarding rule** from the mail migration still copies all CEO mail to the sysadmin | remove the rule |
| C12 | mail | the CFO's budget email with salary figures was **forwarded** to a team member | purge from his mailbox |

There are also **legitimate** shares, links and lists (expense policy to all staff, press-kit link with expiry, onboarding folder for the new hire, a CFO share for the deal team, role mailboxes ap@ and support@) so that an auditor cannot simply flag everything.

**Answer key** — who really needs to know each item: `seed/answer_key/files.csv` and `seed/answer_key/mail.csv` (generated from `company.yaml` and `seed/content/`).

## Run it

Requirements: Docker with the compose plugin, Python 3.9+, `openssl`, `bash`. About 2 GB RAM and 3 GB disk.

```bash
cd lab
pip install -r requirements.txt
cp .env.example .env                 # then change the four passwords
python3 scripts/make_passwords.py    # random passwords for the 19 employees -> seed/accounts.env
./setup.sh all                       # 10-15 minutes the first time (image downloads)
```

`./setup.sh all` runs these steps (each can be run on its own, e.g. `./setup.sh emails`):

| Step | What it does |
|---|---|
| `check` | Docker, Python packages, passwords changed, ports 389/587/993/8080-8082 free |
| `certs` | creates the lab certificate authority and the mail server certificate (`certs/`) |
| `config` | generates the directory (`build/directory.ldif`), mailboxes and the alias file (`config/mail/postfix-virtual.cf`) |
| `up` | starts the containers and waits until Nextcloud, LDAP and mail are ready |
| `ldap` | imports people and groups into the directory |
| `nextcloud` | connects Nextcloud to the directory, installs Team folders, creates the 7 team folders |
| `files` | uploads the 77 documents **as their authors** and creates the 10 shares |
| `emails` | sends the 68 emails through SMTP, **each logged in as its sender**; a copy goes to the sender's *Sent* folder |
| `verify` | compares every file listing and mailbox with the answer key |

The last step should end with `All checks passed.` Then sign in to Nextcloud or webmail with any employee (username e.g. `skim`, password from `seed/accounts.env`).

| To | Command |
|---|---|
| Stop / start again | `docker compose stop` / `docker compose start` |
| Start over from scratch | `./setup.sh reset` then `./setup.sh all` |
| Look at the directory | http://localhost:8082 · login DN `cn=admin,dc=meridian,dc=test` |
| Read someone's mail | http://localhost:8081 |

## Connect the portal

See `../portal/README.md`. In short: copy `portal.env.example` to `portal.env`, set the two admin passwords from `lab/.env`, then `python connector.py` and `python app.py`.

## How realistic is it?

Realistic: one directory feeding the file server; mailboxes, lists and forwarding rules on a real Postfix/Dovecot server; documents uploaded and emails sent by the employees themselves (so Nextcloud's activity log and mail headers are genuine); historical dates; people who joined, moved and left; mistakes that happen in real companies.

Simplified (on purpose): the mail server gets its accounts and lists from `company.yaml` instead of reading the directory live; no single sign-on; no external internet mail; one server per service.

## Files

```
lab/
├── company.yaml            people, groups, team folders, lists, forwards, shares, cases
├── docker-compose.yml      the six containers
├── setup.sh                step-by-step setup
├── seed/
│   ├── content/            source of every document and email (YAML)
│   ├── files/              generated documents (.docx / .xlsx / .md), committed
│   ├── answer_key/         who needs to know each item
│   └── accounts.env.example
├── config/                 mail aliases (generated) and webmail settings
├── scripts/                generators, seeding and verification
└── tests/fake_nextcloud.py fake Nextcloud for testing without Docker
```

Maintainers: after editing `company.yaml` or `seed/content/*.yaml`, run `python3 scripts/build_content.py` to regenerate `seed/files/` and the answer key.

## What was tested

Without Docker (the build machine has none), the scripts were tested against **real** OpenLDAP, Postfix and Dovecot servers configured like the containers, and a fake Nextcloud:

- the LDIF imports cleanly; users can bind with their passwords; the Nextcloud login filter finds them;
- all 68 emails are accepted over SMTP submission (TLS verified against the lab CA, sender login enforced), and every mailbox matches the answer key exactly, including the copies made by the forwarding rule;
- the file seeding is idempotent (a second run creates no duplicate shares).

The Docker-specific parts (container images, `occ` LDAP and Team-folder commands, docker-mailserver start-up) could not be run here. If a step fails, the error message names the command to check.
