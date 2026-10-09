# 1단계: 음성추출 → Whisper 전사(영어, 단어 타임스탬프) → pyannote 화자분리 → 화자별 성별(F0) 판정
# 실행: python_paths.json 의 "gpu" 인터프리터(torch cuda, faster-whisper, pyannote 4.0 설치된 환경)로 실행
#   <gpu python> step1_asr_diarize.py <입력 영상 또는 오디오> <작업폴더>
import json
import os
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault("PYANNOTE_METRICS_ENABLED", "false")  # 외부 전송 차단
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # 도구 폴더의 diarize_lite 를 어디서 실행해도 찾게 함

import numpy as np
import soundfile as sf

src = Path(sys.argv[1])
work = Path(sys.argv[2])
work.mkdir(parents=True, exist_ok=True)
wav = work / "audio16k.wav"

# HF 토큰은 로컬 캐시 파일에서 읽어 환경변수로만 전달(출력 금지)
tok_file = Path.home() / ".cache" / "huggingface" / "token"
if "HF_TOKEN" not in os.environ and tok_file.exists():
    os.environ["HF_TOKEN"] = tok_file.read_text().strip()

T0 = time.time()


def log(msg):
    print(f"[{time.time() - T0:7.1f}s] {msg}", flush=True)


# ── 1) 16kHz 모노 WAV 추출 ──
if not wav.exists():
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)],
        check=True,
    )
log(f"오디오 추출 완료 {wav}")

# ── 2) Whisper 전사 ──
asr_json = work / "asr.json"
if not asr_json.exists():
    from faster_whisper import BatchedInferencePipeline, WhisperModel

    model = WhisperModel("large-v3", device="cuda", compute_type="float16")
    pipe = BatchedInferencePipeline(model=model)
    # 영상별 용어 힌트는 환경변수 WHISPER_PROMPT 로 덮어쓴다(없으면 첫 영상용 기본값)
    prompt = os.environ.get("WHISPER_PROMPT") or (
        "NVIDIA Omniverse livestream about Physical AI. Terms: Isaac Sim, Isaac Lab, OpenUSD, USD, "
        "real-to-sim-to-real, sim-to-real, Sim2Real, R2S2R Arena, sim2world.ai, Industrial Next, Versor, "
        "AllSides, Gaussian splatting, reinforcement learning, digital twin, GR00T, Cosmos."
    )
    segs, info = pipe.transcribe(
        str(wav),
        language="en",
        batch_size=8,
        word_timestamps=True,
        vad_filter=True,
        initial_prompt=prompt,
        condition_on_previous_text=False,
    )
    out = []
    for s in segs:
        out.append(
            {
                "start": s.start,
                "end": s.end,
                "text": s.text.strip(),
                "words": [{"w": w.word, "s": w.start, "e": w.end} for w in (s.words or [])],
            }
        )
        if len(out) % 50 == 0:
            log(f"전사 {len(out)}개 세그먼트, 현재 {s.end:.0f}s / {info.duration:.0f}s")
    asr_json.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    del pipe, model
    import gc, torch

    gc.collect()
    torch.cuda.empty_cache()
log("전사 완료")

# ── 3) 화자분리 (도구 폴더의 diarize_lite.py: pyannote community-1) ──
dz_json = work / "diar.json"
if not dz_json.exists():
    from diarize_lite import diarize

    turns = diarize(str(wav), max_speakers=10)
    dz_json.write_text(
        json.dumps([{"speaker": t.speaker, "start": t.start, "end": t.end} for t in turns]),
        encoding="utf-8",
    )
log("화자분리 완료")
turns = json.loads(dz_json.read_text(encoding="utf-8"))

# ── 4) 화자별 성별 판정: 유성음 프레임 F0 중앙값 (남 < ~160Hz < 여) ──
import torch
import torchaudio.functional as AF

audio, sr = sf.read(str(wav), dtype="float32")
stats = {}
spk_dur = {}
for t in turns:
    spk_dur[t["speaker"]] = spk_dur.get(t["speaker"], 0.0) + (t["end"] - t["start"])
for spk in sorted(spk_dur, key=lambda s: -spk_dur[s]):
    if spk_dur[spk] < 5:
        continue
    f0s = []
    # 화자별로 긴 턴(>=1.5s)부터 최대 40개 샘플링
    cand = sorted([t for t in turns if t["speaker"] == spk and t["end"] - t["start"] >= 1.5], key=lambda t: -(t["end"] - t["start"]))[:40]
    for t in cand:
        seg = audio[int(t["start"] * sr): int(min(t["end"], t["start"] + 8) * sr)]
        x = torch.from_numpy(seg).unsqueeze(0)
        f0 = AF.detect_pitch_frequency(x, sr, freq_low=70, freq_high=400, frame_time=0.02).squeeze(0).numpy()
        # 에너지 있는 프레임만(무성/무음 제외)
        hop = int(0.02 * sr)
        n = min(len(f0), len(seg) // hop)
        rms = np.array([np.sqrt(np.mean(seg[i * hop:(i + 1) * hop] ** 2) + 1e-12) for i in range(n)])
        ok = (rms[:n] > np.percentile(rms[:n], 40)) & (f0[:n] > 75) & (f0[:n] < 380)
        f0s.extend(f0[:n][ok].tolist())
    med = float(np.median(f0s)) if f0s else 0.0
    stats[spk] = {"seconds": round(spk_dur[spk], 1), "f0_median": round(med, 1), "n_frames": len(f0s)}
    log(f"{spk}: 발화 {spk_dur[spk]:.0f}s, F0 중앙값 {med:.0f}Hz")
(work / "speaker_stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
log("모든 단계 완료")
