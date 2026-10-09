# 독립 실행형 화자분리 (pyannote community-1)
# MeetScribe(sidecar/meetscribe/pipeline/diarize.py)의 로직을 그대로 옮겨, MeetScribe 설치 없이 도구 폴더만으로 동작하게 만든 것.
#  - 2시간 이하 오디오는 한 번에 처리, 그보다 길면 청크(기본 2시간)로 나눠 처리한 뒤 화자 라벨을 정합(stitch)
#  - torchcodec(ffmpeg DLL 의존) 대신 soundfile 로 파형을 직접 읽어 Windows DLL 문제를 우회
#  - pyannote 의 텔레메트리(otel 전송)는 import 전에 꺼 둔다(완전 로컬 원칙)
from __future__ import annotations

import contextlib
import os
import wave
from dataclasses import dataclass

os.environ.setdefault("PYANNOTE_METRICS_ENABLED", "false")

DIARIZATION_MODEL = os.environ.get("KO_DUB_DIARIZE_MODEL") or "pyannote/speaker-diarization-community-1"
CHUNK_SEC = 7200.0  # 2시간 이하는 청크 경계 없이 단일 처리


@dataclass
class Turn:
    speaker: str
    start: float
    end: float


def _hf_token() -> str | None:
    tok = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if tok:
        return tok
    try:
        from huggingface_hub import get_token

        return get_token()
    except Exception:
        return None


def diarize(wav_path: str, max_speakers: int | None = None, min_speakers: int | None = None, device: str | None = None) -> list[Turn]:
    """16kHz mono WAV 를 화자 구간 목록으로 분리한다(시간순 정렬, 인접 같은 화자 병합).

    HF 토큰이 없거나 pyannote 약관에 동의하지 않았으면 명확한 예외를 던진다(조용히 실패하지 않는다).
    """
    token = _hf_token()
    if not token:
        raise RuntimeError(
            "화자분리에는 Hugging Face 토큰이 필요합니다. 환경변수 HF_TOKEN 을 설정하거나 `huggingface-cli login` 을 하고, "
            f"https://huggingface.co/{DIARIZATION_MODEL} 에서 약관에 동의하세요(무료)."
        )
    import torch

    dev = device or ("cuda" if torch.cuda.is_available() else "cpu")
    pipeline = _load_pipeline(token, dev)
    try:
        duration = _audio_duration_sec(wav_path)
        if CHUNK_SEC <= 0 or duration <= CHUNK_SEC:
            turns = _diarize_window(pipeline, wav_path, 0.0, duration, min_speakers, max_speakers)
            return _merge_adjacent(turns)
        # 긴 오디오: 청크별 처리 후 라벨 정합. 청크에는 상한만 적용(하한을 주면 그 구간에 없던 화자까지 억지로 쪼갠다).
        turns = _diarize_chunked(pipeline, wav_path, duration, CHUNK_SEC, max_speakers)
        return _merge_adjacent(turns)
    finally:
        del pipeline


def _load_pipeline(token: str, device: str):
    import torch
    from pyannote.audio import Pipeline

    pipeline = Pipeline.from_pretrained(DIARIZATION_MODEL, token=token)
    if pipeline is None:
        raise RuntimeError(f"pyannote 파이프라인 로드 실패: {DIARIZATION_MODEL} (토큰 권한 또는 약관 동의를 확인하세요).")
    pipeline.to(torch.device(device))
    return pipeline


def _extract_annotation(output):
    """pyannote 4.0 은 DiarizeOutput, 3.x 는 Annotation 을 돌려준다 — 둘 다 itertracks 가능한 Annotation 으로 정규화."""
    for attr in ("speaker_diarization", "diarization", "annotation"):
        if hasattr(output, attr):
            return getattr(output, attr)
    return output


def _diarize_window(pipeline, wav_path: str, offset_sec: float, duration_sec: float, min_speakers, max_speakers) -> list[Turn]:
    """[offset, offset+duration) 구간만 잘라 화자분리하고 전역 시간축으로 보정한다."""
    import soundfile as sf
    import torch

    kwargs = {}
    if min_speakers is not None:
        kwargs["min_speakers"] = min_speakers
    if max_speakers is not None:
        kwargs["max_speakers"] = max_speakers

    info = sf.info(wav_path)
    sr = info.samplerate
    if duration_sec > 0:
        start_f = max(0, int(offset_sec * sr))
        stop_f = min(info.frames, int((offset_sec + duration_sec) * sr))
        data, sr = sf.read(wav_path, start=start_f, stop=stop_f, dtype="float32")
        shift = offset_sec  # 잘린 파형은 0초부터 시작 → 전역 시간으로 복원
    else:
        data, sr = sf.read(wav_path, dtype="float32")
        shift = 0.0
    if getattr(data, "ndim", 1) > 1:
        data = data.mean(axis=1)
    waveform = torch.from_numpy(data).unsqueeze(0)

    annotation = _extract_annotation(pipeline({"waveform": waveform, "sample_rate": sr}, **kwargs))
    turns = [Turn(str(spk), float(seg.start) + shift, float(seg.end) + shift) for seg, _t, spk in annotation.itertracks(yield_label=True)]
    turns.sort(key=lambda t: (t.start, t.end))
    return turns


def _diarize_chunked(pipeline, wav_path: str, duration_sec: float, chunk_sec: float, max_speakers) -> list[Turn]:
    all_turns: list[Turn] = []
    label_map: dict[str, str] = {}
    next_global = 0
    prev_chunk: list[Turn] = []
    offset = 0.0
    while offset < duration_sec:
        window = min(chunk_sec, duration_sec - offset)
        local = _diarize_window(pipeline, wav_path, offset, window, None, max_speakers)
        local_map, next_global = _stitch_labels(local, prev_chunk, label_map, next_global)
        relabeled = [Turn(local_map[t.speaker], t.start, t.end) for t in local]
        all_turns.extend(relabeled)
        prev_chunk = relabeled
        offset += window
    all_turns.sort(key=lambda t: (t.start, t.end))
    return all_turns


def _stitch_labels(local_turns, prev_chunk_turns, label_map, next_global):
    """청크 로컬 라벨 → 전역 라벨(휴리스틱): 직전 청크의 지배 화자와 이번 청크의 지배 화자를 같은 사람으로 보고 잇는다."""
    local_map: dict[str, str] = {}
    prev_dom = _dominant_speaker(prev_chunk_turns)
    local_dom = _dominant_speaker(local_turns)
    if prev_dom is not None and local_dom is not None:
        local_map[local_dom] = prev_dom
    for t in local_turns:
        if t.speaker in local_map:
            continue
        local_map[t.speaker] = f"SPEAKER_{next_global:02d}"
        next_global += 1
    return local_map, next_global


def _dominant_speaker(turns) -> str | None:
    if not turns:
        return None
    totals: dict[str, float] = {}
    for t in turns:
        totals[t.speaker] = totals.get(t.speaker, 0.0) + max(0.0, t.end - t.start)
    return max(totals.items(), key=lambda kv: kv[1])[0]


def _merge_adjacent(turns: list[Turn], gap: float = 0.5) -> list[Turn]:
    """같은 화자의 인접(gap 초 이하) turn 을 합쳐 조각화를 줄인다."""
    if not turns:
        return turns
    ordered = sorted(turns, key=lambda t: (t.start, t.end))
    merged = [Turn(ordered[0].speaker, ordered[0].start, ordered[0].end)]
    for t in ordered[1:]:
        last = merged[-1]
        if t.speaker == last.speaker and t.start - last.end <= gap:
            if t.end > last.end:
                last.end = t.end
        else:
            merged.append(Turn(t.speaker, t.start, t.end))
    return merged


def _audio_duration_sec(wav_path: str) -> float:
    try:
        with contextlib.closing(wave.open(wav_path, "rb")) as wf:
            return wf.getnframes() / float(wf.getframerate() or 1)
    except Exception:
        return 0.0
