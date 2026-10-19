"""RAG chatbot: ACL from the servers (layer 1) -> need-to-know guard, three zones (layer 2) -> the LLM
answers only from the items that were allowed."""
import re

import requests

import audit
import config as K

SYSTEM = ("You are a company document assistant. Answer ONLY from the documents and emails provided. "
          "If the answer is not in them, say you don't know. Mention the titles you used.")
_hf = {}


def _llm(question, passages, mode=None):
    mode = mode or K.LLM
    context = "\n\n---\n\n".join(f"[{p['title']}]\n{p['text']}" for p in passages)
    prompt = f"Documents:\n{context}\n\nQuestion: {question}"
    if mode == "ollama":                                    # any OpenAI-compatible server (Ollama, LM Studio, vLLM)
        r = requests.post(f"{K.LLM_URL}/chat/completions", timeout=180, json={
            "model": K.LLM_MODEL, "temperature": 0,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    if mode == "transformers":                              # local Hugging Face model (4-bit on a GPU)
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
        if "m" not in _hf:
            _hf["t"] = AutoTokenizer.from_pretrained(K.LLM_MODEL)
            q = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                   bnb_4bit_compute_dtype=torch.float16) if torch.cuda.is_available() else None
            _hf["m"] = AutoModelForCausalLM.from_pretrained(K.LLM_MODEL, device_map="auto", quantization_config=q)
        t, m = _hf["t"], _hf["m"]
        x = t.apply_chat_template([{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
                                  add_generation_prompt=True, return_dict=True, return_tensors="pt").to(m.device)
        with torch.no_grad():
            out = m.generate(**x, max_new_tokens=300, do_sample=False, pad_token_id=t.pad_token_id or t.eos_token_id)
        return t.decode(out[0][x["input_ids"].shape[1]:], skip_special_tokens=True).strip()
    # no LLM: quote the most relevant sentences of the allowed items
    words = set(re.findall(r"\w{4,}", question.lower()))
    lines = []
    for p in passages[:2]:
        sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", p["text"]) if len(s.strip()) > 20]
        sents.sort(key=lambda s: -len(words & set(re.findall(r"\w{4,}", s.lower()))))
        lines += [f"- **{p['title']}**: {s}" for s in sents[:2]]
    return "(No LLM configured: most relevant quotes)\n" + "\n".join(lines)


def answer(idx, guard, user, question):
    allowed_ids = list(idx["access"].get(user, {}))                       # layer 1: ACL from the servers
    hits = [h for h in guard.search(question, allowed_ids) if h["sim"] >= K.MIN_RELEVANCE]
    ids = list(dict.fromkeys(h["id"] for h in hits))
    dec = {d["id"]: d for d in guard.decide(user, ids)}                    # layer 2: guard
    audit.log_chat(idx, user, list(dec.values()))
    use = [i for i in ids if dec[i]["zone"] == "allow"][:K.MAX_DOCS]
    passages = [{"title": idx["items"][h["id"]]["title"], "text": h["text"]} for h in hits if h["id"] in use]
    if not passages:
        text = "I found nothing I'm allowed to use for this question."
        if any(dec[i]["zone"] == "ask" for i in ids):
            text += " Some items need the author's approval; you can request access below."
    else:
        try:
            text = _llm(question, passages)
        except Exception as e:                                             # LLM down -> still show quotes
            text = f"(LLM '{K.LLM}' unreachable: {e})\n" + _llm(question, passages, mode="none")

    def info(i):
        it = idx["items"][i]
        return {"id": i, "title": it["title"], "kind": it["kind"], "path": idx["access"][user][i]["path"],
                "zone": dec[i]["zone"], "score": dec[i]["score"], "reason": dec[i]["reason"]}
    return {"answer": text, "sources": [info(i) for i in use],
            "ask": [info(i) for i in ids if dec[i]["zone"] == "ask"],
            "held": [info(i) for i in ids if dec[i]["zone"] == "hold"]}
