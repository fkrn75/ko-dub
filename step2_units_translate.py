# 2단계: 단어+화자 → 문장 단위(unit) 구성 → EN→KO 번역 → units.json
# 실행: supdub\.venv\Scripts\python.exe step2_units_translate.py <작업폴더>
import bisect
import json
import re
import sys
from pathlib import Path

import os

work = Path(sys.argv[1])
asr = json.loads((work / "asr.json").read_text(encoding="utf-8"))
turns = json.loads((work / "diar.json").read_text(encoding="utf-8"))
turns.sort(key=lambda t: t["start"])
starts = [t["start"] for t in turns]


def speaker_at(t0, t1):
    """단어 구간과 가장 많이 겹치는 화자. 겹침 없으면 1.0초 이내 가장 가까운 턴."""
    best, best_ov = None, 0.0
    i = bisect.bisect_right(starts, t1)
    for t in turns[max(0, i - 40): i]:
        ov = min(t1, t["end"]) - max(t0, t["start"])
        if ov > best_ov:
            best, best_ov = t["speaker"], ov
    if best:
        return best
    mid = (t0 + t1) / 2
    near = min(turns, key=lambda t: 0 if t["start"] <= mid <= t["end"] else min(abs(t["start"] - mid), abs(t["end"] - mid)))
    d = 0 if near["start"] <= mid <= near["end"] else min(abs(near["start"] - mid), abs(near["end"] - mid))
    return near["speaker"] if d <= 1.0 else None


# ── 단어 평탄화 + 화자 부여 ──
words = []
for seg in asr:
    for w in seg["words"]:
        txt = w["w"].strip()
        if not txt:
            continue
        words.append({"w": txt, "s": w["s"], "e": w["e"], "spk": speaker_at(w["s"], w["e"])})
# 화자 미정 단어는 앞 단어 화자를 이어받음
last = None
for w in words:
    if w["spk"] is None:
        w["spk"] = last
    last = w["spk"] or last
for w in reversed(words):  # 맨 앞쪽 None 보정
    pass
first = next((w["spk"] for w in words if w["spk"]), "SPEAKER_00")
for w in words:
    if w["spk"] is None:
        w["spk"] = first

# ── 문장 단위 분할 ──
END_RE = re.compile(r"[.!?]['\")\]]*$")
MAX_WORDS = 38
units, cur = [], []


def flush():
    global cur
    if cur:
        units.append(
            {
                "start": cur[0]["s"],
                "end": cur[-1]["e"],
                "speaker": cur[0]["spk"],
                "en": " ".join(x["w"] for x in cur).strip(),
            }
        )
    cur = []


for i, w in enumerate(words):
    if cur:
        gap = w["s"] - cur[-1]["e"]
        if w["spk"] != cur[-1]["spk"] or gap > 1.5:
            flush()
    cur.append(w)
    nxt = words[i + 1] if i + 1 < len(words) else None
    ended = bool(END_RE.search(w["w"]))
    # 너무 긴 문장은 쉼표/긴 공백에서 끊기
    soft = len(cur) >= MAX_WORDS // 2 and (w["w"].endswith(",") or (nxt and nxt["s"] - w["e"] > 0.35))
    hard = len(cur) >= MAX_WORDS
    if ended or soft or hard:
        flush()
flush()

# 너무 짧은 조각(1~2단어)은 같은 화자의 인접 조각에 병합
merged = []
for u in units:
    nw = len(u["en"].split())
    if merged and nw <= 2 and u["speaker"] == merged[-1]["speaker"] and u["start"] - merged[-1]["end"] < 1.0 and len(merged[-1]["en"].split()) < MAX_WORDS:
        merged[-1]["en"] += " " + u["en"]
        merged[-1]["end"] = u["end"]
    else:
        merged.append(u)
units = merged
for i, u in enumerate(units):
    u["id"] = i
print(f"단위 {len(units)}개, 화자 {sorted(set(u['speaker'] for u in units))}")

# ── 번역 (줄바꿈 묶음 1회 요청, 줄수 불일치 시 낱줄 폴백) ──


def tr_retry(text):
    """429(요청 과다) 시 대기 후 재시도."""
    import time
    import urllib.parse
    import urllib.request

    def gtx(t):
        # deep-translator 가 쓰는 /m 엔드포인트는 차단됐으므로 translate_a/single(gtx)을 POST 로 직접 호출
        data = urllib.parse.urlencode({"client": "gtx", "sl": "en", "tl": "ko", "dt": "t", "q": t}).encode("utf-8")
        req = urllib.request.Request("https://translate.googleapis.com/translate_a/single", data=data, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            j = json.loads(r.read().decode("utf-8"))
        return "".join(seg[0] for seg in j[0] if seg and seg[0])

    for wait in (5, 20, 60, 120, 240, 300, 300):
        try:
            return gtx(text)
        except Exception as e:  # noqa: BLE001
            print(f"  번역 재시도 대기 {wait}s ({e.__class__.__name__}: {e})", flush=True)
            time.sleep(wait)
    return gtx(text)


def translate_batch(lines):
    res = []
    i = 0
    while i < len(lines):
        chunk, size = [], 0
        while i < len(lines) and size + len(lines[i]) + 1 <= 3800:
            chunk.append(lines[i])
            size += len(lines[i]) + 1
            i += 1
        if not chunk:  # 한 줄이 너무 긴 경우
            chunk = [lines[i]]
            i += 1
        out = tr_retry("\n".join(chunk))
        parts = out.split("\n") if out else []
        if len(parts) != len(chunk):
            print(f"  줄수 불일치({len(parts)}!={len(chunk)}) → 낱줄 폴백")
            parts = [tr_retry(c) or c for c in chunk]
        import time as _t

        _t.sleep(1.5)  # 요청 간격
        res.extend(parts)
        print(f"  번역 {len(res)}/{len(lines)}", flush=True)
    return res


def nllb_translate(lines):
    """로컬 NLLB-200 1.3B(GPU fp16)로 번역 — 구글 429 시 대체 경로. MeetScribe venv(torch+transformers)에서 실행."""
    import torch
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

    name = "facebook/nllb-200-distilled-1.3B"
    tok = AutoTokenizer.from_pretrained(name, src_lang="eng_Latn")
    model = AutoModelForSeq2SeqLM.from_pretrained(name, torch_dtype=torch.float16).to("cuda").eval()
    kor = tok.convert_tokens_to_ids("kor_Hang")
    order = sorted(range(len(lines)), key=lambda i: len(lines[i]))
    out = [""] * len(lines)
    B = 16
    for b in range(0, len(order), B):
        idx = order[b: b + B]
        batch = tok([lines[i] for i in idx], return_tensors="pt", padding=True, truncation=True, max_length=256).to("cuda")
        with torch.no_grad():
            gen = model.generate(**batch, forced_bos_token_id=kor, num_beams=4, max_new_tokens=256, repetition_penalty=1.1, no_repeat_ngram_size=4)  # 반복 루프 방지
        for i, t in zip(idx, tok.batch_decode(gen, skip_special_tokens=True)):
            out[i] = t
        if (b // B) % 20 == 0:
            print(f"  NLLB {b + len(idx)}/{len(lines)}", flush=True)
    return out


if os.environ.get("TRANSLATOR") == "nllb":
    ko = nllb_translate([u["en"] for u in units])
else:
    ko = translate_batch([u["en"] for u in units])
for u, k in zip(units, ko):
    u["ko"] = (k or "").strip()

(work / "units.json").write_text(json.dumps(units, ensure_ascii=False, indent=1), encoding="utf-8")
print("units.json 저장 완료")
