# Portal — document assistant with the Need-to-Know Guard

A web portal for employees and administrators, connected to the file server (Nextcloud) and the mail server (IMAP) of the [Meridian Labs test environment](../lab/README.md).

| Tab | Who | What |
|---|---|---|
| **My items** | employee | every file and email you can open on the servers, *why* you can open it (team folder, share, mailing list, forwarding rule…), and whether the assistant may use it for you |
| **Assistant** | employee | a chatbot that answers from your files and email, with two layers of protection (below) |
| **Access requests** | employee | items the guard was unsure about can be requested from their author; authors approve or reject |
| **Admin** | administrator | proactive audit of every permission, Revoke / Keep buttons, rule-based risks, evaluation against the lab's answer key |

```
question ──▶ ① ACL from the servers: only items the user can actually open (WebDAV listing, own mailbox)
             ② Need-to-Know Guard, three zones per item:
                   allow ─▶ given to the LLM
                   ask   ─▶ not used; the user can request it from the author
                   hold  ─▶ not used
             ③ LLM answers only from the allowed items
                   │
                   └── every "ACL allows, guard holds" is logged with the permission that caused it ──▶ Admin
```

## How the AI is connected to the administrators

1. **Every access has a cause.** The connector records why each person can open each item: a share, a public link, group membership, the file's location in an open team folder, an email sent to them, a mailing list, or a forwarding rule.
2. **Proactive audit** (*Run the audit*). The guard scores *every* access each permission opens and ranks the permissions. `suspicion` is the lower bound of the 95% interval of the share of held accesses, so permissions with little evidence do not jump to the top. Each permission is either **narrow** (reaches few people) or **broad** (reaches ≥ 80% of staff: all-staff shares, announcement folders, all-staff emails, public links).
3. **The administrator decides.** The AI never changes anything by itself.

   | Permission | *Revoke / fix* does |
   |---|---|
   | share, public link | deletes it through the Nextcloud sharing API, as the share owner |
   | group membership | removes the member from the **LDAP** group |
   | file in an open folder | moves the file (type the target folder, e.g. `/Executive`) |
   | email | purges it from the flagged mailboxes over IMAP (or type usernames, e.g. `glin`) |
   | mailing list, forwarding rule | edits the mail server's alias file; docker-mailserver reloads it automatically |

   *Keep* marks a permission as intended. It stays as it is, and the guard no longer holds access through it (feedback from a human).
4. **Rule-based risks** need no AI: public links, accounts without any work history (new hire *or* a former employee), and accounts with no activity for `INACTIVE_DAYS`.
5. **Passive audit**: the same signal collected from real assistant questions.

## Run

The lab must be running (`../lab/setup.sh all`).

```bash
cd portal
pip install -r requirements.txt
cp portal.env.example portal.env     # set NC_ADMIN_PASS and LDAP_ADMIN_PASSWORD from lab/.env
python connector.py                  # reads files + mail -> data/index.json (1-2 minutes)
python app.py                        # http://127.0.0.1:7860
```

Sign in as an employee (e.g. `glin` with the password from `lab/seed/accounts.env`) or as `admin`. In the Admin tab press **1 · Sync**, then **2 · Run the audit**. Run the evaluation from the command line with `python evaluation.py`.

## Guard options (`SCORER`)

| Option | How it works | Notes |
|---|---|---|
| `tfidf` (default) | word overlap between the item and the person's own work (Carvalho-style) | offline; thresholds calibrated without labels from each person's own work |
| `embedding` | meaning similarity (sentence-transformers) | downloads a small model once |
| `deberta` | the **v5 guard** from the notebook (`GUARD_DIR` = `.../v5_seed42/guard`) | trained on English email, so it suits this English lab; a GPU helps but is not required for 19 users |

Work history = files a person created or edited (Nextcloud activity log) + emails they sent.

## Assistant LLM (`LLM`)

`none` shows the most relevant quotes. For real answers on a laptop (e.g. RTX 4060 8 GB), install [Ollama](https://ollama.com), run `ollama pull qwen2.5:7b`, and set `LLM=ollama`. The LLM only ever receives allowed items: leakage is prevented before the LLM.

## Results on the lab (TF-IDF scorer, before any fix)

| | Result |
|---|---|
| Guard AUC on 532 scored accesses | 0.61 |
| Narrow permissions: top 2 | C11 (CEO mail forwarded to the sysadmin) and C08 (Falcon shared with all of Finance) — both planted cases |
| Broad permissions | announcements and policies outrank C01/C03/C10: real announcements match nobody's work, so the need-to-know signal cannot separate them from leaks |
| Rule-based | C06 (new hire) and C07 (former employee) flagged; one false alarm (an employee with no activity for 24 days) |
| Not found | C09 and C12: the recipients *replied* in the thread, so the email now looks like their own work |

These numbers are low because each lab employee has only 5–15 items of history, which is the cold-start situation from the notebook (AUC ≈ 0.65 with 5–20 history items). Try `SCORER=deberta` with the v5 guard. The main paper numbers come from the notebooks, not from this lab.

All admin actions were tested end-to-end (`tests/test_flow.py`): deleting a share and a link, moving a file, purging emails, editing a mailing list and a forwarding rule, and removing an LDAP group member. Each change is visible after the next sync.

## Files

| File | Role |
|---|---|
| `connector.py` | reads Nextcloud (WebDAV/OCS) and IMAP as each employee → items, ACL, access causes, history |
| `guard.py` | scorers, three-zone decision, label-free threshold calibration, search |
| `chatbot.py` | retrieval → guard → LLM |
| `audit.py` | logging, proactive audit, recommendations, Revoke/Keep, access requests |
| `evaluation.py` | AUC and case ranks against `lab/seed/answer_key` |
| `nextcloud.py`, `mailserver.py` | small clients for Nextcloud and IMAP |
| `app.py` | the web interface (Gradio) |
| `tests/test_flow.py` | end-to-end test of every flow and admin action |

## Security notes

- `portal.env` and `lab/seed/accounts.env` contain passwords and are git-ignored.
- The portal listens on `127.0.0.1` only.
- Reading every mailbox with employee passwords (`ACCOUNTS_FILE`) is only acceptable in a test lab. A real deployment would use per-user OAuth / app passwords, or a mail-archive service account.
- Admin functions check the session on the server side, not just by hiding buttons.
