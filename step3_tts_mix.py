# 3단계: 한국어 번역 → 발음변환(용어집+MarkdownRadio toSpoken) → Supertonic v3 합성(화자별 음성, 슬롯 맞춤 speed) → 타임라인 조립
# 실행: supdub\.venv\Scripts\python.exe step3_tts_mix.py <작업폴더>
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

HERE = Path(__file__).parent
work = Path(sys.argv[1])
cache = work / "tts_cache"
cache.mkdir(exist_ok=True)

units = json.loads((work / "units.json").read_text(encoding="utf-8"))
stats = json.loads((work / "speaker_stats.json").read_text(encoding="utf-8"))
glossary = json.loads((HERE / "glossary.json").read_text(encoding="utf-8"))

SR = 44100
BASE_SPEED = 1.05  # Supertonic 기본값
MAX_SPEED = 1.45
STEPS = 8
FEMALE_F0 = float(os.environ.get("FEMALE_F0", "150"))  # F0 중앙값이 이보다 높으면 여성(영상 3편 실측: 남성 100~145, 여성 155~215)

# ── 화자 → 성별 → 음성 ──
spk_order = sorted(stats, key=lambda s: -stats[s]["seconds"])
voices, mi, fi = {}, 0, 0
M_ORDER = ["M1", "M2", "M3", "M4", "M5"]
F_ORDER = ["F1", "F2", "F3", "F4", "F5"]
for s in spk_order:
    if stats[s]["f0_median"] >= FEMALE_F0:
        voices[s] = F_ORDER[fi % 5]
        fi += 1
    else:
        voices[s] = M_ORDER[mi % 5]
        mi += 1
for u in units:  # 통계에 없는(짧게 말한) 화자는 가장 비슷한 기본 음성
    voices.setdefault(u["speaker"], "M1")
print("화자→음성:", voices, flush=True)
(work / "voices.json").write_text(json.dumps(voices, ensure_ascii=False, indent=1), encoding="utf-8")

# ── 발음 텍스트 만들기 ──
gl_items = sorted(glossary.items(), key=lambda kv: -len(kv[0]))


def apply_glossary(t):
    # 번역기가 하이픈 주위에 넣은 공백 정리: "real -to -sim" → "real-to-sim"
    t = re.sub(r"(?<=[A-Za-z0-9])\s*-\s*(?=[A-Za-z])", "-", t)
    for k, v in gl_items:
        t = re.sub(r"(?<![A-Za-z])" + re.escape(k) + r"(?![A-Za-z])", v, t, flags=re.IGNORECASE)
    return t


fixes_path = HERE / "ko_fixes.json"
ko_fixes = json.loads(fixes_path.read_text(encoding="utf-8")) if fixes_path.exists() and os.environ.get("KO_FIXES", "1") == "1" else {}


def apply_fixes(t):
    """번역기(NLLB) 단골 오역 교정: 단순 부분 문자열 치환(삽입 순서대로)."""
    for k, v in ko_fixes.items():
        t = t.replace(k, v)
    return t


pre = [apply_glossary(apply_fixes(u["ko"])) for u in units]
# 용어집 변환이 끝난 문자열 중 toSpoken 이 약자를 철자 읽기로 바꾸지 않도록, 용어집이 이미 한글화한다.
proc = subprocess.run(["node", str(HERE / "spoken.mjs")], input=json.dumps(pre, ensure_ascii=False).encode("utf-8"), capture_output=True, check=True)
spoken = json.loads(proc.stdout.decode("utf-8"))


def clean(t):
    t = re.sub(r"[\"“”‘’'`*_#<>\[\]{}|\\]", "", t)  # 모델 미지원 문자 제거
    t = re.sub(r"\s+", " ", t).strip()
    return t


for u, s in zip(units, spoken):
    u["spoken"] = clean(s)
(work / "spoken.json").write_text(json.dumps([{"id": u["id"], "en": u["en"], "ko": u["ko"], "spoken": u["spoken"]} for u in units], ensure_ascii=False, indent=1), encoding="utf-8")

import os

if os.environ.get("DRY"):  # 발음 텍스트만 만들고 종료(점검용)
    left = {}
    for u in units:
        for m in re.findall(r"[A-Za-z][A-Za-z0-9.\-]*", u["spoken"]):
            left[m] = left.get(m, 0) + 1
    print("남은 영문 토큰:", sorted(left.items(), key=lambda kv: -kv[1]))
    sys.exit(0)

# ── TTS ──
from supertonic import TTS

tts = TTS(model="supertonic-3", auto_download=True)
styles = {v: tts.get_voice_style(v) for v in set(voices.values())}


def synth(text, voice, speed):
    key = hashlib.md5(f"{text}|{voice}|{speed:.3f}|{STEPS}".encode("utf-8")).hexdigest()
    f = cache / f"{key}.npy"
    if f.exists():
        return np.load(f)
    if not text.strip(" .,?!"):
        return np.zeros(int(0.1 * SR), dtype=np.float32)
    try:
        wav, _ = tts.synthesize(text, voice_style=styles[voice], lang="ko", speed=speed, total_steps=STEPS)
    except Exception as e:  # 미지원 문자 등: 한글/숫자/기본 구두점만 남겨 재시도
        t2 = re.sub(r"[^0-9A-Za-z가-힣 .,?!~%-]", " ", text)
        print(f"  합성 재시도({e.__class__.__name__}): {text[:40]}", flush=True)
        wav, _ = tts.synthesize(t2, voice_style=styles[voice], lang="ko", speed=speed, total_steps=STEPS)
    a = wav[0].astype(np.float32)
    np.save(f, a)
    return a


def trim_silence(a, thr=0.004):
    idx = np.where(np.abs(a) > thr)[0]
    if len(idx) == 0:
        return a
    return a[max(0, idx[0] - int(0.03 * SR)): idx[-1] + int(0.05 * SR)]


T0 = time.time()
total_len = int((units[-1]["end"] + 60) * SR)
mix = np.zeros(total_len, dtype=np.float32)
sched = []
prev_end = 0.0
GAP = 0.12
for i, u in enumerate(units):
    nxt_start = units[i + 1]["start"] if i + 1 < len(units) else u["end"] + 10
    start = max(u["start"], prev_end + GAP)
    drift = start - u["start"]
    # 이 문장이 쓸 수 있는 시간: 다음 문장 시작 전까지(밀림이 크면 따라잡기 위해 더 빠르게)
    avail = max(1.0, nxt_start - start - GAP)
    a = trim_silence(synth(u["spoken"], voices[u["speaker"]], BASE_SPEED))
    dur = len(a) / SR
    speed = BASE_SPEED
    if dur > avail:
        need = BASE_SPEED * dur / avail
        # 밀림이 쌓였으면 조금 더 공격적으로 따라잡기
        speed = min(MAX_SPEED, need * (1.0 + min(0.15, drift / 30)))
        speed = round(speed, 2)
        a = trim_silence(synth(u["spoken"], voices[u["speaker"]], speed))
        dur = len(a) / SR
    s0 = int(start * SR)
    mix[s0: s0 + len(a)] += a
    prev_end = start + dur
    sched.append({"id": u["id"], "orig_start": u["start"], "start": round(start, 2), "end": round(prev_end, 2), "speed": speed, "voice": voices[u["speaker"]]})
    if i % 25 == 0:
        print(f"[{time.time() - T0:6.0f}s] {i}/{len(units)} 원본 {u['start']:.0f}s → 더빙 {start:.0f}s (밀림 {drift:.1f}s, speed {speed})", flush=True)

peak = float(np.max(np.abs(mix)))
if peak > 0.97:
    mix *= 0.97 / peak
sf.write(work / "dub_ko.wav", mix[: int((max(s["end"] for s in sched) + 3) * SR)], SR, subtype="PCM_16")
(work / "schedule.json").write_text(json.dumps(sched, indent=1), encoding="utf-8")
drifts = [s["start"] - s["orig_start"] for s in sched]
print(f"완료: 최대 밀림 {max(drifts):.1f}s, 평균 {np.mean(drifts):.1f}s, speed>1.2 {sum(1 for s in sched if s['speed'] > 1.2)}개", flush=True)
