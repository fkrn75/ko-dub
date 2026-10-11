# 모델 미리 받기 — 첫 더빙 도중에 다운로드로 멈추지 않게 한다. (없어도 첫 실행 때 자동으로 받아지므로 선택 사항)
#   <python> prefetch_models.py [--nllb]
# Supertonic v3(약 380MB) / Whisper large-v3(약 3GB) / pyannote community-1(약 수백MB) / (선택) NLLB 1.3B(약 5GB)
import argparse
import os
import sys
import time
from pathlib import Path

for _s in (sys.stdout, sys.stderr):  # 파이프·파일로 출력을 받을 때 cp949 가 줄표(—) 등을 못 써서 죽는 것을 막는다(콘솔에서는 영향 없음)
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

os.environ.setdefault("PYANNOTE_METRICS_ENABLED", "false")


def step(name, fn):
    t = time.time()
    print(f"[받는 중] {name} ...", flush=True)
    try:
        fn()
        print(f"[완료] {name} ({time.time() - t:.0f}s)", flush=True)
        return True
    except Exception as e:  # noqa: BLE001 — 하나가 실패해도 나머지는 계속
        print(f"[실패] {name}: {type(e).__name__}: {str(e)[:200]}", flush=True)
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nllb", action="store_true", help="NLLB 번역 모델(약 5GB)도 받는다")
    ap.add_argument("--only", choices=["all", "tts", "asr"], default="all", help="tts=Supertonic 만, asr=Whisper·pyannote(·NLLB)만 (환경이 둘로 나뉜 PC용)")
    ap.add_argument("--supertonic-dir", help="이미 받아 둔 Supertonic v3 폴더(onnx/·voice_styles/ 가 있는 곳)를 등록해 다운로드 없이 사용")
    a = ap.parse_args()
    ok = []

    def tts():
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import supertonic_find

        if a.supertonic_dir:  # 사용자가 지정한 폴더
            ok, why = supertonic_find.check(a.supertonic_dir)
            if not ok:
                raise RuntimeError(f"{a.supertonic_dir}: {why}")
            supertonic_find.register(a.supertonic_dir)
            print(f"  지정한 폴더 등록: {a.supertonic_dir} ({why}) — 다운로드하지 않음", flush=True)
            return
        found = supertonic_find.find(do_scan=True)  # 등록 경로·캐시·HF 캐시·홈 폴더 얕은 검색
        if found:
            supertonic_find.register(found)
            print(f"  이미 있음: {found} ({supertonic_find.check(found)[1]}) — 등록하고 다운로드하지 않음", flush=True)
            return
        from supertonic import TTS

        TTS(model="supertonic-3", auto_download=True)

    def whisper():
        from huggingface_hub import snapshot_download

        snapshot_download("Systran/faster-whisper-large-v3")

    def pyannote():
        from huggingface_hub import get_token
        from pyannote.audio import Pipeline

        tok = os.environ.get("HF_TOKEN") or get_token()
        if not tok:
            raise RuntimeError("Hugging Face 토큰이 없다(setup.ps1 -HfToken <토큰>)")
        if Pipeline.from_pretrained("pyannote/speaker-diarization-community-1", token=tok) is None:
            raise RuntimeError("약관 동의 또는 토큰 권한 문제")

    def nllb():
        from huggingface_hub import snapshot_download

        snapshot_download("facebook/nllb-200-distilled-1.3B", allow_patterns=["*.json", "*.model", "pytorch_model.bin", "*.txt", "*.safetensors"])

    if a.only in ("all", "tts"):
        ok.append(step("Supertonic v3 (TTS, Hugging Face Supertone/supertonic-3 → ~/.cache)", tts))
    if a.only in ("all", "asr"):
        ok.append(step("Whisper large-v3 (전사)", whisper))
        ok.append(step("pyannote community-1 (화자분리)", pyannote))
        if a.nllb:
            ok.append(step("NLLB 1.3B (번역, 구글 대체)", nllb))
    print("모두 준비됨" if all(ok) else "일부 실패 — 위 [실패] 항목을 확인하세요")
    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(main())
