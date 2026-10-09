# 번역 반복 루프 검사/교정 도구 (표준 라이브러리만 사용 — 어느 파이썬으로도 실행 가능)
#   검사: py -3 scan_repeat.py <작업폴더>
#   교정: py -3 scan_repeat.py <작업폴더> --apply fixes.json   (fixes.json = {"320": "새 한국어 번역", ...})
# NLLB 가 같은 구절을 수백 자 반복해 합성음이 수십 초가 되는 사고(싱크 최대 46초 밀림)를 막기 위한 것.
import argparse
import json
import re
import shutil
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):  # 파이프·파일로 출력을 받을 때 cp949 가 줄표(—) 등을 못 써서 죽는 것을 막는다(콘솔에서는 영향 없음)
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 2~40자 구절이 4번 이상 연속 반복되면 의심
REPEAT = re.compile(r"(.{2,40}?)(?:[ ,.]*\1){3,}")


def suspects(units):
    out = []
    for u in units:
        ko, n_en = u["ko"], len(u["en"].split())
        why = None
        if REPEAT.search(ko):
            why = "구절 반복"
        elif n_en >= 3 and len(ko) / n_en > 9:
            why = "번역문이 비정상적으로 김"
        else:
            latin = len(re.findall(r"[A-Za-z]", ko))
            if n_en >= 4 and latin > 0.6 * max(1, len(re.sub(r"\s", "", ko))):
                why = "미번역(영문 그대로) 의심"
        if why:
            out.append((u["id"], why))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("work")
    ap.add_argument("--apply", help="id→새 번역 JSON 파일")
    a = ap.parse_args()
    path = Path(a.work) / "units.json"
    units = json.loads(path.read_text(encoding="utf-8"))

    if a.apply:
        fixes = json.loads(Path(a.apply).read_text(encoding="utf-8"))
        bak = path.with_name("units.json.bak_before_fix")
        if not bak.exists():
            shutil.copy2(path, bak)  # 교정 전 백업(한 번만)
        for k, new in fixes.items():
            u = units[int(k)]
            assert u["id"] == int(k), f"id 불일치: {k}"
            print(f"[{k}] {u['start'] / 60:.1f}분  EN: {u['en'][:70]}\n    전: {u['ko'][:50]}\n    후: {new[:50]}")
            u["ko"] = new
        path.write_text(json.dumps(units, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{len(fixes)}개 교정 완료 → step3 를 다시 실행하면 바뀐 문장만 재합성된다(tts_cache).")
        return

    bad = suspects(units)
    print(f"총 {len(units)}문장, 의심 {len(bad)}개")
    n_t, lost = sum(1 for u in units if u.get("terms")), [u["id"] for u in units if u.get("term_lost")]
    if n_t or lost:
        print(f"전문용어 고정: {n_t}문장 적용" + (f", 번역기가 표식을 지워 잠금 없이 재번역한 문장 {len(lost)}개(용어가 원래 오역대로일 수 있음): {lost[:20]}" if lost else ""))
    for i, why in bad:
        u = units[i]
        print(f"[{i}] {u['start'] / 60:.1f}분 {u['end'] - u['start']:.1f}s ko={len(u['ko'])}자 ({why})")
        print(f"    EN: {u['en'][:110]}")
        print(f"    KO: {u['ko'][:80]}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
