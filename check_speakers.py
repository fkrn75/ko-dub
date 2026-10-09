# 화자 클러스터 검증: 화자별 턴 단위 F0 분포(성별 혼합 여부) + 대표 발화 텍스트
import json, sys
from pathlib import Path
import numpy as np, soundfile as sf, torch
import torchaudio.functional as AF

work = Path(sys.argv[1])
turns = json.loads((work / "diar.json").read_text(encoding="utf-8"))
asr = json.loads((work / "asr.json").read_text(encoding="utf-8"))
audio, sr = sf.read(str(work / "audio16k.wav"), dtype="float32")


def turn_f0(t):
    seg = audio[int(t["start"] * sr): int(min(t["end"], t["start"] + 8) * sr)]
    x = torch.from_numpy(seg).unsqueeze(0)
    f0 = AF.detect_pitch_frequency(x, sr, freq_low=70, freq_high=400, frame_time=0.02).squeeze(0).numpy()
    hop = int(0.02 * sr)
    n = min(len(f0), len(seg) // hop)
    if n < 10:
        return None
    rms = np.array([np.sqrt(np.mean(seg[i * hop:(i + 1) * hop] ** 2) + 1e-12) for i in range(n)])
    ok = (rms > np.percentile(rms, 40)) & (f0[:n] > 75) & (f0[:n] < 380)
    return float(np.median(f0[:n][ok])) if ok.sum() > 8 else None


for spk in sorted(set(t["speaker"] for t in turns)):
    ts = [t for t in turns if t["speaker"] == spk and t["end"] - t["start"] >= 2.0]
    vals = [(t, turn_f0(t)) for t in ts]
    vals = [(t, v) for t, v in vals if v]
    f = np.array([v for _, v in vals])
    print(f"\n== {spk}: 턴 {len(f)}개  F0 분위 10/50/90 = {np.percentile(f,10):.0f}/{np.percentile(f,50):.0f}/{np.percentile(f,90):.0f}  <150: {(f<150).sum()}  >=165: {(f>=165).sum()}")
    # 첫 등장 시각과 샘플 발화
    first = min(t["start"] for t in turns if t["speaker"] == spk)
    last = max(t["end"] for t in turns if t["speaker"] == spk)
    print(f"   등장 {first:.0f}s ~ {last:.0f}s")
    for t, v in vals[:3]:
        txt = " ".join(s["text"] for s in asr if s["start"] < t["end"] and s["end"] > t["start"])
        print(f"   [{t['start']:.0f}s F0={v:.0f}] {txt[:130]}")
