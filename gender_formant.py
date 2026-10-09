# 성별 보조 판정: 유성음 프레임의 F0 분위 + LPC 포먼트(F1/F2) 중앙값을 기지(旣知) 화자와 비교
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
import torchaudio.functional as AF
from scipy.linalg import solve_toeplitz


def formants(frame, sr, order=14):
    x = frame * np.hamming(len(frame))
    x = np.append(x[0], x[1:] - 0.97 * x[:-1])  # 프리엠퍼시스
    r = np.correlate(x, x, "full")[len(x) - 1: len(x) + order]
    if r[0] < 1e-9:
        return None
    try:
        a = solve_toeplitz(r[:order], r[1: order + 1])
    except Exception:
        return None
    roots = np.roots(np.concatenate(([1.0], -a)))
    roots = roots[np.imag(roots) > 0.01]
    freqs = np.sort(np.angle(roots) * sr / (2 * np.pi))
    bw = -0.5 * sr / (2 * np.pi) * 2 * np.log(np.abs(roots))
    cand = [f for f, b in zip(np.angle(roots) * sr / (2 * np.pi), -np.log(np.abs(roots)) * sr / np.pi) if 200 < f < 3500 and b < 400]
    cand = sorted(cand)
    return cand[:2] if len(cand) >= 2 else None


def analyze(wav_path, turns, spk, sr_expect=16000, max_turns=30):
    audio, sr = sf.read(wav_path, dtype="float32")
    cand = sorted([t for t in turns if t["speaker"] == spk and t["end"] - t["start"] >= 1.5], key=lambda t: -(t["end"] - t["start"]))[:max_turns]
    f0s, f1s, f2s = [], [], []
    hop, win = int(0.02 * sr), int(0.03 * sr)
    for t in cand:
        seg = audio[int(t["start"] * sr): int(min(t["end"], t["start"] + 8) * sr)]
        f0 = AF.detect_pitch_frequency(torch.from_numpy(seg).unsqueeze(0), sr, freq_low=70, freq_high=400, frame_time=0.02).squeeze(0).numpy()
        n = min(len(f0), len(seg) // hop - 2)
        rms = np.array([np.sqrt(np.mean(seg[i * hop:(i + 1) * hop] ** 2) + 1e-12) for i in range(n)])
        thr = np.percentile(rms, 50)
        for i in range(n):
            if rms[i] > thr and 75 < f0[i] < 380:
                f0s.append(f0[i])
                fr = seg[i * hop: i * hop + win]
                if len(fr) == win:
                    ff = formants(fr, sr)
                    if ff:
                        f1s.append(ff[0]); f2s.append(ff[1])
    return {
        "f0_p10_50_90": [round(float(np.percentile(f0s, p))) for p in (10, 50, 90)],
        "F1_med": round(float(np.median(f1s))), "F2_med": round(float(np.median(f2s))), "n": len(f0s),
    }


for w, spks in json.loads(sys.argv[1]).items():
    work = Path(w)
    turns = json.loads((work / "diar.json").read_text(encoding="utf-8"))
    for s in spks:
        print(work.name, s, analyze(str(work / "audio16k.wav"), turns, s), flush=True)
