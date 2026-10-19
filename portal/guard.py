"""Need-to-know guard (second filter after the ACL) and search for the chatbot.

Scorers (SCORER in portal.env):
  tfidf      word overlap between the item and the employee's own work (Carvalho-style; offline)
  embedding  meaning similarity (sentence-transformers) between the item and the employee's own work
  deberta    the v5 guard from the notebook, with the same retrieval profile it was trained with.
             It was trained on English email, so it fits the English lab (files + mail).

Work history = items the employee authored (files they created or edited, emails they sent).

Each (employee, item) decision falls in one of three zones (idea 2 in the novelty notebook):
  allow  score > high   -> may be used by the chatbot
  ask    in between     -> needs the author's approval
  hold   score < low    -> not used
For tfidf/embedding the thresholds are calibrated without labels from the employees' own work (see
calibrate()); set THRESHOLD_LOW/THRESHOLD_HIGH to override. deberta uses 0.50 / 0.65 by default.
"""
import re

import numpy as np

import config as K

DUP_SIM = 0.97
# The v5 guard was trained with this exact profile prefix (Indonesian wording kept on purpose).
PROFILE_PREFIX = "Email lama karyawan ini yang paling mirip: "


def chunks(text, size=800, overlap=100):
    text = text or ""
    return [text[i:i + size] for i in range(0, max(len(text), 1), size - overlap)]


class _Tfidf:
    def __init__(self, corpus):
        from sklearn.feature_extraction.text import TfidfVectorizer
        self.v = TfidfVectorizer(sublinear_tf=True, ngram_range=(1, 2), lowercase=True, stop_words="english",
                                 token_pattern=r"(?u)\b[^\W\d_]{3,}\b").fit(corpus)

    def encode(self, texts):
        return self.v.transform(texts)

    @staticmethod
    def sim(a, b):
        return (a @ b.T).toarray()


class _Embed:
    def __init__(self, corpus):
        from sentence_transformers import SentenceTransformer
        self.m = SentenceTransformer(K.EMBED_MODEL)

    def encode(self, texts):
        return self.m.encode(list(texts), batch_size=32, normalize_embeddings=True)

    @staticmethod
    def sim(a, b):
        return a @ b.T


class Guard:
    def __init__(self, idx, approvals=(), legit=()):
        self.idx = idx
        self.ids = sorted(idx["items"])
        self.pos = {i: n for n, i in enumerate(self.ids)}
        docs = [self._doc(i) for i in self.ids]
        self.chunks = [(i, ch) for i in self.ids for ch in chunks(self._doc(i))]
        backend = _Tfidf if K.SCORER == "tfidf" else _Embed
        self.be = backend(docs + [ch for _, ch in self.chunks] or ["empty"])
        self.D = self.be.encode(docs) if docs else None
        self.C = self.be.encode([ch for _, ch in self.chunks]) if self.chunks else None
        self.low, self.high, self.calibration = K.LOW, K.HIGH, "fixed thresholds"
        if K.SCORER != "deberta" and not K.THRESHOLDS_SET:
            self.calibrate()
        self.approvals = set(map(tuple, approvals))
        self.legit = set(legit)
        self.model = None
        if K.SCORER == "deberta":
            if not K.GUARD_DIR or not (K.GUARD_DIR / "config.json").exists():
                raise RuntimeError("SCORER=deberta needs GUARD_DIR (the v5 guard folder, e.g. v5_seed42/guard)")
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
            self.torch = torch
            self.tok = AutoTokenizer.from_pretrained(K.GUARD_DIR)
            dev = "cuda" if torch.cuda.is_available() else "cpu"
            self.model = AutoModelForSequenceClassification.from_pretrained(K.GUARD_DIR).to(dev).eval()

    def _doc(self, iid):
        it = self.idx["items"][iid]
        return f"{it['title']}\n{it.get('text', '')}"

    def _snippet(self, iid, n=140):
        it = self.idx["items"][iid]
        body = re.sub(r"\s+", " ", re.sub(r"^Subject:.*$", "", it.get("text", ""), count=1, flags=re.M)).strip()[:n]
        return f"{it['title']} — {body}"

    # ------------------------------------------------------------------ scoring
    def history(self, user):
        return [h for h in self.idx["history"].get(user, []) if h in self.pos]

    def new_hire(self, user):
        return len(self.history(user)) < K.MIN_HISTORY

    def scores(self, user, ids):
        """Need-to-know score per item (higher = more needed). None when the user has no history."""
        hist = self.history(user)
        if not ids:
            return []
        if not hist:
            return [None] * len(ids)
        S = np.asarray(self.be.sim(self.D[[self.pos[i] for i in ids]], self.D[[self.pos[h] for h in hist]]), dtype=float)
        S[S >= DUP_SIM] = -1.0                                   # the item itself / copies do not count
        if self.model is None:
            return [float(max(row.max(), 0.0)) for row in S]
        profiles = []
        for row in S:
            top = [i for i in np.argsort(-row)[:3] if row[i] > -1]
            items = " ".join(f"[{j + 1}] (sim {row[i]:.2f}) {self._snippet(hist[i])}" for j, i in enumerate(top))
            profiles.append((PROFILE_PREFIX + items)[:600])
        texts = [self.idx["items"][i].get("text", "")[:1500] for i in ids]
        x = self.tok(profiles, texts, truncation="only_second", max_length=512, padding=True,
                     return_tensors="pt").to(self.model.device)
        with self.torch.no_grad():
            return self.model(**x).logits.float().softmax(-1)[:, 1].cpu().tolist()

    def calibrate(self, hold_q=0.10, ask_q=0.30):
        """Label-free thresholds from each employee's OWN work: score every authored item against the rest of
        that person's history (leave-one-out). Items people really work on should pass, so
        LOW = 10th percentile (at most ~10% of own-like work would be held), HIGH = 30th percentile."""
        own = []
        for u, hist in self.idx["history"].items():
            hist = [h for h in hist if h in self.pos]
            if len(hist) < 3:
                continue
            S = np.asarray(self.be.sim(self.D[[self.pos[h] for h in hist]], self.D[[self.pos[h] for h in hist]]), dtype=float)
            np.fill_diagonal(S, -1.0)
            S[S >= DUP_SIM] = -1.0
            own += [max(r.max(), 0.0) for r in S]
        if len(own) >= 20:
            self.low, self.high = float(np.quantile(own, hold_q)), float(np.quantile(own, ask_q))
            self.calibration = f"calibrated on {len(own)} own-work items: low={self.low:.3f}, high={self.high:.3f}"

    def decide(self, user, ids):
        """Three-zone decision per item: dict(id, zone, score, reason)."""
        out, todo = {}, []
        for i in ids:
            keys = set(self.idx["access"].get(user, {}).get(i, {}).get("keys", []))
            if user in self.idx["items"][i].get("authors", []):
                out[i] = {"id": i, "zone": "allow", "score": None, "reason": "you wrote this"}
            elif (user, i) in self.approvals:
                out[i] = {"id": i, "zone": "allow", "score": None, "reason": "approved by the author"}
            elif keys & self.legit:
                out[i] = {"id": i, "zone": "allow", "score": None, "reason": "permission confirmed by an admin"}
            elif self.new_hire(user):
                z = "allow" if K.NEW_HIRE_MODE == "acl" else "ask"
                out[i] = {"id": i, "zone": z, "score": None, "reason": "no work history yet (new hire or inactive account)"}
            else:
                todo.append(i)
        for i, s in zip(todo, self.scores(user, todo)):
            z = "allow" if s > self.high else "hold" if s < self.low else "ask"
            out[i] = {"id": i, "zone": z, "score": round(float(s), 4),
                      "reason": {"allow": "matches your work", "ask": "unsure: needs the author's approval",
                                 "hold": "does not match your work"}[z]}
        return [out[i] for i in ids]

    # ------------------------------------------------------------------ search (chatbot)
    def search(self, question, allowed, k=None):
        """Most relevant chunks, ONLY from items in the asker's ACL."""
        k = k or K.TOP_K
        ok = set(allowed)
        rows = [n for n, (i, _) in enumerate(self.chunks) if i in ok]
        if not rows:
            return []
        s = np.asarray(self.be.sim(self.be.encode([question]), self.C[rows]), dtype=float)[0]
        return [{"id": self.chunks[rows[n]][0], "text": self.chunks[rows[n]][1], "sim": float(s[n])} for n in np.argsort(-s)[:k]]
