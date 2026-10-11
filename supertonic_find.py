# 이미 받아 둔 Supertonic v3 모델 폴더를 찾아 재사용한다(표준 라이브러리만 사용).
# 찾는 순서: ① 등록된 경로(model_paths.json, `kodub models --supertonic-dir`) ② 환경변수 KO_DUB_SUPERTONIC_DIR
#           ③ SUPERTONIC_CACHE_DIR ④ supertonic 패키지 기본 캐시 ~/.cache/supertonic3 ⑤ Hugging Face 캐시(Supertone/supertonic-3)
#           ⑥ (scan=True 일 때만) 홈 폴더·다른 드라이브 얕은 검색 — 다른 앱이나 git clone 으로 받아 둔 사본
# 유효 조건: onnx 4종 + tts.json·unicode_indexer.json + 음성 10종(M1~M5, F1~F5). config.json 의 model_name 으로 v3 여부 확인.
import json
import os
import string
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REG = HERE / "model_paths.json"
REQUIRED = [f"onnx/{n}.onnx" for n in ("duration_predictor", "text_encoder", "vector_estimator", "vocoder")] + \
           ["onnx/tts.json", "onnx/unicode_indexer.json"] + [f"voice_styles/{g}{i}.json" for g in "MF" for i in range(1, 6)]
SKIP = {"node_modules", ".git", "site-packages", "$recycle.bin", "windows", "program files", "program files (x86)",
        "programdata", "system volume information", "appdata", "venv", ".venv", "venv-all", "__pycache__", "tts_cache"}


def check(d):
    """폴더가 쓸 수 있는 Supertonic v3 인지. 반환: (사용 가능, 설명)"""
    d = Path(d)
    if not d.is_dir():
        return False, "폴더 없음"
    miss = [r for r in REQUIRED if not (d / r).exists()]
    if miss:
        return False, f"파일 {len(miss)}개 없음(예: {miss[0]})"
    cfg = d / "config.json"
    if cfg.exists():
        try:
            name = json.loads(cfg.read_text(encoding="utf-8-sig")).get("model_name", "")
        except Exception:  # noqa: BLE001
            name = ""
        if name and "3" not in name:
            return False, f"다른 버전({name})"
        return True, name or "버전 표기 없음(파일 구성은 v3와 같음)"
    return True, "config.json 없음(파일 구성은 v3와 같음)"


def _registered():
    if REG.exists():
        try:
            return json.loads(REG.read_text(encoding="utf-8-sig")).get("supertonic3")
        except Exception:  # noqa: BLE001
            return None
    return None


def register(d):
    data = {}
    if REG.exists():
        try:
            data = json.loads(REG.read_text(encoding="utf-8-sig"))
        except Exception:  # noqa: BLE001
            data = {}
    data["supertonic3"] = str(Path(d).resolve())
    REG.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")  # BOM 없는 UTF-8


def _hf_snapshots():
    hub = Path(os.environ.get("HF_HUB_CACHE") or (Path(os.environ.get("HF_HOME") or (Path.home() / ".cache" / "huggingface")) / "hub"))
    snap = hub / "models--Supertone--supertonic-3" / "snapshots"
    return sorted(snap.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True) if snap.is_dir() else []


def known_candidates():
    c = [_registered(), os.environ.get("KO_DUB_SUPERTONIC_DIR"), os.environ.get("SUPERTONIC_CACHE_DIR"), str(Path.home() / ".cache" / "supertonic3")]
    c += [str(p) for p in _hf_snapshots()]
    return [x for x in c if x]


def scan(max_depth=4, time_limit=20.0):
    """홈 폴더와 다른 드라이브 루트를 얕게 훑어 onnx/ 와 voice_styles/ 가 함께 있는 폴더를 찾는다(시간 제한)."""
    roots = [Path.home()]
    if os.name == "nt":
        home_drive = Path.home().anchor.upper()
        roots += [Path(f"{d}:\\") for d in string.ascii_uppercase if f"{d}:\\" != home_drive and Path(f"{d}:\\").exists()]
    t0, found = time.time(), []
    stack = [(r, 0) for r in roots]
    while stack and time.time() - t0 < time_limit:
        d, depth = stack.pop()
        try:
            names = {e.name: e for e in os.scandir(d) if e.is_dir(follow_symlinks=False)}
        except OSError:
            continue
        if "onnx" in names and "voice_styles" in names:
            found.append(Path(d))
        if depth < max_depth:
            stack += [(Path(e.path), depth + 1) for n, e in names.items() if n.lower() not in SKIP and not n.startswith(".") or n == ".cache"]
    return found


def find(do_scan=False):
    """쓸 수 있는 Supertonic v3 폴더(Path) 또는 None."""
    seen = set()
    cands = [Path(x) for x in known_candidates()] + (scan() if do_scan else [])
    for c in cands:
        k = str(c).lower()
        if k in seen:
            continue
        seen.add(k)
        ok, _ = check(c)
        if ok:
            return c
    return None


if __name__ == "__main__":
    # 사용: py -3 supertonic_find.py [--scan] [--register <폴더>]
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    if "--register" in sys.argv:
        d = sys.argv[sys.argv.index("--register") + 1]
        ok, why = check(d)
        if not ok:
            print(f"[실패] {d}: {why}")
            sys.exit(1)
        register(d)
        print(f"[등록] {d} ({why}) → 이 폴더를 다운로드 없이 사용")
        sys.exit(0)
    m = find(do_scan="--scan" in sys.argv)
    print(f"[있음] {m} ({check(m)[1]})" if m else "[없음] 이미 받아 둔 Supertonic v3 를 찾지 못함")
    sys.exit(0 if m else 1)
