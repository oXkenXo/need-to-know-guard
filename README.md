# Need-to-Know Guard — test environment and portal

System part of the research **Need-to-Know Guard for Enterprise RAG** (MBC Lab, Telkom University).

| Folder | Contents | Start with |
|---|---|---|
| [`lab/`](lab/) | **Meridian Labs**: a realistic enterprise test environment in Docker — directory (OpenLDAP), file server (Nextcloud), mail server (Postfix + Dovecot) with webmail. 19 employees, 77 documents, 68 emails, 12 planted access-control problems and an answer key | `lab/README.md` |
| [`portal/`](portal/) | Employee portal: assistant (RAG) with a three-zone need-to-know guard, access requests, and an admin panel that audits permissions on the file and mail servers | `portal/README.md` |
| [`storage/`](storage/) | Earlier, smaller test server (Nextcloud only, 7 employees, Indonesian documents). Kept for reference; superseded by `lab/` | `storage/README.md` |

## Order

1. `lab/`: `./setup.sh all` (needs Docker). It ends with `All checks passed.`
2. `portal/`: `python connector.py`, then `python app.py` → http://127.0.0.1:7860

## Security

- Passwords live only in git-ignored files (`lab/.env`, `lab/seed/accounts.env`, `portal/portal.env`). The repo contains only `*.example` files.
- The planted problems are **deliberately wrong** permissions. Use the lab only on your own machine.
- The experiment notebooks (EnronQA, DeBERTa guard training) are not in this repository.
