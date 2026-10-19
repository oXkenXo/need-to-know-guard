"""Chatbot RAG: ACL Nextcloud (lapis 1) -> guard 3 zona (lapis 2) -> LLM menjawab hanya dari dokumen yang lolos."""
import re

import requests

import audit
import konfigurasi as K

SISTEM = ("Kamu asisten dokumen kantor. Jawab dalam bahasa Indonesia, HANYA dari dokumen yang diberikan. "
          "Kalau jawabannya tidak ada di dokumen, katakan tidak tahu. Sebutkan judul dokumen yang kamu pakai.")
_hf = {}


def _llm(pertanyaan, potongan, mode=None):
    mode = mode or K.LLM
    konteks = "\n\n---\n\n".join(f"[{p['judul']}]\n{p['teks']}" for p in potongan)
    if mode == "ollama":                                   # server OpenAI-compatible (Ollama, LM Studio, vLLM)
        r = requests.post(f"{K.LLM_URL}/chat/completions", timeout=180, json={
            "model": K.LLM_MODEL, "temperature": 0,
            "messages": [{"role": "system", "content": SISTEM},
                         {"role": "user", "content": f"Dokumen:\n{konteks}\n\nPertanyaan: {pertanyaan}"}]})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    if mode == "transformers":                             # model Hugging Face lokal (4-bit kalau ada GPU)
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
        if "m" not in _hf:
            _hf["t"] = AutoTokenizer.from_pretrained(K.LLM_MODEL)
            q = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                   bnb_4bit_compute_dtype=torch.float16) if torch.cuda.is_available() else None
            _hf["m"] = AutoModelForCausalLM.from_pretrained(K.LLM_MODEL, device_map="auto", quantization_config=q)
        t, m = _hf["t"], _hf["m"]
        x = t.apply_chat_template([{"role": "system", "content": SISTEM},
                                   {"role": "user", "content": f"Dokumen:\n{konteks}\n\nPertanyaan: {pertanyaan}"}],
                                  add_generation_prompt=True, return_dict=True, return_tensors="pt").to(m.device)
        with torch.no_grad():
            out = m.generate(**x, max_new_tokens=300, do_sample=False, pad_token_id=t.pad_token_id or t.eos_token_id)
        return t.decode(out[0][x["input_ids"].shape[1]:], skip_special_tokens=True).strip()
    # tanpa LLM: tampilkan kalimat paling relevan dari dokumen yang lolos
    kata = {w for w in re.findall(r"\w{4,}", pertanyaan.lower())}
    baris = []
    for p in potongan[:2]:
        kal = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", p["teks"]) if len(s.strip()) > 20]
        kal.sort(key=lambda s: -len(kata & set(re.findall(r"\w{4,}", s.lower()))))
        baris += [f"- **{p['judul']}**: {s}" for s in kal[:2]]
    return "(Mode tanpa LLM: kutipan paling relevan)\n" + "\n".join(baris)


def jawab(idx, penilai, user, pertanyaan):
    boleh = list(idx["akses"].get(user, {}))                      # lapis 1: ACL dari Nextcloud
    hits = [h for h in penilai.cari(pertanyaan, boleh) if h["sim"] >= K.RELEVAN_MIN]
    fids = list(dict.fromkeys(h["fid"] for h in hits))
    nilai = {r["fid"]: r for r in penilai.nilai(user, fids)}       # lapis 2: guard
    audit.catat_chat(idx, user, list(nilai.values()))
    lolos_f = [f for f in fids if nilai[f]["zona"] == "lolos"][:K.MAX_DOCS]
    potongan = [{"judul": idx["files"][h["fid"]]["judul"], "teks": h["teks"]} for h in hits if h["fid"] in lolos_f]
    if not potongan:
        teks = ("Aku tidak menemukan dokumen yang boleh kupakai untuk menjawab ini."
                + (" Ada dokumen yang perlu persetujuan pemilik; kamu bisa memintanya di bawah." if any(
                    nilai[f]["zona"] == "tanya" for f in fids) else ""))
    else:
        try:
            teks = _llm(pertanyaan, potongan)
        except Exception as e:                                     # LLM mati -> tetap tampilkan kutipan
            teks = f"(LLM '{K.LLM}' tidak bisa dihubungi: {e})\n" + _llm(pertanyaan, potongan, mode="tanpa")
    info = lambda f: {"fid": f, "judul": idx["files"][f]["judul"], "path": idx["akses"][user][f]["path"],
                      "zona": nilai[f]["zona"], "skor": nilai[f]["skor"], "alasan": nilai[f]["alasan"]}
    return {"jawaban": teks, "sumber": [info(f) for f in lolos_f],
            "tanya": [info(f) for f in fids if nilai[f]["zona"] == "tanya"],
            "ditahan": [info(f) for f in fids if nilai[f]["zona"] == "tahan"]}
