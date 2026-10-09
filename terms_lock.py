# 전문용어 고정(번역 전) — 표준 라이브러리만 사용.
# 원리: 번역 전에 영어 원문의 전문용어를 대문자 표식(TQA, TQB…)으로 바꾸고, 번역 후 표식을 terms.json 의 한국어 용어로 되돌린다.
#   번역기(NLLB·구글)는 대문자 약어를 거의 그대로 둔다(MegaLights 60문장 실험: 표식 보존 63/63,
#   반면 원문에 한국어를 직접 넣는 방식은 33/63 — "텍스트처", "레이 트레싱"처럼 망가졌다).
#   표식 뒤에 번역기가 붙인 조사(을/를 등)는 표식 발음 기준이라, 복원 후 실제 용어의 받침에 맞게 고친다.
import json
import re
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
TERMS_PATH = HERE / "terms.json"
CODES = [f"TQ{c}" for c in "ABCDEFGHJKLMNPRSUVWXYZ"]  # 문장 하나에 서로 다른 용어 최대 22개(I·O·Q·T 제외: 숫자·단어와 헷갈림 방지)
CODE_RE = re.compile(r"TQ[A-Z]")


def load_terms(path=TERMS_PATH):
    if not Path(path).exists():
        return {}
    d = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    return {k: v for k, v in d.items() if not k.startswith("_")}


def term_regex(key):
    """'shadow map' → 대소문자 무시, 단어 사이 공백/하이픈 허용, 마지막 단어 복수형(s/es) 허용, 영문 경계."""
    words = key.strip().split()
    body = r"[\s-]?".join(re.escape(w) for w in words)
    return re.compile(r"(?<![A-Za-z0-9])" + body + r"(?:s|es)?(?![A-Za-z0-9])", re.I)


def compile_terms(terms):
    # 긴 키부터 바꿔야 'virtual shadow map' 이 'shadow map' 보다 먼저 잡힌다
    return [(k, term_regex(k), v) for k, v in sorted(terms.items(), key=lambda kv: -len(kv[0]))]


def lock(text, compiled):
    """영어 문장의 용어를 표식으로 바꾼다. 반환: (바뀐 문장, {표식: 한국어 용어})"""
    mapping, by_val = {}, {}
    for _, rx, v in compiled:
        if len(mapping) >= len(CODES):
            break
        if not rx.search(text):
            continue
        code = by_val.get(v)
        if code is None:
            code = CODES[len(mapping)]
            mapping[code], by_val[v] = v, code
        text = rx.sub(code, text)
    return text, mapping


def _has_batchim(ch):
    o = ord(ch) - 0xAC00
    return 0 <= o <= 11171 and o % 28 != 0


def _is_rieul(ch):
    o = ord(ch) - 0xAC00
    return 0 <= o <= 11171 and o % 28 == 8


JOSA = re.compile(r"(을|를|이|가|은|는|과|와|으로|로)(?=[\s.,!?·\"')\]]|$|서|써|부터)")


def fix_josa(text, term):
    """복원한 용어 바로 뒤의 조사를 용어 끝 글자 받침에 맞춘다(이/가·은/는 은 뒤가 띄어쓰기·문장부호일 때만 = 서술격 '이다' 오인 방지)."""
    last = term.rstrip()[-1:]
    if not last or not ("가" <= last <= "힣"):
        return text
    bat, rieul = _has_batchim(last), _is_rieul(last)

    def rep(m):
        j = m.group(1)
        if j in ("을", "를"):
            return "을" if bat else "를"
        if j in ("이", "가"):
            return "이" if bat else "가"
        if j in ("은", "는"):
            return "은" if bat else "는"
        if j in ("과", "와"):
            return "과" if bat else "와"
        return "으로" if (bat and not rieul) else "로"

    out, i = [], 0
    for m in re.finditer(re.escape(term), text):
        out.append(text[i:m.end()])
        rest = text[m.end():]
        jm = JOSA.match(rest)
        if jm:
            out.append(rep(jm))
            i = m.end() + jm.end(1)
        else:
            i = m.end()
    out.append(text[i:])
    return "".join(out)


def unlock(ko, mapping):
    """번역문의 표식을 한국어 용어로 되돌린다. 반환: (문장, 잃어버린 표식 목록). 표식이 사라졌으면 호출 측이 잠금 없이 다시 번역한다."""
    lost = [c for c in mapping if c not in ko]
    # 번역기가 표식을 'TQAs', 'TQA의' 처럼 붙여 쓰는 경우도 표식만 바꾼다
    ko = CODE_RE.sub(lambda m: mapping.get(m.group(0), ""), ko)
    for v in set(mapping.values()):
        ko = fix_josa(ko, v)
    ko = re.sub(r"(?<=[가-힣])s(?=[\s.,!?]|$)", "", ko)  # 'TQAs' → '용어s' 의 복수 s 제거
    return re.sub(r"\s{2,}", " ", ko).strip(), lost


# ── 후보 추출(kodub terms) ──
STOP = set("""a an the and or but if so of to in on at by for with from as is are was were be been being am do does did done have has had
this that these those it its it's they them their there here what which who whom whose when where why how i me my we us our you your he she his her
not no yes can could will would should may might must shall just very really also too then than now well okay ok yeah oh um uh like kind sort
get got go going gonna want wanna need know think see look make made use used using lot lots thing things way ways one two three some any all
each every more most much many few other another same different new good great right left up down out over into about after before because
let let's say said here's that's there's we're you're they're i'm don't can't won't isn't aren't didn't doesn't it'll we'll you'll actually
basically probably maybe pretty quite still even only again back first next last time times little bit big small sure thank thanks please
around second seconds minute minutes question questions inside example examples almost couple through information project projects better best
always quality bright screen though change changes simple together without already number numbers highlight environment result results multiple
anything something everything nothing someone everyone people person today tomorrow yesterday folks guys hello welcome stream show feature features
able again another anyway around being between both called certain doing during either enough especially exactly getting giving going happen
happens having important instead itself looking makes making might obviously often others otherwise perhaps rather really right seems should
since start started starting stuff taking talk talking tell things through trying understand until whatever whether which while within working
works world would yourself awesome amazing interesting cool nice basically definitely literally totally certainly usually generally currently
across against already although always another anybody because before behind below beside beyond toward towards upon whole whose within
""".split())


def candidates(words_text, terms, limit=120):
    """영어 원문에서 전문용어 후보(반복되는 2~3단어 명사구, 문장 중간의 대문자 고유명사, 반복되는 긴 단어)를 뽑는다. 이미 terms.json 에 있는 것은 제외."""
    toks = re.findall(r"[A-Za-z][A-Za-z0-9\-']*|[.!?]", words_text)
    low = [t.lower() for t in toks]
    known = compile_terms(terms)
    cnt, caps = Counter(), Counter()
    for i, t in enumerate(toks):
        if t in ".!?":
            continue
        if t[0].isupper() and i > 0 and toks[i - 1] not in ".!?" and low[i] not in STOP and len(t) > 1 and "'" not in t and not t.startswith("I'"):
            caps[t] += 1
        for n in (1, 2, 3):
            seg = low[i:i + n]
            if len(seg) < n or any(s in ".!?" for s in seg) or seg[0] in STOP or seg[-1] in STOP:
                continue
            if n == 1 and (len(seg[0]) < 6 or seg[0].endswith(("ly", "ing", "ed")) or "'" in seg[0]):
                continue
            if any("'" in s for s in seg):
                continue
            cnt[" ".join(seg)] += 1
    for k in [k for k in cnt if k.endswith("s") and k[:-1] in cnt]:  # 복수형은 단수형에 합친다(매칭이 복수형을 자동 처리하므로)
        cnt[k[:-1]] += cnt.pop(k)
    rows = []
    for k, c in cnt.items():
        n = len(k.split())
        if (n == 1 and c >= 4) or (n > 1 and c >= 2):
            rows.append((k, c, "구"))
    for k, c in caps.items():
        if c >= 2 and k.lower() not in cnt:
            rows.append((k, c, "고유명사"))
    out = []
    for k, c, kind in sorted(rows, key=lambda r: -r[1] * (2 if r[2] != "구" or " " in r[0] else 1)):
        if any(rx.fullmatch(k) or rx.fullmatch(k + "s") for _, rx, _ in known):
            continue
        out.append((k, c, kind))
    # 긴 구에 포함된 짧은 구가 횟수까지 같으면 짧은 쪽은 버린다
    keep = []
    for k, c, kind in out:
        if any(k != k2 and k in k2 and c == c2 for k2, c2, _ in out):
            continue
        keep.append((k, c, kind))
    return keep[:limit]
