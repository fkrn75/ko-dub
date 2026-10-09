/**
 * 한국어 TTS 발음 개선 (FN-02 보조) · 분수·통화기호·차원(D) 매처
 *
 * speak.ts 의 toSpoken 루프 안에서 지휘자가 호출하는 "부분 매처(partial matcher)" 모음이다.
 * 각 함수는 문자열 s 와 위치 idx 를 받아, 그 자리에서 패턴이 명백히 인식되면
 * { ko, len }(발음 한국어 + 소비한 원문 길이)을, 아니면 null 을 반환한다.
 *
 * ⚠️ 시그니처는 speak.ts 의 matchUnit / speak.misc.ts 의 매처들과 동일하다(통일):
 *     match...(s: string, idx: number): { ko: string; len: number } | null
 *
 * ⚠️ speak.ts·speak.misc.ts·speak.datetime.ts 를 import 하지 않는다(순환 의존 회피).
 *    숫자 음독은 범위가 작으므로(분자/분모·통화 금액) 이 파일에 자체 최소 구현을 둔다.
 *
 * 보수적 원칙(MUST): 확신 없으면 null 을 반환해 원문을 유지한다. 잘못 읽느니 원문이 안전하다.
 *   - 분수('/'): 분수·날짜·비율·경로 모두 '/'를 쓰므로, '명백한 분수'만 잡고
 *     날짜로 보이면 null 을 반환해 날짜 매처에 양보한다(아래 matchFraction 주석 참조).
 *   - 통화기호: 기호가 숫자 "앞"에 오는 형태만(₩5000). '$'는 speak.ts 가 이미 처리하므로 제외.
 *   - 차원(D): 숫자 바로 뒤 대문자 'D' 한 글자. 뒤에 영문자가 더 있으면(3Days) null.
 */

// ─────────────────────────────────────────────────────────────
// 자체 최소 한자어 숫자 음독 (speak.ts 와 공유하지 않음 · 순환 회피)
// ─────────────────────────────────────────────────────────────

/** 한자어 숫자 0~9. */
const SINO_DIGIT = ['영', '일', '이', '삼', '사', '오', '육', '칠', '팔', '구']
/** 4자리 내 자리값 (일/십/백/천). */
const SINO_SMALL_UNIT = ['', '십', '백', '천']
/** 4자리 묶음 단위 (만/억). 통화 금액은 보수적으로 억까지만(그 이상은 변환 포기). */
const SINO_BIG_UNIT = ['', '만', '억']

/**
 * 0~9999 한자어 읽기. 자리값 숫자 1은 생략(일십→십, 일백→백, 일천→천).
 * 예: 1->일, 10->십, 15->십오, 22->이십이, 5000->오천
 */
function sinoUnder10000(n: number): string {
  if (n === 0) return ''
  let out = ''
  const digits = String(n).split('').map(Number)
  const len = digits.length
  for (let i = 0; i < len; i++) {
    const d = digits[i]
    if (d === 0) continue
    const pos = len - 1 - i // 0=일,1=십,2=백,3=천
    if (d === 1 && pos > 0) {
      out += SINO_SMALL_UNIT[pos]
    } else {
      out += SINO_DIGIT[d] + SINO_SMALL_UNIT[pos]
    }
  }
  return out
}

/**
 * 음이 아닌 정수(작은 범위)를 한자어로. 분자/분모·통화 금액 읽기 공용.
 * 4자리씩 끊어 만/억 단위를 붙인다. 억(8자리)을 넘으면 보수적으로 null.
 * @returns 한자어 문자열, 또는 너무 큰 수면 null
 */
function sinoInteger(n: number): string | null {
  if (n === 0) return '영'
  if (n > 99999999) return null // 억(8자리) 초과는 변환 포기(보수적)
  const groups: number[] = []
  let rest = n
  while (rest > 0) {
    groups.push(rest % 10000)
    rest = Math.floor(rest / 10000)
  }
  let out = ''
  for (let g = groups.length - 1; g >= 0; g--) {
    const val = groups[g]
    if (val === 0) continue
    out += sinoUnder10000(val) + SINO_BIG_UNIT[g]
  }
  return out === '' ? '영' : out
}

// ─────────────────────────────────────────────────────────────
// 분수  —  1/2 -> "이분의 일", 3/4 -> "사분의 삼"
// ─────────────────────────────────────────────────────────────

/**
 * 분수를 idx 자리에서 인식하면 { ko, len }, 아니면 null.
 *
 * 한국어 분수 = 분모(아래) 먼저 + "분의" + 분자(위). 둘 다 한자어 숫자.
 *   1/2 -> "이분의 일", 3/4 -> "사분의 삼", 2/3 -> "삼분의 이"
 *
 * ⚠️ 날짜(6/22)와의 충돌 — 핵심:
 *   '/'는 분수·날짜(M/D)·비율·경로에 모두 쓰인다. 사용자 1순위는 1/2·3/4·2/3 가
 *   분수로 읽히는 것이고, 6/22 같은 건 날짜로 읽히는 게 자연스럽다.
 *   그래서 이 매처는 "명백한 단순분수"만 잡고, '날짜로도 보이는' 모호한 조합은
 *   null 을 반환해 날짜 매처(우선순위 상)에게 양보한다.
 *
 *   ⚠️ 위치 주의: '/' 앞 숫자 = 분자(분수)이자 '월'(날짜 M/D), '/' 뒤 숫자 = 분모이자 '일'.
 *      따라서 "numer=월후보(1~12), denom=일후보(1~31)" 이면 유효한 날짜로도 읽힌다.
 *
 *   인식(분수로 확정)하는 조건 — 아래 중 하나라도 충족(둘 다 아니면 null):
 *     (A) 흔한 단순분수: 분모 2~12 AND 분자 < 분모 AND 분자>=1.
 *         (1/2,3/4,2/3,5/6,7/12 … 일상에서 분수로 읽는 전형. 1/2 는 1월2일로도 읽히지만
 *          사용자 1순위가 '이분의 일'이므로 이 좁은 범위에선 분수를 택한다.)
 *     (B) 날짜로 불가능: 분자(월후보) > 12  OR  분모(일후보) > 31.
 *         (월/일 범위를 벗어나 'M/D 날짜'가 절대 될 수 없으므로 안전하게 분수.
 *          예: 13/4(월13 불가)·3/40(일40 불가)·5/32 …)
 *
 *   그 외 — 특히 분모가 13~31 이고 분자가 1~12 인 조합(6/22, 5/30, 1/16 …)은
 *   "유효한 날짜로도 보이는 모호 구간"이므로 (A)·(B) 어디에도 안 들어가 null 을 반환,
 *   날짜 매처(우선순위 상)에게 양보한다. → 6/22 는 날짜로 읽힌다. ✅ 핵심 요구사항.
 *
 * 경계(보수):
 *   - 시작 직전 문자가 숫자/'.'/'/'면 더 큰 수·다른 패턴의 일부 → 포기.
 *   - 분자/분모 뒤에 숫자·'/'·'.'가 더 붙으면(6/22/99, 1/2.5, 1/2/3) 단순분수가 아님 → 포기.
 *   - 분모 0(0 나눗셈)·앞자리 0 패딩(01/2)도 비전형이라 포기.
 */
export function matchFraction(s: string, idx: number): { ko: string; len: number } | null {
  // 시작 직전 경계: 숫자·소수점·슬래시에 바로 붙어 있으면 포기.
  const before = idx > 0 ? s[idx - 1] : ''
  if (/[\d./]/.test(before)) return null

  const rest = s.slice(idx)
  // 분자/분모 각각 1~2자리(0 패딩 없는 자연수). 3자리 이상 분모는 (B)에서도 제외(드뭄).
  const m = /^(\d{1,2})\/(\d{1,2})/.exec(rest)
  if (!m) return null

  const numerStr = m[1] // 분자(위)
  const denomStr = m[2] // 분모(아래)
  const consumed = m[0].length

  // 꼬리 경계: 매칭 직후가 숫자·'/'·'.'면 단순분수가 아니라 더 큰 패턴(날짜꼬리·중첩·소수) → 포기.
  const after = rest[consumed] ?? ''
  if (/[\d/.]/.test(after)) return null

  // 0 패딩(01/2, 1/02)·다중 0은 비전형 → 포기.
  if (/^0\d/.test(numerStr) || /^0\d/.test(denomStr)) return null

  const numer = Number(numerStr)
  const denom = Number(denomStr)

  // 분모 0(0 나눗셈)·분자 0 은 분수로 안 읽음.
  if (denom === 0 || numer === 0) return null
  // 분모 1(N/1)은 분수로 어색 → 포기.
  if (denom === 1) return null

  // ── (A) 흔한 단순분수: 분모 2~12, 분자 < 분모 (1/2,3/4,2/3,5/6,7/12 …) ──
  const isSimpleProper = denom >= 2 && denom <= 12 && numer < denom
  // ── (B) 날짜로 불가능: 분자(월후보)>12  OR  분모(일후보)>31 → 안전하게 분수 ──
  //   (13/4·3/40·5/32 … 'M/D 날짜'가 절대 될 수 없는 조합. 분모 100+ 큰 분수는 드물지만
  //    여기 들어오면 분수로 읽되, 너무 큰 수는 sinoInteger 가 null 을 줘 자연 포기된다.)
  const isImpossibleDate = numer > 12 || denom > 31

  // 둘 다 아니면 — 분모 13~31 & 분자 1~12 같은 '유효 날짜로도 보이는 모호 구간' →
  //   null 로 날짜 매처에 양보(예: 6/22, 5/30, 1/16 → 날짜 우선).
  if (!isSimpleProper && !isImpossibleDate) return null

  const denomKo = sinoInteger(denom)
  const numerKo = sinoInteger(numer)
  if (denomKo === null || numerKo === null) return null

  // 한국어 분수: "<분모>분의 <분자>"
  return { ko: `${denomKo}분의 ${numerKo}`, len: consumed }
}

// ─────────────────────────────────────────────────────────────
// 통화기호  —  ₩5000 -> "오천 원", €10 -> "십 유로", £20 -> "이십 파운드", ¥100 -> "백 엔"
// ─────────────────────────────────────────────────────────────

/**
 * 통화기호(숫자 앞)별 한국어 단위.
 *   ₩(U+20A9 원), ￦(U+FFE6 전각 원), €(유로), £(U+00A3 파운드), ¥(U+00A5)·￥(U+FFE5 엔/위안)
 *   ※ '$'는 speak.ts 가 이미 "<숫자> 달러"로 처리하므로 여기서 제외(중복 회피).
 *   ※ ¥는 엔(일본)·위안(중국) 둘 다 쓰지만, 한국 사용자 맥락에서 '엔'이 우세하다고 보고 '엔'.
 */
const CURRENCY_UNIT: Record<string, string> = {
  '₩': '원', // ₩
  '￦': '원', // ￦ (전각)
  '€': '유로',
  '£': '파운드', // £
  '¥': '엔', // ¥
  '￥': '엔', // ￥ (전각)
}

/**
 * 통화기호+숫자(기호가 숫자 "앞")를 idx 자리에서 인식하면 { ko, len }, 아니면 null.
 *   ₩5000 -> "오천 원", €10 -> "십 유로", £20 -> "이십 파운드", ¥100 -> "백 엔"
 *
 * 숫자 읽기는 자체 한자어 정수 음독(천단위 콤마 흡수). 소수는 통화에서 드물지만
 *   "<정수> 점 <소수자리> <단위>"로 처리(예: €10.5 -> "십 점 오 유로").
 *
 * 보수: 기호 직후가 숫자가 아니면(₩ 단독, ₩원) null. 금액이 억(8자리)을 넘으면 변환 포기.
 */
export function matchCurrency(s: string, idx: number): { ko: string; len: number } | null {
  const sym = s[idx]
  const unit = CURRENCY_UNIT[sym]
  if (!unit) return null

  const rest = s.slice(idx + 1) // 기호 다음부터
  // 숫자(콤마 허용) + 선택 소수부.
  const m = /^(\d[\d,]*)(\.\d+)?/.exec(rest)
  if (!m) return null

  const intRaw = m[1].replace(/,/g, '')
  const fracPart = m[2] ? m[2].slice(1) : ''
  const consumed = 1 + m[0].length // 기호 1글자 + 숫자 길이

  const intNum = Number(intRaw)
  const intKo = sinoInteger(intNum)
  if (intKo === null) return null // 너무 큰 금액 → 변환 포기

  let numRead = intKo
  if (fracPart) {
    const fracRead = fracPart
      .split('')
      .map((d) => SINO_DIGIT[Number(d)] ?? '')
      .join('')
    numRead += ` 점 ${fracRead}`
  }

  return { ko: `${numRead} ${unit}`, len: consumed }
}

// ─────────────────────────────────────────────────────────────
// 차원(D)  —  3D -> "쓰리디", 2D -> "투디", 4D -> "포디", 1D -> "원디"
// ─────────────────────────────────────────────────────────────

/** 1~9 영어 음독(차원 D 앞 숫자). 0·10+는 차원으로 거의 안 쓰여 제외. */
const ENG_DIGIT_KO: Record<string, string> = {
  '1': '원',
  '2': '투',
  '3': '쓰리',
  '4': '포',
  '5': '파이브',
  '6': '식스',
  '7': '세븐',
  '8': '에잇',
  '9': '나인',
}

/**
 * 차원 표기(숫자+D)를 idx 자리에서 인식하면 { ko, len }, 아니면 null.
 *   3D -> "쓰리디", 2D -> "투디", 4D -> "포디", 1D -> "원디"
 *
 * 규칙(보수):
 *   - 숫자 1~9 (한 자리) 바로 뒤에 대문자 'D' 한 글자.
 *   - 'D' 뒤에 영문자가 더 이어지면(3Days, 2Dx) 단어 일부 → null.
 *   - 'D' 뒤에 숫자가 이어지면(3D4) 모호 → null.
 *   - 숫자를 "영어 음독(원/투/쓰리/포/파이브…)" + "디" 로 읽는다(한자어 아님).
 *   - 시작 직전이 숫자/영문자면(x3D, 33D) 더 큰 토큰의 일부 → 포기.
 */
export function matchDimension(s: string, idx: number): { ko: string; len: number } | null {
  // 시작 직전 경계: 숫자/영문자에 바로 붙어 있으면 더 큰 토큰의 일부 → 포기.
  const before = idx > 0 ? s[idx - 1] : ''
  if (/[\dA-Za-z]/.test(before)) return null

  const rest = s.slice(idx)
  // 숫자 1자리(1~9) + 대문자 D.
  const m = /^([1-9])D/.exec(rest)
  if (!m) return null

  const digit = m[1]
  const consumed = m[0].length // 항상 2 (숫자1 + D1)

  // 'D' 뒤에 영문자/숫자가 더 붙으면 차원이 아님 → 포기.
  const after = rest[consumed] ?? ''
  if (/[A-Za-z0-9]/.test(after)) return null

  const eng = ENG_DIGIT_KO[digit]
  if (!eng) return null

  return { ko: `${eng}디`, len: consumed }
}
