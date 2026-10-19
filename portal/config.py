"""Portal settings, read from portal.env (copy portal.env.example)."""
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent


def _load_env(path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_load_env(pathlib.Path(os.environ.get("PORTAL_ENV", ROOT / "portal.env")))


def _path(v):
    if not v:
        return None
    p = pathlib.Path(v).expanduser()
    return p if p.is_absolute() else (ROOT / p).resolve()


def _flt(name, default):
    v = os.environ.get(name)
    return float(v) if v not in (None, "") else default


# --- file server (Nextcloud)
NC_URL = os.environ.get("NC_URL", "http://localhost:8080").rstrip("/")
NC_ADMIN_USER = os.environ.get("NC_ADMIN_USER", "admin")
NC_ADMIN_PASS = os.environ.get("NC_ADMIN_PASS", "")

# --- mail server (IMAP). Leave IMAP_HOST empty to index files only.
IMAP_HOST = os.environ.get("IMAP_HOST", "")
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))
IMAP_CAFILE = _path(os.environ.get("IMAP_CAFILE", ""))       # lab: ../lab/certs/ca.crt
MAIL_DOMAIN = os.environ.get("MAIL_DOMAIN", "meridian.test")
MAIL_ALIASES = _path(os.environ.get("MAIL_ALIASES", ""))     # lab: ../lab/config/mail/postfix-virtual.cf

# --- directory (LDAP). Used only by the admin action "remove from group"; empty = use Nextcloud groups.
LDAP_URL = os.environ.get("LDAP_URL", "")                    # lab: ldap://localhost:389
LDAP_ADMIN_DN = os.environ.get("LDAP_ADMIN_DN", "")          # lab: cn=admin,dc=meridian,dc=test
LDAP_ADMIN_PASSWORD = os.environ.get("LDAP_ADMIN_PASSWORD", "")
LDAP_BASE = os.environ.get("LDAP_BASE", "dc=meridian,dc=test")

# --- lab accounts and answer key (test environments only)
ACCOUNTS_FILE = _path(os.environ.get("ACCOUNTS_FILE", ""))   # lab: ../lab/seed/accounts.env
ANSWER_KEY = _path(os.environ.get("ANSWER_KEY", ""))         # lab: ../lab/seed/answer_key

# --- guard
SCORER = os.environ.get("SCORER", "tfidf")                   # tfidf | embedding | deberta
EMBED_MODEL = os.environ.get("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
GUARD_DIR = _path(os.environ.get("GUARD_DIR", ""))           # v5 guard folder, e.g. .../v5_seed42/guard
_DEFAULT_THRESHOLDS = {"tfidf": (0.06, 0.12), "embedding": (0.35, 0.50), "deberta": (0.50, 0.65)}
THRESHOLDS_SET = bool(os.environ.get("THRESHOLD_LOW") or os.environ.get("THRESHOLD_HIGH"))
LOW = _flt("THRESHOLD_LOW", _DEFAULT_THRESHOLDS.get(SCORER, (0.5, 0.65))[0])
HIGH = _flt("THRESHOLD_HIGH", _DEFAULT_THRESHOLDS.get(SCORER, (0.5, 0.65))[1])
BROAD_GROUP_SHARE = _flt("BROAD_GROUP_SHARE", 0.8)         # groups with >= 80% of staff are not audited per member
MIN_HISTORY = int(os.environ.get("MIN_HISTORY", "1"))
NEW_HIRE_MODE = os.environ.get("NEW_HIRE_MODE", "ask")       # ask | acl
INACTIVE_DAYS = int(os.environ.get("INACTIVE_DAYS", "14"))

# --- chatbot
LLM = os.environ.get("LLM", "none")                          # none | ollama | transformers
LLM_URL = os.environ.get("LLM_URL", "http://localhost:11434/v1").rstrip("/")
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen2.5:7b")
MIN_RELEVANCE = _flt("MIN_RELEVANCE", {"tfidf": 0.05}.get(SCORER, 0.25))
TOP_K = int(os.environ.get("TOP_K", "8"))
MAX_DOCS = int(os.environ.get("MAX_DOCS", "4"))

# --- misc
NOTIFY = os.environ.get("NOTIFY", "0") == "1"
DATA_DIR = _path(os.environ.get("DATA_DIR", "data"))
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "7860"))
DATA_DIR.mkdir(parents=True, exist_ok=True)


def accounts():
    """Employee passwords from the lab's accounts.env: {uid: password}."""
    if not ACCOUNTS_FILE or not ACCOUNTS_FILE.exists():
        return {}
    out = {}
    for line in ACCOUNTS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("PW_") and "=" in line:
            k, v = line.split("=", 1)
            out[k[3:]] = v
    return out
