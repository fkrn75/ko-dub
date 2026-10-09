#!/usr/bin/env python3
# ko-dub 통합 명령줄 도구 — 표준 라이브러리만 사용, 셸(bash/PowerShell/cmd)에 상관없이 같은 명령으로 동작한다.
# Claude Code·Codex·Antigravity 등 어느 에이전트에서든 `py -3 kodub.py <명령> ...` 만 알면 전 과정을 돌릴 수 있게 만든 것.
#
#   check [--net]                          환경 점검(없는 것·해결법 표)
#   info <URL> [--height 720]              유튜브 제목·길이·예상 용량만 조회(다운로드 없음)
#   download <URL> --out <폴더> --name <파일명> --work <작업폴더> [--height 720] [--detach]
#   probe <영상파일>                        스트림·길이·오디오 유무 요약
#   step1 <입력> --work <작업폴더> [--prompt "용어 힌트"] [--detach]     전사+화자분리
#   speakers <작업폴더> [--female-f0 150] [--formant SPEAKER_01,...]      화자별 F0·성별 판정표(+F1 보조)
#   translate <작업폴더> [--engine auto|google|nllb] [--detach]           영→한 번역
#   scan <작업폴더> [--apply fixes.json]   번역 반복 오류 검사/교정
#   dry <작업폴더> [--female-f0 N]          발음 점검(남은 영문 토큰·화자→음성 배정)
#   synth <작업폴더> [--female-f0 N] [--detach]                           음성 합성+타임라인 조립
#   mux --video V --work W --out O [--orig-audio A] [--bg-vol 0.3] [--detach]   한국어 더빙 mp4 만들기
#   verify <작업폴더> [--n 12] [--ids 5,120]                              되받아쓰기 유사도 검증
#   glossary add "Isaac Sim=아이작 심" ...  용어집 추가 (--fix 면 번역 교정 사전) / glossary show [--grep 단어]
#   report <작업폴더>                       최종 보고용 통계(밀림·배속·음성 배정)
#   status <작업폴더>                       --detach 로 시작한 작업의 진행/완료/실패
#   wait <작업폴더> <작업이름> [--timeout 540]                            끝날 때까지 기다림(시간 안에 안 끝나면 종료코드 3, 다시 호출)
#
# 오래 걸리는 단계(step1/translate/synth/mux/download)는 --detach 로 시작하고 status/wait 로 확인한다.
# 에이전트의 명령 실행 제한 시간(보통 수 분~10분)을 넘기는 작업을 안전하게 돌리기 위한 것이다.
import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
for _s in (sys.stdout, sys.stderr):  # 파이프로 읽는 에이전트가 한글을 UTF-8 로 받도록
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import check_env  # noqa: E402

NOISE = re.compile(r"torchcodec|libtorchcodec|Traceback \(most recent|FileNotFoundError: Could not find module|FFmpeg|for each of those versions|fix torchcodec|warnings\.warn|TF32|ReproducibilityWarning|It can be re-enabled|import torch|>>> torch|See https://github.com/pyannote|^\s*\^+\s*$|^\s*File \"|^\s+return |^\s+raise |^\s+\w+ = ")
SUCCESS_MARK = {"step1": "모든 단계 완료", "translate": "units.json 저장 완료", "synth": "완료: 최대 밀림"}


def die(msg, code=2):
    print("[오류] " + msg, file=sys.stderr)
    sys.exit(code)


def interp(role):
    p = check_env.interpreter(role)
    if not p or not Path(p).exists():
        die(f"'{role}' 파이썬을 찾을 수 없습니다. 먼저 `check` 로 점검하고 setup.ps1 을 실행하세요.")
    return p


def base_env(extra=None):
    e = dict(os.environ)
    e["PYTHONIOENCODING"] = "utf-8"
    e["PYTHONUNBUFFERED"] = "1"
    for k, v in (extra or {}).items():
        if v is not None:
            e[k] = str(v)
    return e


def clean_tail(path, n=3, size=12000):
    """로그 끝에서 잡음(torchcodec 경고 등)을 뺀 마지막 n줄."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            end = f.tell()
            f.seek(max(0, end - size))
            txt = f.read().decode("utf-8", errors="replace")
    except Exception:
        return []
    lines = [l.rstrip() for l in txt.splitlines() if l.strip() and not NOISE.search(l)]
    return lines[-n:]


def pid_alive(pid):
    if os.name == "nt":
        import ctypes

        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, int(pid))
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


# ── 작업(job) 실행: 포그라운드 또는 분리 실행 ─────────────────────────────
def job_file(work, name):
    return Path(work) / f"job_{name}.json"


def log_file(work, name):
    return Path(work) / f"{name}.log"


def launch(name, cmd, work, env=None, detach=False, cwd=None):
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    jf, lf = job_file(work, name), log_file(work, name)
    job = {"name": name, "cmd": cmd, "env": env or {}, "log": str(lf), "cwd": str(cwd or HERE), "started": time.time(), "marker": SUCCESS_MARK.get(name)}
    if detach:
        jf.write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
        kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "close_fds": True}
        if os.name == "nt":
            kw["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED_PROCESS | NEW_PROCESS_GROUP | NO_WINDOW
        else:
            kw["start_new_session"] = True
        p = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "_runjob", str(jf)], **kw)
        job["pid"] = p.pid
        jf.write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[시작] {name} (pid {p.pid}) — 로그: {lf}")
        print(f"       진행 확인: py -3 kodub.py status \"{work}\"   |   기다리기: py -3 kodub.py wait \"{work}\" {name}")
        return 0
    jf.write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
    rc = run_job(job, stream=True)
    job["exit_code"] = rc
    job["ended"] = time.time()
    jf.write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
    return rc


def run_job(job, stream=False):
    env = base_env(job.get("env"))
    with open(job["log"], "wb") as lf:
        p = subprocess.Popen(job["cmd"], cwd=job["cwd"], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for raw in iter(p.stdout.readline, b""):
            lf.write(raw)
            lf.flush()
            if stream:
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line.strip() and not NOISE.search(line):
                    print(line, flush=True)
        return p.wait()


def cmd_runjob(a):  # 내부용: 분리 실행된 작업을 감싸 종료코드를 기록한다
    jf = Path(a.jobfile)
    job = json.loads(jf.read_text(encoding="utf-8"))
    try:
        rc = run_job(job)
    except Exception as e:  # noqa: BLE001
        with open(job["log"], "ab") as lf:
            lf.write(f"\n[kodub] 실행 실패: {e}\n".encode("utf-8"))
        rc = 99
    job = json.loads(jf.read_text(encoding="utf-8"))
    job["exit_code"] = rc
    job["ended"] = time.time()
    jf.write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


def job_state(jf):
    job = json.loads(Path(jf).read_text(encoding="utf-8"))
    name = job["name"]
    tail = clean_tail(job["log"], 2)
    text = ""
    try:
        text = Path(job["log"]).read_text(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if "exit_code" in job:
        marker = job.get("marker")
        ok = job["exit_code"] == 0 and (not marker or marker in text)
        state = "DONE" if ok else "FAILED"
    elif job.get("pid") and pid_alive(job["pid"]):
        state = "RUNNING"
    else:
        state = "ABORTED"  # 프로세스가 사라졌는데 종료 기록이 없다(PC 재시작 등)
    el = (job.get("ended") or time.time()) - job["started"]
    return {"name": name, "state": state, "elapsed_s": round(el), "exit_code": job.get("exit_code"), "log": job["log"], "last": tail}


def cmd_status(a):
    work = Path(a.work)
    jfs = sorted(work.glob("job_*.json"))
    if not jfs:
        print("(분리 실행된 작업 없음 — 포그라운드로 돌렸거나 아직 시작 전)")
        return 0
    rows = [job_state(j) for j in jfs]
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    for r in rows:
        m, s = divmod(r["elapsed_s"], 60)
        print(f"[{r['state']:<7}] {r['name']:<10} {m}분 {s}초  exit={r['exit_code']}")
        for l in r["last"]:
            print("            " + l[:160])
    return 0


def cmd_wait(a):
    jf = job_file(a.work, a.name)
    if not jf.exists():
        die(f"작업 기록이 없습니다: {jf}")
    t0 = time.time()
    last = None
    while time.time() - t0 < a.timeout:
        r = job_state(jf)
        if r["state"] != "RUNNING":
            print(f"[{r['state']}] {r['name']} exit={r['exit_code']}  ({r['elapsed_s']}초)")
            for l in r["last"]:
                print("   " + l[:160])
            return 0 if r["state"] == "DONE" else 1
        cur = r["last"][-1] if r["last"] else ""
        if cur != last:
            print(f"[진행] {r['name']} {r['elapsed_s']}s  {cur[:140]}", flush=True)
            last = cur
        time.sleep(15)
    print(f"[계속 실행 중] {a.name} — 제한 시간({a.timeout}초) 안에 끝나지 않았습니다. 같은 wait 명령을 다시 실행하세요.")
    return 3


# ── 개별 명령 ────────────────────────────────────────────────────────────
def cmd_check(a):
    cmd = [sys.executable, str(HERE / "check_env.py")] + (["--net"] if a.net else [])
    return subprocess.call(cmd, env=base_env())


def yt():
    c = check_env.ytdlp_command()
    if not c:
        die("yt-dlp 를 찾을 수 없습니다. setup.ps1 로 설치하거나 `py -3 -m pip install yt-dlp`")
    return c


def cmd_info(a):
    fmt = f"bv*[height<={a.height}]+ba/b[height<={a.height}]"
    p = subprocess.run(yt() + ["-f", fmt, "--no-playlist", "--print", "%(title)s | %(duration_string)s | %(channel)s | 예상용량 %(filesize_approx)s B | %(resolution)s", a.url], capture_output=True, text=True, encoding="utf-8", errors="replace", env=base_env())
    out = [l for l in p.stdout.splitlines() if l.strip()]
    print("\n".join(out) if out else (p.stderr.strip()[-400:]))
    return p.returncode


def cmd_download(a):
    h = a.height
    fmt = f"bv*[height<={h}][vcodec^=avc1]+ba[ext=m4a]/bv*[height<={h}]+ba/b[height<={h}]"
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cmd = yt() + ["--no-playlist", "-f", fmt, "--merge-output-format", "mp4", "-o", str(out / (a.name + ".%(ext)s")), a.url]
    return launch("download", cmd, a.work, detach=a.detach)


def ffprobe_json(path):
    ff = check_env.find_exe("ffprobe")
    if not ff:
        die("ffprobe 를 찾을 수 없습니다(ffmpeg 설치 필요).")
    p = subprocess.run([ff, "-v", "error", "-show_entries", "format=duration:stream=index,codec_type,codec_name,width,height,channels", "-of", "json", str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        die(f"ffprobe 실패: {p.stderr.strip()[:200]}")
    return json.loads(p.stdout)


def cmd_probe(a):
    j = ffprobe_json(a.file)
    dur = float(j.get("format", {}).get("duration", 0))
    v = [s for s in j["streams"] if s["codec_type"] == "video"]
    au = [s for s in j["streams"] if s["codec_type"] == "audio"]
    print(f"길이 {dur:.1f}초 ({dur / 60:.1f}분)")
    for s in v:
        print(f"영상: {s.get('codec_name')} {s.get('width')}x{s.get('height')}")
    for s in au:
        print(f"오디오: {s.get('codec_name')} {s.get('channels')}ch")
    if v and not au:
        print("※ 소리가 없는 영상입니다 → 별도 오디오 파일이 있으면 mux 에 --orig-audio 로 주세요.")
    if au and not v:
        print("※ 오디오 파일입니다(영상 없음).")
    return 0


def cmd_step1(a):
    env = {"WHISPER_PROMPT": a.prompt} if a.prompt else {}
    cmd = [interp("gpu"), "-u", str(HERE / "step1_asr_diarize.py"), str(a.input), str(Path(a.work))]
    return launch("step1", cmd, a.work, env=env, detach=a.detach)


def cmd_speakers(a):
    work = Path(a.work)
    sf = work / "speaker_stats.json"
    if not sf.exists():
        die("speaker_stats.json 이 없습니다(step1 이 끝났는지 `status` 로 확인).")
    st = json.loads(sf.read_text(encoding="utf-8"))
    th = a.female_f0
    print(f"기준 F0 {th:.0f}Hz 이상=여성 (경계 135~170Hz는 불확실)")
    print(f"{'화자':<12}{'발화(초)':>9}{'F0(Hz)':>9}  판정")
    for spk in sorted(st, key=lambda s: -st[s]["seconds"]):
        f0 = st[spk]["f0_median"]
        g = "여성" if f0 >= th else "남성"
        flag = "  ← 경계: 불확실(--formant 로 F1 확인)" if 135 <= f0 <= 170 else ""
        print(f"{spk:<12}{st[spk]['seconds']:>9.0f}{f0:>9.0f}  {g}{flag}")
    if a.formant:
        spks = [s.strip() for s in a.formant.split(",") if s.strip()]
        arg = json.dumps({str(work.resolve()).replace("\\", "/"): spks})
        p = subprocess.run([interp("gpu"), "-u", str(HERE / "gender_formant.py"), arg], capture_output=True, text=True, encoding="utf-8", errors="replace", env=base_env())
        for l in p.stdout.splitlines():
            if l.startswith("work"):
                print(l)
        print("해석: F1_med 남성 약 410~530, 여성 약 510~610 (보조 근거일 뿐, 노이즈 큼)")
    return 0


def google_ok():
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kw):
            return None

    try:
        data = urllib.parse.urlencode({"client": "gtx", "sl": "en", "tl": "ko", "dt": "t", "q": "hello"}).encode()
        req = urllib.request.Request("https://translate.googleapis.com/translate_a/single", data=data, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.build_opener(NoRedirect).open(req, timeout=20) as r:
            j = json.loads(r.read().decode("utf-8"))
            return bool(j[0][0][0])
    except Exception:
        return False


def cmd_translate(a):
    engine = a.engine
    if engine == "auto":
        engine = "google" if google_ok() else "nllb"
        print(f"[번역 엔진] auto → {engine}" + (" (구글이 막혀 있어 로컬 NLLB 사용; 첫 사용이면 모델 약 5GB 자동 다운로드)" if engine == "nllb" else ""))
    env = {"TRANSLATOR": "nllb"} if engine == "nllb" else {}
    cmd = [interp("gpu"), "-u", str(HERE / "step2_units_translate.py"), str(Path(a.work))]
    return launch("translate", cmd, a.work, env=env, detach=a.detach)


def cmd_scan(a):
    cmd = [sys.executable, str(HERE / "scan_repeat.py"), str(Path(a.work))] + (["--apply", a.apply] if a.apply else [])
    return subprocess.call(cmd, env=base_env())


def synth_env(a):
    return {"FEMALE_F0": a.female_f0} if a.female_f0 else {}


def cmd_dry(a):
    env = base_env(dict(synth_env(a), DRY="1"))
    p = subprocess.run([interp("tts"), "-u", str(HERE / "step3_tts_mix.py"), str(Path(a.work))], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, cwd=str(HERE))
    out = [l for l in (p.stdout + p.stderr).splitlines() if l.startswith(("화자→음성", "남은 영문 토큰", "[오류]", "Traceback")) or "Error" in l]
    print("\n".join(out) if out else (p.stdout + p.stderr)[-600:])
    return p.returncode


def cmd_synth(a):
    cmd = [interp("tts"), "-u", str(HERE / "step3_tts_mix.py"), str(Path(a.work))]
    return launch("synth", cmd, a.work, env=synth_env(a), detach=a.detach)


WM_TEXT = "개인 학습용 · 재배포 금지 / Personal study use only"
WM_FONTS = [  # 한글이 나오는 글꼴 후보(운영체제별)
    "C:/Windows/Fonts/malgun.ttf", "C:/Windows/Fonts/malgunsl.ttf",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc", "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf", "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
]


def wm_escape(s):
    """drawtext text= 값에 안전하도록 특수문자를 제거/치환한다(\\ ' % 제거, : 은 ' -' 로)."""
    return s.replace("\\", "").replace("'", "").replace("%", "").replace(":", " -").replace(",", "，")


def wm_filter(ff, a, duration):
    """워터마크 drawtext 필터 문자열. 시작 직후 한 번, 이후 a.wm_every 초마다 a.wm_dur 초씩 우상단에 반투명으로 띄운다."""
    filters = subprocess.run([ff, "-hide_banner", "-filters"], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    if " drawtext " not in filters:
        die("이 ffmpeg 에는 drawtext 필터(libfreetype)가 없습니다. 전체 빌드 ffmpeg 를 쓰거나 --no-watermark 로 워터마크 없이 합치세요.")
    font = a.wm_font or next((f for f in WM_FONTS if Path(f).exists()), None)
    if not font or not Path(font).exists():
        die("한글 글꼴을 찾지 못했습니다. --wm-font <글꼴파일.ttf> 를 지정하거나 --no-watermark 를 쓰세요.")
    start = min(20.0, duration * 0.2)  # 짧은 영상에서도 한 번은 보이게
    en = f"gte(t,{start:.1f})*lt(mod(t-{start:.1f},{a.wm_every}),{a.wm_dur})"
    font_arg = font.replace("\\", "/").replace(":", "\\:")
    return (f"drawtext=fontfile='{font_arg}':text='{wm_escape(a.watermark)}':fontsize=h/34:fontcolor=white@0.55:"
            f"shadowcolor=black@0.5:shadowx=1:shadowy=1:x=w-tw-w/40:y=h/25:enable='{en}'")


def pick_encoder(ff):
    """워터마크는 영상을 다시 인코딩해야 한다. NVENC 가 실제로 되면 쓰고, 아니면 libx264."""
    enc = subprocess.run([ff, "-hide_banner", "-encoders"], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    if "h264_nvenc" in enc:
        t = subprocess.run([ff, "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=256x144:d=0.2", "-c:v", "h264_nvenc", "-f", "null", "-"], capture_output=True)
        if t.returncode == 0:
            return ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "21", "-b:v", "0", "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p"]


def cmd_mux(a):
    ff = check_env.find_exe("ffmpeg")
    if not ff:
        die("ffmpeg 를 찾을 수 없습니다.")
    work, out = Path(a.work), Path(a.out)
    dub = work / "dub_ko.wav"
    if not dub.exists():
        die("dub_ko.wav 가 없습니다(synth 가 끝났는지 `status` 로 확인).")
    orig = 2 if a.orig_audio else 0
    flt = (f"[{orig}:a]aresample=44100,volume={a.bg_vol}[o];[1:a]aresample=44100,asplit=2[d1][d2];"
           "[o][d1]sidechaincompress=threshold=0.02:ratio=14:attack=15:release=600[duck];"
           "[duck][d2]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11[mix]")
    tmp = out.with_name(out.stem + ".kodub_tmp.mp4")
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", str(a.video), "-i", str(dub)]
    if a.orig_audio:
        cmd += ["-i", str(a.orig_audio)]
    if a.no_watermark:
        vmap, vcodec = "0:v:0", ["-c:v", "copy"]  # 영상은 그대로 복사(빠름)
    else:
        dur = float(ffprobe_json(a.video)["format"]["duration"])
        flt = f"[0:v]{wm_filter(ff, a, dur)}[v];" + flt
        vmap, vcodec = "[v]", pick_encoder(ff)
        print(f"[워터마크] '{a.watermark}' — {a.wm_every}초마다 {a.wm_dur}초씩 우상단 표시, 영상 재인코딩({vcodec[1]})")
    cmd += ["-filter_complex", flt, "-map", vmap, "-map", "[mix]", "-map", f"{orig}:a:0"] + vcodec + ["-c:a:0", "aac", "-b:a:0", "192k"]
    cmd += ["-c:a:1", "aac", "-b:a:1", "128k"] if a.orig_audio else ["-c:a:1", "copy"]
    cmd += ["-metadata:s:a:0", "language=kor", "-metadata:s:a:1", "language=eng", "-disposition:a:0", "default", "-disposition:a:1", "0", "-movflags", "+faststart", str(tmp)]
    if a.detach:
        # 분리 실행: 임시 파일에 만든 뒤 검증·교체까지 하는 후속 명령을 같은 작업에서 이어 실행
        wrapper = [sys.executable, str(Path(__file__).resolve()), "_finish_mux", str(tmp), str(out), str(a.video), json.dumps(cmd)]
        return launch("mux", wrapper, a.work, detach=True)
    return finish_mux(cmd, tmp, out, a.video)


def finish_mux(cmd, tmp, out, video):
    rc = subprocess.call(cmd)
    if rc != 0:
        print(f"[오류] ffmpeg 실패(exit {rc})")
        return rc
    j, src = ffprobe_json(tmp), ffprobe_json(video)
    n_v = sum(s["codec_type"] == "video" for s in j["streams"])
    n_a = sum(s["codec_type"] == "audio" for s in j["streams"])
    d1, d0 = float(j["format"]["duration"]), float(src["format"]["duration"])
    if n_v != 1 or n_a != 2 or abs(d1 - d0) > 2.0:
        print(f"[오류] 결과 검증 실패: 영상 {n_v}, 오디오 {n_a}, 길이 {d1:.1f}s (원본 {d0:.1f}s). 임시 파일 유지: {tmp}")
        return 1
    os.replace(tmp, out)  # 검증 통과 후에만 교체(기존 파일 보호)
    print(f"[완료] {out}  ({out.stat().st_size / 1e6:.1f} MB, 영상 1 + 오디오 2, 길이 {d1:.1f}s)")
    return 0


def cmd_finish_mux(a):
    return finish_mux(json.loads(a.cmdjson), Path(a.tmp), Path(a.out), a.video)


def cmd_verify(a):
    cmd = [interp("gpu"), "-u", str(HERE / "verify_ko.py"), str(Path(a.work)), "--n", str(a.n)] + (["--ids", a.ids] if a.ids else [])
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=base_env())
    for l in (p.stdout + p.stderr).splitlines():
        if l.strip() and not NOISE.search(l) and not re.search(r"warn|dll|Traceback", l, re.I):
            print(l)
    return p.returncode


def cmd_report(a):
    work = Path(a.work)
    s = json.loads((work / "schedule.json").read_text(encoding="utf-8"))
    d = [x["start"] - x["orig_start"] for x in s]
    print(f"문장 {len(s)}개 | 밀림 최대 {max(d):.1f}s, 평균 {sum(d) / len(d):.2f}s | 밀림>5s {sum(v > 5 for v in d)}개, >3s {sum(v > 3 for v in d)}개 | 배속 1.2 초과 {sum(x['speed'] > 1.2 for x in s)}개 ({100 * sum(x['speed'] > 1.2 for x in s) / len(s):.0f}%), 상한 1.4 이상 {sum(x['speed'] >= 1.4 for x in s)}개")
    if (work / "voices.json").exists() and (work / "speaker_stats.json").exists():
        v = json.loads((work / "voices.json").read_text(encoding="utf-8"))
        st = json.loads((work / "speaker_stats.json").read_text(encoding="utf-8"))
        print("화자→음성: " + ", ".join(f"{k}→{v[k]}({st[k]['f0_median']:.0f}Hz)" for k in sorted(v, key=lambda k: -st.get(k, {}).get('seconds', 0)) if k in st))
    if max(d) > 10:
        print("※ 최대 밀림이 10초를 넘습니다 → `scan` 으로 번역 반복 오류를 확인하세요.")
    return 0


def cmd_glossary(a):
    """용어집(glossary.json: 영문→한글 발음) / 번역 교정(ko_fixes.json: 오역→바른 표현) 항목 추가·조회. JSON 을 직접 고칠 필요가 없다."""
    path = HERE / ("ko_fixes.json" if a.fix else "glossary.json")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if a.action == "show":
        for k, v in data.items():
            if not a.grep or a.grep.lower() in (k + v).lower():
                print(f"{k} = {v}")
        print(f"(총 {len(data)}개)")
        return 0
    n = 0
    for item in a.items:
        if "=" not in item:
            print(f"[건너뜀] '키=값' 형식이 아님: {item}")
            continue
        k, v = item.split("=", 1)
        k, v = k.strip(), v.strip()
        if not k or not v:
            continue
        old = data.get(k)
        data[k] = v
        n += 1
        print(f"{'수정' if old is not None and old != v else '추가'}: {k} = {v}" + (f"  (이전: {old})" if old is not None and old != v else ""))
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")  # BOM 없는 UTF-8
    print(f"{n}개 반영 → {path.name} 총 {len(data)}개")
    return 0


def main():
    ap = argparse.ArgumentParser(description="ko-dub 통합 CLI")
    sp = ap.add_subparsers(dest="cmd", required=True)

    def P(name, fn, **kw):
        p = sp.add_parser(name, **kw)
        p.set_defaults(fn=fn)
        return p

    P("check", cmd_check).add_argument("--net", action="store_true")
    p = P("info", cmd_info); p.add_argument("url"); p.add_argument("--height", type=int, default=720)
    p = P("download", cmd_download); p.add_argument("url"); p.add_argument("--out", required=True); p.add_argument("--name", required=True); p.add_argument("--work", required=True); p.add_argument("--height", type=int, default=720); p.add_argument("--detach", action="store_true")
    p = P("probe", cmd_probe); p.add_argument("file")
    p = P("step1", cmd_step1); p.add_argument("input"); p.add_argument("--work", required=True); p.add_argument("--prompt"); p.add_argument("--detach", action="store_true")
    p = P("speakers", cmd_speakers); p.add_argument("work"); p.add_argument("--female-f0", type=float, default=150.0); p.add_argument("--formant")
    p = P("translate", cmd_translate); p.add_argument("work"); p.add_argument("--engine", choices=["auto", "google", "nllb"], default="auto"); p.add_argument("--detach", action="store_true")
    p = P("scan", cmd_scan); p.add_argument("work"); p.add_argument("--apply")
    p = P("dry", cmd_dry); p.add_argument("work"); p.add_argument("--female-f0")
    p = P("synth", cmd_synth); p.add_argument("work"); p.add_argument("--female-f0"); p.add_argument("--detach", action="store_true")
    p = P("mux", cmd_mux); p.add_argument("--video", required=True); p.add_argument("--work", required=True); p.add_argument("--out", required=True); p.add_argument("--orig-audio"); p.add_argument("--bg-vol", type=float, default=0.30); p.add_argument("--detach", action="store_true"); p.add_argument("--watermark", default=WM_TEXT, help="워터마크 문구"); p.add_argument("--no-watermark", action="store_true", help="워터마크 없이 영상 복사(빠름)"); p.add_argument("--wm-every", type=int, default=300, help="표시 주기(초)"); p.add_argument("--wm-dur", type=int, default=8, help="1회 표시 길이(초)"); p.add_argument("--wm-font")
    p = P("verify", cmd_verify); p.add_argument("work"); p.add_argument("--n", type=int, default=12); p.add_argument("--ids")
    p = P("glossary", cmd_glossary); p.add_argument("action", choices=["add", "show"]); p.add_argument("items", nargs="*", help="add 일 때 '영문=한글 발음' 또는(--fix) '오역=바른 표현'"); p.add_argument("--fix", action="store_true", help="번역 교정 사전(ko_fixes.json) 대상"); p.add_argument("--grep")
    p = P("report", cmd_report); p.add_argument("work")
    p = P("status", cmd_status); p.add_argument("work"); p.add_argument("--json", action="store_true")
    p = P("wait", cmd_wait); p.add_argument("work"); p.add_argument("name"); p.add_argument("--timeout", type=int, default=540)
    p = P("_runjob", cmd_runjob); p.add_argument("jobfile")
    p = P("_finish_mux", cmd_finish_mux); p.add_argument("tmp"); p.add_argument("out"); p.add_argument("video"); p.add_argument("cmdjson")
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
