"""Penilai need-to-know (guard) + pencarian dokumen untuk chatbot.

Tiga pilihan (PENILAI di portal.env):
  tfidf      kemiripan kata dengan histori kerja karyawan (ala Carvalho; offline, cocok untuk dokumen Indonesia)
  embedding  kemiripan makna (sentence-transformers multibahasa) dengan histori kerja
  deberta    guard v5 (DeBERTa) dengan profil retrieval yang sama seperti di notebook.
             Guard v5 dilatih dengan email berbahasa INGGRIS; untuk dokumen Indonesia hasilnya tidak bisa dipercaya.

Keputusan per (karyawan, dokumen) dibagi 3 zona (Ide 2 di notebook kebaruan):
  lolos  skor > AMBANG_ATAS      -> boleh masuk ke chatbot
  tanya  di antara               -> perlu persetujuan penulis/pemilik dokumen
  tahan  skor < AMBANG_BAWAH     -> tidak dipakai
"""
import re

import numpy as np

import konfigurasi as K

DUP_SIM = 0.97


def potong(teks, size=800, overlap=100):
    teks = teks or ""
    return [teks[i:i + size] for i in range(0, max(len(teks), 1), size - overlap)]


class _Tfidf:
    def __init__(self, corpus):
        from sklearn.feature_extraction.text import TfidfVectorizer
        self.v = TfidfVectorizer(sublinear_tf=True, ngram_range=(1, 2), lowercase=True,
                                 token_pattern=r"(?u)\b[^\W\d_]{3,}\b").fit(corpus)

    def encode(self, texts):
        return self.v.transform(texts)

    @staticmethod
    def sim(A, B):
        return (A @ B.T).toarray()


class _Embed:
    def __init__(self, corpus):
        from sentence_transformers import SentenceTransformer
        self.m = SentenceTransformer(K.EMBED_MODEL)

    def encode(self, texts):
        return self.m.encode(list(texts), batch_size=32, normalize_embeddings=True)

    @staticmethod
    def sim(A, B):
        return A @ B.T


class Penilai:
    def __init__(self, idx, persetujuan=(), sah=()):
        self.idx = idx
        self.fids = sorted(idx["files"])
        self.pos = {f: i for i, f in enumerate(self.fids)}
        docs = [self._doc(f) for f in self.fids]
        self.chunks = [(f, c) for f in self.fids for c in potong(self._doc(f))]
        backend = "tfidf" if K.PENILAI == "tfidf" else "embedding"
        self.be = (_Tfidf if backend == "tfidf" else _Embed)(docs + [c for _, c in self.chunks] or ["kosong"])
        self.D = self.be.encode(docs) if docs else None
        self.C = self.be.encode([c for _, c in self.chunks]) if self.chunks else None
        self.persetujuan = set(map(tuple, persetujuan))
        self.sah = set(sah)                                      # izin yang ditandai sah oleh admin
        self.guard = None
        if K.PENILAI == "deberta":
            if not K.GUARD_DIR or not (K.GUARD_DIR / "config.json").exists():
                raise RuntimeError("PENILAI=deberta butuh GUARD_DIR (folder guard v5, mis. v5_seed42/guard)")
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
            self.torch = torch
            self.tok = AutoTokenizer.from_pretrained(K.GUARD_DIR)
            dev = "cuda" if torch.cuda.is_available() else "cpu"
            self.guard = AutoModelForSequenceClassification.from_pretrained(K.GUARD_DIR).to(dev).eval()

    def _doc(self, fid):
        f = self.idx["files"][fid]
        return f"{f['judul']}\n{f.get('teks', '')}"

    def _snip(self, fid, n=140):
        f = self.idx["files"][fid]
        isi = re.sub(r"\s+", " ", f.get("teks", ""))[:n]
        return f"{f['judul']} — {isi}"

    # ------------------------------------------------------------------ skor
    def histori(self, user):
        return [h for h in self.idx["histori"].get(user, []) if h in self.pos]

    def karyawan_baru(self, user):
        return len(self.histori(user)) < K.MIN_HISTORI

    def skor(self, user, fids):
        """Skor 'perlu tahu' per dokumen (makin tinggi makin boleh). None kalau histori kosong."""
        H = self.histori(user)
        if not fids:
            return []
        if not H:
            return [None] * len(fids)
        S = self.be.sim(self.D[[self.pos[f] for f in fids]], self.D[[self.pos[h] for h in H]])
        S = np.asarray(S, dtype=float)
        S[S >= DUP_SIM] = -1.0                                     # dokumen itu sendiri / salinannya tidak dihitung
        if self.guard is None:
            return [float(max(row.max(), 0.0)) for row in S]
        profs = []
        for row in S:                                               # format profil sama dengan notebook (mode no_contacts)
            top = [i for i in np.argsort(-row)[:3] if row[i] > -1]
            items = " ".join(f"[{j + 1}] (sim {row[i]:.2f}) {self._snip(H[i])}" for j, i in enumerate(top))
            profs.append(("Email lama karyawan ini yang paling mirip: " + items)[:600])
        texts = [self.idx["files"][f].get("teks", "")[:1500] for f in fids]
        x = self.tok(profs, texts, truncation="only_second", max_length=512, padding=True,
                     return_tensors="pt").to(self.guard.device)
        with self.torch.no_grad():
            return self.guard(**x).logits.float().softmax(-1)[:, 1].cpu().tolist()

    def nilai(self, user, fids):
        """Keputusan 3 zona untuk tiap dokumen: dict(fid, zona, skor, alasan)."""
        out, perlu = {}, []
        for f in fids:
            if user in self.idx["files"][f]["penulis"]:
                out[f] = {"fid": f, "zona": "lolos", "skor": None, "alasan": "kamu penulis dokumen ini"}
            elif (user, f) in self.persetujuan:
                out[f] = {"fid": f, "zona": "lolos", "skor": None, "alasan": "disetujui pemilik"}
            elif self.sah & set(self.idx["akses"].get(user, {}).get(f, {}).get("kunci", [])):
                out[f] = {"fid": f, "zona": "lolos", "skor": None, "alasan": "izin ditandai sah oleh admin"}
            elif self.karyawan_baru(user):
                z = "lolos" if K.MODE_KARYAWAN_BARU == "acl" else "tanya"
                out[f] = {"fid": f, "zona": z, "skor": None,
                          "alasan": "histori kerja belum ada (karyawan baru / akun tanpa aktivitas)"}
            else:
                perlu.append(f)
        for f, s in zip(perlu, self.skor(user, perlu)):
            z = "lolos" if s > K.AMBANG_ATAS else "tahan" if s < K.AMBANG_BAWAH else "tanya"
            out[f] = {"fid": f, "zona": z, "skor": round(float(s), 4),
                      "alasan": {"lolos": "nyambung dengan kerjaanmu", "tanya": "ragu-ragu: perlu persetujuan pemilik",
                                 "tahan": "tidak nyambung dengan kerjaanmu"}[z]}
        return [out[f] for f in fids]

    # ------------------------------------------------------------------ pencarian (chatbot)
    def cari(self, pertanyaan, fids_boleh, k=None):
        """Potongan dokumen paling relevan, HANYA dari file yang ada di ACL penanya."""
        k = k or K.TOP_K
        boleh = set(fids_boleh)
        idx = [i for i, (f, _) in enumerate(self.chunks) if f in boleh]
        if not idx:
            return []
        q = self.be.encode([pertanyaan])
        s = np.asarray(self.be.sim(q, self.C[idx]), dtype=float)[0]
        order = np.argsort(-s)[:k]
        return [{"fid": self.chunks[idx[i]][0], "teks": self.chunks[idx[i]][1], "sim": float(s[i])} for i in order]
