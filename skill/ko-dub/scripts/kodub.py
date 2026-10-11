#!/usr/bin/env python3
# ko-dub 런처 — 스킬 폴더 안에서 실제 도구 폴더의 kodub.py 를 찾아 그대로 실행한다(인자·종료코드 전달).
# 이 파일은 스킬을 어느 에이전트(Claude Code / Codex / Antigravity)의 스킬 폴더에 복사해도 같은 명령으로 동작하게 해 주는 얇은 껍데기다.
# 도구 폴더 위치 탐색 순서: ① 같은 스킬 폴더의 toolkit_path.txt(설치기가 기록)  ② 환경변수 KO_DUB_HOME  ③ 기본 위치
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT = str(Path.home() / "ko-dub")  # 저장소를 홈 폴더에 그대로 받았을 때의 기본 위치


def find_toolkit():
    cands = []
    f = HERE.parent / "toolkit_path.txt"
    if f.exists():
        cands.append(f.read_text(encoding="utf-8-sig").strip())
    if os.environ.get("KO_DUB_HOME"):
        cands.append(os.environ["KO_DUB_HOME"])
    cands.append(DEFAULT)
    for c in cands:
        if c and (Path(c) / "kodub.py").exists():
            return Path(c)
    return None


tk = find_toolkit()
if not tk:
    sys.stderr.write(
        "[오류] ko-dub 도구 폴더를 찾지 못했습니다(이 PC에는 스킬 문서만 있음).\n"
        f"  받기: git clone https://github.com/fkrn75/ko-dub \"{DEFAULT}\"   (git 이 없으면 같은 주소에서 ZIP 으로 받아 그 위치에 풀기)\n"
        "  그 뒤 setup.ps1 로 환경을 설치합니다. 다른 위치에 두었으면 환경변수 KO_DUB_HOME 을 그 폴더로 설정하세요.\n"
        "  ※ 도구가 없다고 edge-tts 등 다른 TTS 로 대신 만들지 마세요 — 화자분리·남녀 음성·Supertonic 이 빠진 결과가 됩니다.\n"
    )
    sys.exit(2)
sys.exit(subprocess.call([sys.executable, str(tk / "kodub.py")] + sys.argv[1:]))
