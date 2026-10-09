/**
 * 한국어 TTS 발음 개선 — 날짜·시각 매처 (FN-02 보조 · speak.ts toSpoken 루프 확장)
 *
 * 지휘자가 speak.ts 의 toSpoken 루프에 끼워 쓰는 두 매처를 제공한다.
 * speak.ts 의 matchUnit(s, idx) 패턴과 동일한 계약:
 *   - s 의 idx 위치에서 패턴이 매칭되면 { ko: 한국어발음, len: 소비한 문자수 } 반환.
 *   - 아니면 null(원문 유지).
 *
 * ⚠️ speak.ts 는 절대 import 하지 않는다(순환 의존 회피). 숫자 음독은
 *   범위가 작으므로(연 4자리·월/일/시/분) 이 파일에 자체 최소 구현을 둔다.
 *   DIGIT 테이블도 로컬 복제(공유 import 안 함).
 *
 * 보수적 원칙(MUST): 확신 없으면 null 을 반환해 원문을 유지한다.
 *   '/' 와 ':' 는 분수·비율·경로·성경구절(3:1) 등에도 쓰이므로, 월/일/시/분이
 *   모두 유효 범위 안일 때에만 날짜·시각으로 해석한다.
 */

// ─────────────────────────────────────────────────────────────
// 숫자 음독 (자체 최소 구현 — speak.ts import 금지)
// ─────────────────────────────────────────────────────────────

/** 한자어 숫자 0~9 (연/월/일/분 읽기에 사용). */
const SINO_DIGIT = ['영', '일', '이', '삼', '사', '오', '육', '칠', '팔', '구']
/** 4자리 내 자리값 (일/십/백/천). */
const SINO_SMALL_UNIT = ['', '십', '백', '천']
/** 4자리 묶음 단위 (만/억…). 연도(최대 4자리)면 '만'까지로 충분하나 일반화해 둔다. */
const SINO_BIG_UNIT = ['', '만', '억']

/**
 * 0~9999 한자어 읽기. 자리값 숫자 1은 생략(일십→십, 일백→백, 일천→천).
 * 예: 1->일, 10->십, 15->십오, 26->이십육, 2026 그룹용 2->이…
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
 * 음이 아닌 정수(작은 범위)를 한자어로. 연도(이천이십육)·월·일·분 읽기 공용.
 * 4자리씩 끊어 만/억 단위를 붙인다(연도는 4자리 이하라 사실상 만 단위까지).
 */
function sinoInteger(n: number): string {
  if (n === 0) return '영'
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

/**
 * 고유어 시(時) 읽기 0~23. 시계의 '시'는 고유어가 한국어 관행(한 시·열네 시).
 *   0=영(영 시), 1~12=하나..열둘의 관형형(한/두/세…열두),
 *   13~23 도 자연스러운 고유어 결합(열세·열네…스물세)로 확장한다.
 * '시'와 결합하는 관형형이므로 12=열두, 1=한, 20=스무, 22=스물두 형태.
 */
const NATIVE_HOUR: Record<number, string> = {
  0: '영',
  1: '한',
  2: '두',
  3: '세',
  4: '네',
  5: '다섯',
  6: '여섯',
  7: '일곱',
  8: '여덟',
  9: '아홉',
  10: '열',
  11: '열한',
  12: '열두',
  13: '열세',
  14: '열네',
  15: '열다섯',
  16: '열여섯',
  17: '열일곱',
  18: '열여덟',
  19: '열아홉',
  20: '스무',
  21: '스물한',
  22: '스물두',
  23: '스물세',
}

// ─────────────────────────────────────────────────────────────
// 날짜
// ─────────────────────────────────────────────────────────────

/**
 * 월 읽기(불규칙 필수). 6월=유월, 10월=시월. 나머지는 한자어 숫자+월.
 * (월 자체에 '월'을 붙여 반환.)
 */
function readMonth(m: number): string {
  if (m === 6) return '유월'
  if (m === 10) return '시월'
  return sinoInteger(m) + '월'
}

/** 일 읽기: 한자어 숫자 + 일. (예: 22 -> 이십이일) */
function readDay(d: number): string {
  return sinoInteger(d) + '일'
}

/** 월 범위 검증(1~12). */
function isValidMonth(m: number): boolean {
  return m >= 1 && m <= 12
}
/** 일 범위 검증(1~31). 월별 정확한 일수까지는 보지 않는다(보수적이되 과도하지 않게). */
function isValidDay(d: number): boolean {
  return d >= 1 && d <= 31
}

/**
 * s 의 idx 위치에서 날짜 패턴을 인식하면 { ko, len } 반환, 아니면 null.
 *
 * 지원 패턴(우선순위: 긴 것 먼저):
 *   1) YYYY-MM-DD  (2026-06-22 -> "이천이십육 년 유월 이십이일")
 *   2) YYYY/MM/DD  (2026/06/22 동일)
 *   3) M/D 또는 MM/DD (6/22 -> "유월 이십이일")
 *
 * 경계·보수 규칙:
 *   - 매칭 직후 문자가 숫자거나 '/'·'-'(추가 구분자)면 더 큰 패턴의 일부로 보고 포기.
 *     (예: 6/22/99 처럼 꼬리가 붙으면 M/D 로 섣불리 먹지 않음)
 *   - 매칭 시작 직전 문자가 숫자/'.'면 더 큰 수의 일부일 수 있어 포기(소수·버전 등).
 *   - 월 1~12, 일 1~31 범위 밖이면 null(분수·비율·경로 보호).
 */
export function matchDate(s: string, idx: number): { ko: string; len: number } | null {
  // 시작 직전 경계: 숫자나 소수점에 바로 붙어 있으면(예: '12.6/22') 포기.
  const before = idx > 0 ? s[idx - 1] : ''
  if (/[\d.]/.test(before)) return null

  const rest = s.slice(idx)

  // ── 1) YYYY-MM-DD / YYYY/MM/DD ──
  {
    const m = /^(\d{4})([-/])(\d{1,2})\2(\d{1,2})/.exec(rest)
    if (m) {
      const year = Number(m[1])
      const month = Number(m[3])
      const day = Number(m[4])
      const consumed = m[0].length
      const after = rest[consumed] ?? ''
      // 꼬리에 숫자/같은 구분자가 더 붙으면(예: 시각·범위) 보수적으로 포기.
      if (/[\d]/.test(after) || after === m[2]) return null
      if (isValidMonth(month) && isValidDay(day)) {
        const ko = `${sinoInteger(year)} 년 ${readMonth(month)} ${readDay(day)}`
        return { ko, len: consumed }
      }
      return null // 범위 밖이면 날짜 아님
    }
  }

  // ── 2) M/D · MM/DD (슬래시만 — '-'는 음수·범위·하이픈과 겹쳐 위험하므로 제외) ──
  {
    const m = /^(\d{1,2})\/(\d{1,2})/.exec(rest)
    if (m) {
      const month = Number(m[1])
      const day = Number(m[2])
      const consumed = m[0].length
      const after = rest[consumed] ?? ''
      // 뒤에 숫자나 '/'가 더 오면 날짜가 아니라 분수·경로·비율일 수 있어 포기.
      if (/[\d]/.test(after) || after === '/') return null
      if (isValidMonth(month) && isValidDay(day)) {
        const ko = `${readMonth(month)} ${readDay(day)}`
        return { ko, len: consumed }
      }
      return null // 범위 밖(6/99 등)이면 분수·비율 → 원문 유지
    }
  }

  return null
}

// ─────────────────────────────────────────────────────────────
// 시각
// ─────────────────────────────────────────────────────────────

/** 시 범위 검증(0~23). */
function isValidHour(h: number): boolean {
  return h >= 0 && h <= 23
}
/** 분 범위 검증(0~59). */
function isValidMinute(m: number): boolean {
  return m >= 0 && m <= 59
}

/** 시 읽기: 고유어 관형형 + 시. (예: 14 -> 열네 시) */
function readHour(h: number): string {
  return `${NATIVE_HOUR[h]} 시`
}

/**
 * 분 읽기: 한자어 숫자 + 분. 00분은 '정각'으로(영 분 회피).
 *   예: 30 -> 삼십 분, 5 -> 오 분, 0 -> 정각
 */
function readMinute(m: number): string {
  if (m === 0) return '정각'
  return `${sinoInteger(m)} 분`
}

/**
 * s 의 idx 위치에서 시각 패턴(H:MM / HH:MM)을 인식하면 { ko, len }, 아니면 null.
 *
 *   14:30 -> "열네 시 삼십 분", 9:05 -> "아홉 시 오 분", 0:00 -> "영 시 정각"
 *
 * 보수 규칙:
 *   - 분은 반드시 2자리(MM)로 본다. 시:분 표기는 분을 0-패딩하는 게 관행이고,
 *     1자리 분을 허용하면 성경구절 3:1·비율 a:b 오인식이 커진다.
 *   - 시 0~23, 분 00~59 범위 밖이면 null(비율·구절 보호).
 *   - 시작 직전이 숫자/'.'면, 끝 직후가 숫자/':'(예: HH:MM:SS)면 보수적으로 포기.
 */
export function matchTime(s: string, idx: number): { ko: string; len: number } | null {
  // 시작 직전 경계: 숫자/소수점에 바로 붙어 있으면 더 큰 수의 일부 → 포기.
  const before = idx > 0 ? s[idx - 1] : ''
  if (/[\d.]/.test(before)) return null

  const rest = s.slice(idx)
  // 시(1~2자리) : 분(정확히 2자리).
  const m = /^(\d{1,2}):(\d{2})/.exec(rest)
  if (!m) return null

  const hour = Number(m[1])
  const minute = Number(m[2])
  const consumed = m[0].length
  const after = rest[consumed] ?? ''
  // 뒤에 숫자나 ':'(초 등)가 이어지면 시각 단독이 아니므로 보수적으로 포기.
  if (/[\d]/.test(after) || after === ':') return null

  if (isValidHour(hour) && isValidMinute(minute)) {
    const ko = `${readHour(hour)} ${readMinute(minute)}`
    return { ko, len: consumed }
  }
  return null // 범위 밖(3:1=구절·25:00=비율 등) → 원문 유지
}
