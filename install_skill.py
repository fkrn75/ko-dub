#!/usr/bin/env python3
# ko-dub 스킬을 여러 에이전트의 스킬 폴더에 설치한다 (표준 라이브러리만 사용).
#   py -3 install_skill.py                 이 PC에서 감지된 에이전트(Claude Code, Codex, Antigravity)에 모두 설치
#   py -3 install_skill.py --list          설치 대상 폴더와 현재 상태만 보여 줌
#   py -3 install_skill.py --agents claude,codex   특정 대상만
#   py -3 install_skill.py --uninstall     설치한 복사본 제거
# 원본은 도구 폴더의 skill/ko-dub (이 폴더 하나만 고치면 되고, 고친 뒤 이 설치기를 다시 실행하면 전부 갱신된다).
# 설치되는 복사본마다 toolkit_path.txt 에 이 도구 폴더의 절대경로를 기록해, 스킬 폴더만으로 도구를 찾게 한다.
import argparse
import hashlib
import shutil
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):  # 파이프·파일로 출력을 받을 때 cp949 가 줄표(—) 등을 못 써서 죽는 것을 막는다(콘솔에서는 영향 없음)
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = Path(__file__).resolve().parent
SRC = HERE / "skill" / "ko-dub"
HOME = Path.home()

# (이름, 스킬 폴더, 에이전트가 설치돼 있다고 볼 근거 폴더, 이미 있을 때만 갱신 여부)
# 근거(2026-10-09 이 PC에서 확인): Codex 0.147 바이너리에 `CODEX_HOME/skills`·`~/.codex/skills` 문자열이 있고, 최신 공식 문서는 `$HOME/.agents/skills`.
# Antigravity 는 설치본 내장 안내문(agy-customizations)이 전역 위치를 `~/.gemini/config/`(그 아래 skills/<이름>/SKILL.md)로 명시한다.
# 같은 이름의 스킬이 여러 곳에 있어도 에이전트가 우선순위로 하나만 쓰므로 중복 설치는 무해하다.
TARGETS = [
    ("claude", HOME / ".claude" / "skills", HOME / ".claude", False),
    ("codex", HOME / ".codex" / "skills", HOME / ".codex", False),                 # Codex(이 PC 0.147이 읽는 위치)
    ("shared", HOME / ".agents" / "skills", HOME / ".agents", False),              # Codex 최신 공식 위치 + Antigravity 도 읽음(항상 만든다)
    ("antigravity", HOME / ".gemini" / "config" / "skills", HOME / ".gemini" / "config", False),  # Antigravity 전역 위치
    ("antigravity", HOME / ".gemini" / "antigravity" / "skills", HOME / ".gemini" / "antigravity", True),  # 구버전 위치: 이미 있을 때만
]


def tree_hash(root):
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.name != "toolkit_path.txt":
            h.update(str(p.relative_to(root)).encode("utf-8"))
            h.update(p.read_bytes())
    return h.hexdigest()[:12]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agents", default="all", help="claude,codex,antigravity 중 쉼표 구분 (기본 all)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    a = ap.parse_args()
    if not (SRC / "SKILL.md").exists():
        print(f"[오류] 원본 스킬이 없습니다: {SRC}")
        return 1
    want = None if a.agents == "all" else {x.strip() for x in a.agents.split(",")}
    src_hash = tree_hash(SRC)
    done = 0
    for agent, skills_dir, marker, only_existing in TARGETS:
        if want is not None and agent not in want and not (agent == "shared" and want & {"codex", "antigravity"}):
            continue
        dst = skills_dir / "ko-dub"
        # 에이전트가 설치된 흔적(marker)이 있는 곳, 또는 이미 스킬 폴더가 있는 곳만 대상. 구버전 위치는 이미 있을 때만.
        if (only_existing and not skills_dir.exists()) or (not marker.exists() and not skills_dir.exists()):
            print(f"[건너뜀] {agent:<12} {skills_dir}  ({'구버전 위치라 이미 있을 때만 갱신' if only_existing else '이 에이전트가 설치되지 않은 것으로 보임'})")
            continue
        state = "없음"
        if dst.exists():
            state = "최신" if (dst / "SKILL.md").exists() and tree_hash(dst) == src_hash else "오래됨/다름"
        if a.list:
            print(f"[{state:<6}] {agent:<12} {dst}")
            continue
        if a.uninstall:
            if dst.exists():
                shutil.rmtree(dst)
                print(f"[제거] {agent:<12} {dst}")
            continue
        skills_dir.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            shutil.rmtree(dst)  # 우리가 만든 ko-dub 폴더만 교체한다
        shutil.copytree(SRC, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "toolkit_path.txt"))
        (dst / "toolkit_path.txt").write_text(str(HERE), encoding="utf-8")
        print(f"[설치] {agent:<12} {dst}")
        done += 1
    if not a.list and not a.uninstall:
        print(f"\n{done}곳에 설치했습니다. 에이전트를 새로 시작(또는 스킬 목록 새로고침)하면 보입니다.")
        print("Codex: `$ko-dub` 또는 /skills · Antigravity: 프롬프트에서 스킬 이름을 부르거나 설명에 맞는 요청 · Claude Code: /ko-dub")
    return 0


if __name__ == "__main__":
    sys.exit(main())
