# ko-dub 환경 점검 — 표준 라이브러리만 사용(어느 파이썬으로도 실행 가능)
#   py -3 check_env.py            전체 점검 표 출력 (필수 항목이 없으면 종료코드 1)
#   py -3 check_env.py --net      + Hugging Face 인터넷 접속·pyannote 약관 동의(gated 접근) 확인
#   py -3 check_env.py --py gpu   전사·화자분리·번역(NLLB)용 파이썬 경로만 출력 (tts 도 가능)
#   py -3 check_env.py --json     기계가 읽는 JSON 출력
# 인터프리터 경로는 python_paths.json 에서 읽는다(환경변수 KO_DUB_PY_GPU / KO_DUB_PY_TTS 가 있으면 우선).
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):  # 파이프·파일로 출력을 받을 때 cp949 가 줄표(—) 등을 못 써서 죽는 것을 막는다(콘솔에서는 영향 없음)
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = Path(__file__).resolve().parent
HUB = Path(os.environ.get("HF_HUB_CACHE") or (Path(os.environ.get("HF_HOME") or (Path.home() / ".cache" / "huggingface")) / "hub"))

GPU_MODULES = ["torch", "faster_whisper", "pyannote.audio", "transformers", "sentencepiece", "soundfile", "scipy", "numpy", "huggingface_hub"]
TTS_MODULES = ["supertonic", "onnxruntime", "soundfile", "numpy", "scipy"]
HF_REPOS = {
    "models--Systran--faster-whisper-large-v3": "Whisper large-v3 전사 모델(약 3GB)",
    "models--pyannote--speaker-diarization-community-1": "pyannote 화자분리 모델",
    "models--facebook--nllb-200-distilled-1.3B": "NLLB 번역 모델(약 5GB, 구글 번역이 막혔을 때만 필요)",
}


def run(cmd, timeout=60, cwd=None):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd, encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return 124, "시간 초과"


def find_exe(name):
    p = shutil.which(name)
    if p:
        return p
    # winget 으로 방금 설치했으면 PATH 에 아직 없을 수 있다
    la = os.environ.get("LOCALAPPDATA")
    if la:
        for g in glob.glob(str(Path(la) / "Microsoft" / "WinGet" / "Packages" / "Gyan.FFmpeg*" / "*" / "bin" / (name + ".exe"))):
            return g
    return None


def interpreter(role):
    env = os.environ.get("KO_DUB_PY_" + role.upper())
    if env:
        return env
    f = HERE / "python_paths.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8-sig")).get(role)
        except Exception:
            return None
    return None


def ytdlp_command():
    """yt-dlp 를 실행할 명령(문자열 리스트)을 찾는다: gpu/tts 파이썬 → 시스템 py -3 → PATH 의 yt-dlp. 없으면 None."""
    cands = []
    for role in ("gpu", "tts"):
        p = interpreter(role)
        if p and Path(p).exists():
            cands.append([p, "-m", "yt_dlp"])
    cands.append(["py", "-3", "-m", "yt_dlp"])
    exe = shutil.which("yt-dlp")
    if exe:
        cands.append([exe])
    for c in cands:
        rc, out = run(c + ["--version"], timeout=60)
        if rc == 0 and re.match(r"\d{4}\.", out.strip()):
            return c
    return None


def probe(py, modules):
    """인터프리터에서 모듈을 import 해 버전을 모은다. 없는 모듈은 None."""
    code = (
        "import json,importlib,sys\nr={}\n"
        "for m in %r:\n"
        "    try:\n"
        "        mod=importlib.import_module(m); r[m]=getattr(mod,'__version__','설치됨')\n"
        "    except Exception as e:\n"
        "        r[m]=None\n"
        "try:\n"
        "    import torch; r['_cuda']=bool(torch.cuda.is_available()); r['_gpu']=torch.cuda.get_device_name(0) if r['_cuda'] else ''\n"
        "except Exception:\n"
        "    r['_cuda']=False; r['_gpu']=''\n"
        "print('@@'+json.dumps(r))\n" % (modules,)
    )
    rc, out = run([py, "-c", code], timeout=180)
    m = re.search(r"@@(\{.*\})", out)
    return json.loads(m.group(1)) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--py", choices=["gpu", "tts"])
    ap.add_argument("--net", action="store_true")
    ap.add_argument("--ytdlp", action="store_true", help="yt-dlp 실행 명령만 출력")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.ytdlp:
        c = ytdlp_command()
        print(" ".join('"%s"' % x if " " in x else x for x in c) if c else "", end="")
        return 0 if c else 2

    if a.py:
        p = interpreter(a.py)
        if p and Path(p).exists():
            print(p)
            return 0
        print("", end="")
        return 2

    rows = []  # (필수여부, 항목, 상태OK/WARN/FAIL, 상세, 해결)

    def add(req, name, status, detail="", fix=""):
        rows.append((req, name, status, detail, fix))

    # 1) 외부 프로그램
    for exe in ("ffmpeg", "ffprobe"):
        p = find_exe(exe)
        if p:
            rc, out = run([p, "-version"])
            add("필수", exe, "OK", out.splitlines()[0][:60] if out else p)
        else:
            add("필수", exe, "FAIL", "찾을 수 없음", "setup.ps1 -InstallMissing  (또는 winget install Gyan.FFmpeg)")

    node = shutil.which("node")
    if node:
        rc, out = run([node, "--version"])
        m = re.match(r"v(\d+)\.(\d+)", out.strip())
        ok = bool(m) and (int(m.group(1)) >= 24 or (int(m.group(1)) == 22 and int(m.group(2)) >= 18) or (int(m.group(1)) == 23 and int(m.group(2)) >= 6))
        add("필수", "node (발음 변환용, 22.18 이상)", "OK" if ok else "FAIL", out.strip(), "" if ok else "setup.ps1 -InstallMissing  (또는 winget install OpenJS.NodeJS.LTS)")
        if ok and (HERE / "spoken.mjs").exists():
            try:
                p = subprocess.run([node, str(HERE / "spoken.mjs")], input='["3D 2024년"]', capture_output=True, text=True, timeout=30, encoding="utf-8")
                good = "쓰리디" in p.stdout and "이천이십사" in p.stdout
                add("필수", "발음 변환 자체 시험(spoken.mjs)", "OK" if good else "FAIL", p.stdout.strip()[:60] if good else (p.stderr or p.stdout)[:120], "" if good else "speak/ 폴더 4개 파일과 node 버전을 확인")
            except Exception as e:
                add("필수", "발음 변환 자체 시험(spoken.mjs)", "FAIL", str(e)[:100])
    else:
        add("필수", "node (발음 변환용, 22.18 이상)", "FAIL", "찾을 수 없음", "setup.ps1 -InstallMissing  (또는 winget install OpenJS.NodeJS.LTS)")

    rc, out = run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"])
    if rc == 0 and out.strip():
        mem = re.search(r"(\d+)\s*MiB", out)
        gb = int(mem.group(1)) / 1024 if mem else 0
        add("권장", "NVIDIA GPU", "OK" if gb >= 6 else "WARN", out.strip().splitlines()[0], "" if gb >= 6 else "VRAM 6GB 미만이면 Whisper large-v3/NLLB 가 메모리 부족일 수 있음")
    else:
        add("권장", "NVIDIA GPU", "WARN", "없음", "GPU 없이도 되지만 전사·화자분리·번역이 몇 배~수십 배 느려진다(CPU)")

    # 2) 파이썬 환경
    gpu_py, tts_py = interpreter("gpu"), interpreter("tts")
    rc, out = run(["py", "-3.12", "--version"])
    add("조건부", "시스템 Python 3.12 (새 환경을 만들 때만)", "OK" if rc == 0 else "WARN", out.strip() if rc == 0 else "없음", "" if rc == 0 else "setup.ps1 -InstallMissing  (또는 winget install Python.Python.3.12)")

    if not gpu_py or not Path(gpu_py).exists():
        add("필수", "gpu 파이썬(전사·화자분리·번역)", "FAIL", f"python_paths.json 없음 또는 경로 무효: {gpu_py}", "setup.ps1 실행 (통합 환경 venv-all 생성)")
        gp = None
    else:
        gp = probe(gpu_py, GPU_MODULES)
        if gp is None:
            add("필수", "gpu 파이썬 실행", "FAIL", gpu_py, "인터프리터가 깨졌다 — setup.ps1 -Force")
        else:
            miss = [m for m in GPU_MODULES if not gp.get(m)]
            add("필수", "gpu 파이썬 패키지", "OK" if not miss else "FAIL", ("전부 설치됨 (torch %s)" % gp.get("torch")) if not miss else "없음: " + ", ".join(miss), "" if not miss else "setup.ps1 (requirements-all.txt 설치)")
            if gp.get("torch"):
                add("권장", "torch CUDA 사용 가능", "OK" if gp.get("_cuda") else "WARN", gp.get("_gpu") or "CPU 전용 빌드이거나 GPU 없음", "" if gp.get("_cuda") else "CUDA 빌드 torch 필요: setup.ps1 -Force (NVIDIA GPU 가 있을 때)")

    if not tts_py or not Path(tts_py).exists():
        add("필수", "tts 파이썬(음성 합성)", "FAIL", f"경로 무효: {tts_py}", "setup.ps1 실행")
    else:
        tp = probe(tts_py, TTS_MODULES)
        if tp is None:
            add("필수", "tts 파이썬 실행", "FAIL", tts_py)
        else:
            miss = [m for m in TTS_MODULES if not tp.get(m)]
            add("필수", "tts 파이썬 패키지", "OK" if not miss else "FAIL", ("전부 설치됨 (supertonic %s)" % tp.get("supertonic")) if not miss else "없음: " + ", ".join(miss), "" if not miss else "setup.ps1")
            if not miss:
                code = "from supertonic.loader import get_cache_dir; from pathlib import Path; d=Path(get_cache_dir('supertonic-3')); print('@@', d, len(list(d.rglob('*.onnx'))) if d.exists() else 0)"
                rc, out = run([tts_py, "-c", code], timeout=60)
                m = re.search(r"@@ (.+) (\d+)", out)
                n = int(m.group(2)) if m else 0
                add("정보", "Supertonic v3 모델 캐시", "OK" if n >= 4 else "WARN", f"{n}개 onnx" if n else "아직 없음", "" if n >= 4 else "첫 합성 때 약 380MB 자동 다운로드 (미리 받기: setup.ps1 -DownloadModels)")

    yc = ytdlp_command()
    add("권장", "yt-dlp (유튜브 URL 다운로드용)", "OK" if yc else "WARN", " ".join(yc) if yc else "없음", "" if yc else "setup.ps1 (venv-all 에 설치) 또는 py -3 -m pip install yt-dlp")

    # 3) 도구 파일
    need = ["step1_asr_diarize.py", "step2_units_translate.py", "step3_tts_mix.py", "step4_mux.ps1", "diarize_lite.py", "spoken.mjs", "glossary.json", "ko_fixes.json", "scan_repeat.py", "speak/speak.ts", "speak/speak.datetime.ts", "speak/speak.misc.ts", "speak/speak.units.ts"]
    lost = [f for f in need if not (HERE / f).exists()]
    add("필수", "도구 파일", "OK" if not lost else "FAIL", "전부 있음" if not lost else "없음: " + ", ".join(lost), "" if not lost else "도구 폴더를 통째로 다시 복사")

    # 4) 모델 캐시(정보) / Hugging Face 토큰 / 디스크
    for d, label in HF_REPOS.items():
        has = (HUB / d).exists()
        add("정보", "모델 캐시: " + label, "OK" if has else "WARN", "있음" if has else "아직 없음", "" if has else "첫 사용 때 자동 다운로드 (미리 받기: setup.ps1 -DownloadModels" + (" -WithNLLB" if "nllb" in d else "") + ")")

    tok = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    tf = Path(os.environ.get("HF_HOME") or (Path.home() / ".cache" / "huggingface")) / "token"
    has_tok = bool(tok) or tf.exists()
    add("필수", "Hugging Face 토큰 (화자분리용)", "OK" if has_tok else "FAIL", "있음" if has_tok else "없음",
        "" if has_tok else "huggingface.co/settings/tokens 에서 Read 토큰 발급 → setup.ps1 -HfToken <토큰>, 그리고 https://huggingface.co/pyannote/speaker-diarization-community-1 에서 약관 동의")
    if a.net and has_tok and gpu_py and Path(gpu_py).exists():
        code = ("import os\nfrom huggingface_hub import hf_hub_download\n"
                "try:\n    p=hf_hub_download('pyannote/speaker-diarization-community-1','config.yaml'); print('@@OK')\n"
                "except Exception as e:\n    print('@@FAIL', type(e).__name__, str(e)[:120].replace('\\n',' '))\n")
        rc, out = run([gpu_py, "-c", code], timeout=120)
        ok = "@@OK" in out
        add("필수", "pyannote 약관 동의·접근 (--net)", "OK" if ok else "FAIL", "접근 가능" if ok else (re.search(r"@@FAIL.*", out).group(0) if re.search(r"@@FAIL.*", out) else out.strip()[-120:]),
            "" if ok else "https://huggingface.co/pyannote/speaker-diarization-community-1 에서 약관에 동의했는지, 토큰이 Read 권한인지 확인")

    free = shutil.disk_usage(HERE).free / 1e9
    add("권장", "디스크 여유", "OK" if free >= 30 else "WARN", f"{free:.0f}GB", "" if free >= 30 else "모델 캐시 10GB+, 영상 1시간당 작업파일 약 1GB. 30GB 이상 권장")

    fails = [r for r in rows if r[2] == "FAIL" and r[0] == "필수"]
    if a.json:
        print(json.dumps([dict(zip(["level", "name", "status", "detail", "fix"], r)) for r in rows], ensure_ascii=False, indent=1))
    else:
        tag = {"OK": "[ OK ]", "WARN": "[주의]", "FAIL": "[없음]"}
        for lv, name, st, detail, fix in rows:
            print(f"{tag[st]} {lv:<3} {name} — {detail}")
            if fix and st != "OK":
                print(f"          → 해결: {fix}")
        print()
        print("결과:", "필수 항목 모두 통과" if not fails else f"필수 항목 {len(fails)}개 부족 → setup.ps1 을 실행하세요")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
