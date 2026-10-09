# 더빙 음성 검증: 합성된 한국어 음성을 한국어 Whisper 로 되받아쓰기해 의도한 발음 텍스트와의 유사도를 잰다.
#   <gpu python> verify_ko.py <작업폴더> [--n 12] [--ids 5,120,450]
# 출력: 문장별 유사도(0~1), 평균, 0.6 미만 개수, 낮은 문장의 의도/인식 텍스트. 평균 0.9 이상이면 정상.
import argparse
import difflib
import json
import random
import re
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from faster_whisper import WhisperModel
from scipy.signal import resample_poly

ap = argparse.ArgumentParser()
ap.add_argument("work")
ap.add_argument("--n", type=int, default=12, help="무작위 표본 문장 수")
ap.add_argument("--ids", help="반드시 포함할 문장 id(쉼표 구분, 교정한 문장 등)")
a = ap.parse_args()

W = Path(a.work)
sched = json.loads((W / "schedule.json").read_text(encoding="utf-8"))
spoken = json.loads((W / "spoken.json").read_text(encoding="utf-8"))
wav = W / "dub_ko.wav"
info = sf.info(str(wav))
sr = info.samplerate
print(f"dub_ko.wav 길이 {info.duration / 60:.1f}분, 문장 {len(sched)}개")

random.seed(7)
ids = [int(x) for x in a.ids.split(",")] if a.ids else []
pool = [i for i in range(len(sched)) if i not in ids and len(spoken[i]["spoken"]) >= 8]  # 너무 짧은 추임새는 표본에서 제외
ids += random.sample(pool, min(a.n, len(pool)))

model = WhisperModel("medium", device="cuda" if __import__("torch").cuda.is_available() else "cpu", compute_type="float16" if __import__("torch").cuda.is_available() else "int8")


def norm(t):
    return re.sub(r"[^0-9가-힣a-zA-Z]", "", t)


scores = []
for i in ids:
    s = sched[i]
    seg, _ = sf.read(str(wav), start=int(s["start"] * sr), stop=int((s["end"] + 0.3) * sr), dtype="float32")
    seg16 = resample_poly(seg, 160, 441).astype(np.float32)
    segs, _ = model.transcribe(seg16, language="ko", beam_size=3)
    heard = " ".join(x.text.strip() for x in segs)
    want = spoken[i]["spoken"]
    r = difflib.SequenceMatcher(None, norm(want), norm(heard)).ratio()
    scores.append(r)
    print(f"[{i}] {s['voice']} speed={s['speed']} {s['end'] - s['start']:.1f}s 유사도={r:.2f}")
    if r < 0.6 or (a.ids and i in [int(x) for x in a.ids.split(",")]):
        print("   의도:", want[:100])
        print("   인식:", heard[:100])
print(f"평균 유사도 {np.mean(scores):.2f} | 0.6 미만 {sum(v < 0.6 for v in scores)}/{len(scores)} (표본이므로 전체 품질의 추정일 뿐, 전체를 들은 것은 아님)")
sys.exit(0)
