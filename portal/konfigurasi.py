"""Pengaturan portal. Nilai dibaca dari file portal.env (salin dari portal.env.example)."""
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent


def _muat_env(path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_muat_env(pathlib.Path(os.environ.get("PORTAL_ENV", ROOT / "portal.env")))


def _path(v):
    if not v:
        return None
    p = pathlib.Path(v).expanduser()
    return p if p.is_absolute() else (ROOT / p).resolve()


# --- Nextcloud
NC_URL = os.environ.get("NC_URL", "http://localhost:8080").rstrip("/")
NC_ADMIN_USER = os.environ.get("NC_ADMIN_USER", "admin")
NC_ADMIN_PASS = os.environ.get("NC_ADMIN_PASS", "")
AKUN_FILE = _path(os.environ.get("AKUN_FILE", ""))        # seed/accounts.env milik storage uji (PW_<user>=...)
MANIFEST = _path(os.environ.get("MANIFEST", ""))          # seed/manifest.csv (kunci jawaban, khusus evaluasi)

# --- Penilai (guard)
PENILAI = os.environ.get("PENILAI", "tfidf")              # tfidf | embedding | deberta
EMBED_MODEL = os.environ.get("EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
GUARD_DIR = _path(os.environ.get("GUARD_DIR", ""))        # folder guard v5 (mis. v5_seed42/guard), hanya untuk PENILAI=deberta
_AMBANG_DEFAULT = {"tfidf": (0.06, 0.12), "embedding": (0.35, 0.50), "deberta": (0.50, 0.65)}
AMBANG_BAWAH = float(os.environ.get("AMBANG_BAWAH") or _AMBANG_DEFAULT.get(PENILAI, (0.5, 0.65))[0])
AMBANG_ATAS = float(os.environ.get("AMBANG_ATAS") or _AMBANG_DEFAULT.get(PENILAI, (0.5, 0.65))[1])
GRUP_LUAS_MIN = float(os.environ.get("GRUP_LUAS_MIN", "0.8"))   # grup berisi >= 80% karyawan = grup luas (tidak diaudit)
MIN_HISTORI = int(os.environ.get("MIN_HISTORI", "1"))     # histori lebih sedikit -> dianggap karyawan baru
MODE_KARYAWAN_BARU = os.environ.get("MODE_KARYAWAN_BARU", "tanya")   # tanya | acl

# --- Chatbot
LLM = os.environ.get("LLM", "tanpa")                      # tanpa | ollama | transformers
LLM_URL = os.environ.get("LLM_URL", "http://localhost:11434/v1").rstrip("/")
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen2.5:7b")
RELEVAN_MIN = float(os.environ.get("RELEVAN_MIN") or {"tfidf": 0.05}.get(PENILAI, 0.25))   # potongan kurang relevan dibuang
TOP_K = int(os.environ.get("TOP_K", "8"))
MAX_DOCS = int(os.environ.get("MAX_DOCS", "4"))

# --- Lain-lain
KIRIM_NOTIFIKASI = os.environ.get("KIRIM_NOTIFIKASI", "0") == "1"
DATA_DIR = _path(os.environ.get("DATA_DIR", "data"))
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "7860"))
DATA_DIR.mkdir(parents=True, exist_ok=True)


def akun_uji():
    """Password akun karyawan dari accounts.env storage uji: {user: password}."""
    if not AKUN_FILE or not AKUN_FILE.exists():
        return {}
    out = {}
    for line in AKUN_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("PW_") and "=" in line:
            k, v = line.split("=", 1)
            out[k[3:]] = v
    return out
