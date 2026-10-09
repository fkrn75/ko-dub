/**
 * 한국어 TTS 발음 개선 (FN-02 보조) · 전화번호·서수·온도 매처
 *
 * speak.ts 의 toSpoken 안에서 지휘자가 호출하는 "부분 매처(partial matcher)" 모음이다.
 * 각 함수는 문자열 s 와 위치 idx 를 받아, 그 자리에서 패턴이 명백히 인식되면
 * { ko, len }(발음 한국어 + 소비한 원문 길이)을, 아니면 null 을 반환한다.
 *
 * ⚠️ 시그니처는 speak.ts 의 matchUnit 과 동일하다(통일):
 *     match...(s: string, idx: number): { ko: string; len: number } | null
 *
 * 보수적 원칙(MUST): 확신 없으면 null. 잘못 읽느니 원문 유지가 안전하다.
 *   - 전화번호: 하이픈 구분 + 전화로 명백한 휴리스틱(휴대폰 010/011·지역 02/0NN·대표 15/16/18…)일 때만.
 *     일반 하이픈 수식('1-2명','2-3개','A-1')은 절대 null.
 *   - 서수: 숫자 바로 뒤 st/nd/rd/th 접미사 정확 매칭. 'word','the' 처럼 단어 일부는 건드리지 않는다.
 *
 * ⚠️ speak.ts 를 import 하지 않는다(순환 의존 회피). 숫자 음독은 이 파일 자체 최소 구현.
 */

// ─────────────────────────────────────────────────────────────
// 자체 최소 숫자 음독 (speak.ts 와 공유하지 않음 · 순환 회피)
// ─────────────────────────────────────────────────────────────

/** 0~9 한자음 음독. 전화번호 자리별 읽기에서 0 은 '공'(아래 별도). */
const DIGIT_KO = ['영', '일', '이', '삼', '사', '오', '육', '칠', '팔', '구']

/**
 * 전화번호 자리별 음독: 한 자리씩 읽되 0 은 '공'.
 * 예: '010' -> '공일공', '1234' -> '일이삼사'
 */
function readDigitsPhone(digits: string): string {
  return digits
    .split('')
    .map((d) => (d === '0' ? '공' : (DIGIT_KO[Number(d)] ?? d)))
    .join('')
}

// ─────────────────────────────────────────────────────────────
// 고유어 차례(서수) — 첫/두/세/네/다섯 … 번째
// ─────────────────────────────────────────────────────────────

/** 1~10 고유어 차례 어간(번째 앞). index 0 미사용. */
const NATIVE_ORDINAL_ONES = [
  '', // 0 자리 채움
  '첫',
  '두',
  '세',
  '네',
  '다섯',
  '여섯',
  '일곱',
  '여덟',
  '아홉',
  '열',
]

/** 10·20·30 십의 자리 어간. 단독(10·20·30)일 때 사용. */
const NATIVE_ORDINAL_TENS_SOLO: Record<number, string> = {
  10: '열',
  20: '스무',
  30: '서른',
}

/** 11~19, 21~29, 31 등 십의 자리 어간(뒤에 일의 자리 어간이 붙는 결합형). */
const NATIVE_ORDINAL_TENS_COMBINE: Record<number, string> = {
  10: '열',
  20: '스물',
  30: '서른',
}

/**
 * 1~31 정수를 고유어 차례 어간으로(뒤에 ' 번째'가 붙는다).
 * 예: 1->'첫', 2->'두', 11->'열한', 21->'스물한', 22->'스물두', 30->'서른', 31->'서른한'
 * 범위를 벗어나면 null(보수적: 큰 서수는 거의 없고 어색할 수 있어 변환 포기).
 */
function nativeOrdinalStem(n: number): string | null {
  if (n < 1 || n > 31) return null
  if (n <= 10) return NATIVE_ORDINAL_ONES[n]

  const tens = Math.floor(n / 10) * 10
  const ones = n % 10
  if (ones === 0) {
    // 10·20·30 단독
    return NATIVE_ORDINAL_TENS_SOLO[tens] ?? null
  }
  const tensStem = NATIVE_ORDINAL_TENS_COMBINE[tens]
  if (!tensStem) return null
  // 결합형 일의 자리: '한/두/세…' — 1 은 '한'(첫 아님), 나머지는 ONES 그대로.
  const onesStem = ones === 1 ? '한' : NATIVE_ORDINAL_ONES[ones]
  return tensStem + onesStem
}

/**
 * 영어 서수(1st·2nd·3rd·4th·11th·21st·22nd …)를 idx 자리에서 인식.
 *
 * 규칙(보수적):
 *  - 숫자 1~2자리 + 서수 접미사(st/nd/rd/th, 소문자만) 정확 매칭.
 *  - 접미사가 숫자와 문법적으로 일치해야 함(1->st, 2->nd, 3->rd, 그 외 th;
 *    단 11/12/13 은 예외로 th). 'word'/'the' 처럼 숫자 없이 오는 건 애초에 숫자로 시작 안 하므로 매칭 안 됨.
 *  - 서수 뒤에 영문자가 더 이어지면(예 '1stomething') 단어 일부일 수 있어 보류(null).
 *  - 숫자 뒤 1글자라도 앞 글자가 숫자가 아니어야 함은 호출부(숫자 스캐너) 책임이나,
 *    여기서는 idx 가 숫자 시작이라는 전제만 둔다.
 *
 * @returns { ko: '<차례> 번째', len } 또는 null
 */
export function matchOrdinal(s: string, idx: number): { ko: string; len: number } | null {
  const rest = s.slice(idx)
  // 숫자 1~2자리 + 서수 접미사. 대문자 접미사(ST/ND)는 흔치 않아 보수적으로 소문자만.
  const m = /^(\d{1,2})(st|nd|rd|th)/.exec(rest)
  if (!m) return null

  const numStr = m[1]
  const suffix = m[2]
  const consumed = m[0].length

  // 서수 뒤에 영문자가 더 붙으면 단어 일부 가능성 → 보류.
  const after = rest[consumed]
  if (after && /[a-zA-Z]/.test(after)) return null

  const num = Number(numStr)

  // 접미사·숫자 일치 검증(영문법). 불일치면 오타/우연이므로 보류.
  const expected = expectedOrdinalSuffix(num)
  if (suffix !== expected) return null

  const stem = nativeOrdinalStem(num)
  if (stem === null) return null

  return { ko: `${stem} 번째`, len: consumed }
}

/** 영어 서수 접미사 규칙: 11/12/13->th, 그 외 끝자리 1->st,2->nd,3->rd, 나머지 th. */
function expectedOrdinalSuffix(n: number): 'st' | 'nd' | 'rd' | 'th' {
  const mod100 = n % 100
  if (mod100 >= 11 && mod100 <= 13) return 'th'
  switch (n % 10) {
    case 1:
      return 'st'
    case 2:
      return 'nd'
    case 3:
      return 'rd'
    default:
      return 'th'
  }
}

// ─────────────────────────────────────────────────────────────
// 전화번호
// ─────────────────────────────────────────────────────────────

/**
 * 전화번호를 idx 자리에서 인식. 하이픈으로 구분된 숫자 그룹이 "전화로 명백"할 때만 변환.
 *
 * 인식하는 형태(보수적 화이트리스트):
 *  - 휴대폰: 010/011/016/017/018/019 - NNN(N) - NNNN     (예 010-1234-5678, 011-123-4567)
 *  - 지역 02:           02 - NNN(N) - NNNN                (예 02-123-4567, 02-1234-5678)
 *  - 지역 0NN(3자리):   0NN - NNN(N) - NNNN               (예 031-123-4567, 051-1234-5678)
 *  - 인터넷전화 070:    070 - NNNN - NNNN                 (예 070-1234-5678)
 *  - 대표번호 4자리:    15NN/16NN/18NN - NNNN             (예 1588-1234, 1644-0000)
 *
 * 읽기: 각 그룹을 자리별 음독(0='공'), 그룹 사이는 짧은 공백.
 *   예 010-1234-5678 -> '공일공 일이삼사 오육칠팔'
 *
 * ⚠️ 일반 하이픈 수식('1-2','2-3개','A-1')은 위 화이트리스트에 안 걸리므로 null.
 *   또한 전화번호 앞뒤가 숫자/하이픈으로 더 이어지면(긴 식별자 일부) 보류.
 *
 * @returns { ko, len } 또는 null
 */
export function matchPhone(s: string, idx: number): { ko: string; len: number } | null {
  const rest = s.slice(idx)

  // 후보: 숫자/하이픈으로 이루어진 선두 토큰을 통째로 잡는다(2~3 그룹).
  //   세 그룹(국번-중간-끝) 또는 두 그룹(대표번호/지역 약식).
  const m = /^(\d{2,4})-(\d{3,4})(?:-(\d{4}))?/.exec(rest)
  if (!m) return null

  const g1 = m[1]
  const g2 = m[2]
  const g3 = m[3] // undefined 가능(두 그룹)
  const consumed = m[0].length

  // 경계: 매칭 직후 글자가 숫자/하이픈이면 더 긴 무언가의 일부 → 보류.
  const after = rest[consumed]
  if (after && /[\d-]/.test(after)) return null
  // 경계: 바로 앞 글자가 숫자/하이픈이어도(이미 숫자 스캔 중이던 흐름) 보류.
  //   (지휘자는 보통 비숫자 위치에서 이 매처를 부르지만 안전망으로 둔다.)
  const before = idx > 0 ? s[idx - 1] : ''
  if (before && /[\d-]/.test(before)) return null

  // ── 전화 휴리스틱 판정(화이트리스트) ──
  if (!isPhoneLike(g1, g2, g3)) return null

  const groups = g3 !== undefined ? [g1, g2, g3] : [g1, g2]
  const ko = groups.map(readDigitsPhone).join(' ')
  return { ko, len: consumed }
}

/** 휴대폰 식별 국번. */
const MOBILE_PREFIX = new Set(['010', '011', '016', '017', '018', '019'])

/**
 * (g1,g2,g3)가 전화번호로 명백한지. 보수적 화이트리스트.
 *  - 세 그룹: 휴대폰(010…)·서울(02)·지역(0NN)·070
 *  - 두 그룹: 대표번호(15NN/16NN/18NN)-NNNN
 */
function isPhoneLike(g1: string, g2: string, g3?: string): boolean {
  if (g3 !== undefined) {
    // 세 그룹 형태.
    if (MOBILE_PREFIX.has(g1)) {
      // 휴대폰: 중간 3~4 + 끝 4.
      return /^\d{3,4}$/.test(g2) && g3.length === 4
    }
    if (g1 === '02') {
      // 서울: 02 - 3~4 - 4.
      return /^\d{3,4}$/.test(g2) && g3.length === 4
    }
    if (g1 === '070') {
      // 인터넷전화: 070 - 4 - 4.
      return g2.length === 4 && g3.length === 4
    }
    if (/^0\d{2}$/.test(g1)) {
      // 기타 지역번호(3자리, 0으로 시작): 0NN - 3~4 - 4.
      return /^\d{3,4}$/.test(g2) && g3.length === 4
    }
    return false
  }

  // 두 그룹 형태: 대표번호만 인정(15NN/16NN/18NN - 4자리).
  if (/^(15|16|18)\d{2}$/.test(g1) && g2.length === 4) {
    return true
  }
  return false
}

// ─────────────────────────────────────────────────────────────
// 온도 (선택 기능) — 25°C / 25℃ -> '섭씨 이십오 도'
// ─────────────────────────────────────────────────────────────

/**
 * 0~9999 정수를 한국어 한자음 읽기로(온도 등 일반 수치용 · 자체 최소 구현).
 * speak.ts 의 readInteger 와 동일 취지지만 import 회피를 위해 여기 간이 구현.
 * 4자리(만 단위)까지만 다루며 그 이상은 호출부에서 쓰지 않는다.
 */
const SMALL_UNIT = ['', '십', '백', '천']
function readUnder10000Sino(n: number): string {
  if (n === 0) return '영'
  let out = ''
  const digits = String(n).split('').map(Number)
  const len = digits.length
  for (let i = 0; i < len; i++) {
    const d = digits[i]
    if (d === 0) continue
    const pos = len - 1 - i
    if (d === 1 && pos > 0) out += SMALL_UNIT[pos]
    else out += DIGIT_KO[d] + SMALL_UNIT[pos]
  }
  return out
}

/**
 * 온도를 idx 자리에서 인식. 숫자(정수/소수) + 도 기호(°C/℃/°F/℉) 패턴만.
 *   25°C -> '섭씨 이십오 도', 25℃ -> '섭씨 이십오 도', 98.6°F -> '화씨 구십팔 점 육 도'
 *   '°'(U+00B0) 뒤 C/F, 또는 단일 기호 '℃'(U+2103)/'℉'(U+2109).
 *
 * 보수적: 숫자에 바로 붙은 온도 기호만. 단위 없는 '°'(각도)는 모호하므로 변환 안 함(null).
 *
 * @returns { ko, len } 또는 null
 */
export function matchTemperature(s: string, idx: number): { ko: string; len: number } | null {
  const rest = s.slice(idx)
  // 숫자(정수 + 선택 소수) + 온도기호.
  //   형태 A: 25°C / 98.6°F   (° 뒤 C|F)
  //   형태 B: 25℃ / 98.6℉     (합자 기호)
  const m = /^(\d+)(\.\d+)?(?:°\s?([CF])|([℃℉]))/.exec(rest)
  if (!m) return null

  const intPart = m[1]
  const fracPart = m[2] ? m[2].slice(1) : ''
  const letter = m[3] // 'C' | 'F' | undefined
  const combined = m[4] // '℃' | '℉' | undefined
  const consumed = m[0].length

  // 섭씨/화씨 판정.
  let scale: '섭씨' | '화씨'
  if (letter === 'C' || combined === '℃') scale = '섭씨'
  else if (letter === 'F' || combined === '℉') scale = '화씨'
  else return null

  // 숫자 읽기(정수 + 소수). 자체 최소 구현 사용.
  const intNum = Number(intPart)
  if (intNum > 9999) return null // 보수적: 비현실적 온도는 변환 포기.
  let numRead = readUnder10000Sino(intNum)
  if (fracPart) {
    const fracRead = fracPart
      .split('')
      .map((d) => DIGIT_KO[Number(d)] ?? '')
      .join('')
    numRead += ` 점 ${fracRead}`
  }

  return { ko: `${scale} ${numRead} 도`, len: consumed }
}
