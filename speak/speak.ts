/**
 * 한국어 TTS 발음 개선 (FN-02 보조 · 발음 텍스트 생성)
 *
 * 우리 엔진(Supertonic)은 순수 텍스트만 받고 SSML·감정 표현이 불가능하다.
 * 그래서 "무슨 텍스트를 읽는가"를 자연스럽게 만드는 것이 발음 품질의 전부다.
 *
 * ⚠️ 불변식 보호: chunk.text 의 (startOffset,endOffset) 원문 점프 불변식 때문에
 *   chunk.text 자체는 절대 바꿀 수 없다. 이 모듈은 text 를 입력받아 "발음 텍스트(spokenText)"를
 *   새로 만들어 반환만 한다(원본 불변, 순수 함수). 지휘자가 chunk.ts 에서 toSpoken(chunk.text) 를
 *   호출해 별도 필드(spokenText)에 담는다. → 합성은 spokenText, 북마크/하이라이트는 text 로 분리.
 *
 * 보수적 원칙(MUST): 확신 없는 변환은 하지 않는다. 잘못 읽느니 원문 유지가 안전하다.
 *   각 규칙은 "명백히 어색하고 변환이 거의 확실한" 패턴만 건드린다.
 */

import { matchDate, matchTime } from './speak.datetime.ts'
import { matchPhone, matchOrdinal, matchTemperature } from './speak.misc.ts'
import { matchFraction, matchCurrency, matchDimension } from './speak.units.ts'

// ─────────────────────────────────────────────────────────────
// 규칙 토글 (옵션)
// ─────────────────────────────────────────────────────────────

/**
 * 규칙별 on/off. 기본은 전부 true(전 규칙 적용).
 * 단순형 toSpoken(text) 가 정식 계약이며, 이 옵션은 디버깅·튜닝·테스트용 부가 기능이다.
 */
export interface SpokenOptions {
  /** 단위·통화·퍼센트 (숫자와 결합해 함께 읽기). 기본 true. */
  units?: boolean
  /** 아라비아 숫자 → 한국어 읽기(연도/정수/소수). 기본 true. */
  numbers?: boolean
  /** 영문 약자(연속 대문자) → 알파벳 한글 음독. 기본 true. */
  acronyms?: boolean
  /** 기호(&, 원문자, 범위 ~, 화살표 등) → 한국어. 기본 true. */
  symbols?: boolean
  /** 호흡(pause) 보강: 구두점 살짝 보강. 기본 true. */
  breath?: boolean
  /** 날짜(6/22, 2026-06-22) → 한국어 읽기(달 불규칙 유월/시월). 기본 true. */
  dates?: boolean
  /** 시각(14:30) → 한국어 읽기(시 고유어·분 한자어). 기본 true. */
  times?: boolean
  /** 전화번호(010-1234-5678) → 자리별 음독(0=공). 기본 true. */
  phones?: boolean
  /** 영어 서수(3rd) → 한국어 차례(세 번째). 기본 true. */
  ordinals?: boolean
  /** 온도(25°C, 98.6°F) → 섭씨/화씨 도. 기본 true. */
  temperatures?: boolean
  /** 분수(1/2 → 이분의 일). 날짜로 보이면 양보. 기본 true. */
  fractions?: boolean
  /** 통화(₩5000·€10·£20·¥100). $는 별도 처리. 기본 true. */
  currencies?: boolean
  /** 차원(3D → 쓰리디, 2D → 투디). 기본 true. */
  dimensions?: boolean
}

const DEFAULT_OPTS: Required<SpokenOptions> = {
  units: true,
  numbers: true,
  acronyms: true,
  symbols: true,
  breath: true,
  dates: true,
  times: true,
  phones: true,
  ordinals: true,
  temperatures: true,
  fractions: true,
  currencies: true,
  dimensions: true,
}

// ─────────────────────────────────────────────────────────────
// 숫자 → 한국어 읽기
// ─────────────────────────────────────────────────────────────

const DIGIT_KO = ['영', '일', '이', '삼', '사', '오', '육', '칠', '팔', '구']
/** 4자리 내 자리값 (일/십/백/천). */
const SMALL_UNIT = ['', '십', '백', '천']
/** 4자리 묶음 단위 (만/억/조/경). 더 큰 수는 거의 안 나오므로 보수적으로 여기까지만. */
const BIG_UNIT = ['', '만', '억', '조', '경']

/**
 * 0~9999 정수를 한국어로. 자리값 '일'은 생략(일십→십, 일백→백)하되, '만' 단위 결합 때
 * 천의 자리 '일천'은 호출부에서 별도 처리하지 않고 여기 규칙대로 '천'으로 읽는다.
 * 예: 1->일, 10->십, 15->십오, 100->백, 1004->천사, 0->''(상위에서 처리)
 */
function readUnder10000(n: number): string {
  if (n === 0) return ''
  let out = ''
  const digits = String(n).split('').map(Number)
  const len = digits.length
  for (let i = 0; i < len; i++) {
    const d = digits[i]
    if (d === 0) continue
    const pos = len - 1 - i // 0=일,1=십,2=백,3=천
    // 자리값이 있는 칸(십/백/천)에서 숫자 1은 '일'을 생략(십, 백, 천).
    if (d === 1 && pos > 0) {
      out += SMALL_UNIT[pos]
    } else {
      out += DIGIT_KO[d] + SMALL_UNIT[pos]
    }
  }
  return out
}

/**
 * 음이 아닌 정수 문자열을 한국어 읽기로. 4자리씩 끊어 만/억/조 단위를 붙인다.
 * 매우 큰 수(경 초과)는 보수적으로 변환을 포기하고 원문 숫자열을 그대로 반환한다.
 */
function readInteger(numStr: string): string {
  // 앞 0 제거(00 같은 건 그대로 두면 어색하나, 우리는 의미보존 위해 정규화).
  const trimmed = numStr.replace(/^0+(?=\d)/, '')
  if (trimmed === '0') return '영'
  // 4자리 묶음이 BIG_UNIT 범위를 넘으면(너무 큰 수) 변환 포기.
  const groupCount = Math.ceil(trimmed.length / 4)
  if (groupCount > BIG_UNIT.length) return numStr // 보수적: 그대로 둠

  // 뒤에서부터 4자리씩 그룹화.
  const groups: number[] = []
  for (let end = trimmed.length; end > 0; end -= 4) {
    const start = Math.max(0, end - 4)
    groups.push(Number(trimmed.slice(start, end)))
  }
  // groups[0]=일의자리묶음 … groups[k]=BIG_UNIT[k]
  let out = ''
  for (let g = groups.length - 1; g >= 0; g--) {
    const val = groups[g]
    if (val === 0) continue
    out += readUnder10000(val) + BIG_UNIT[g]
  }
  return out === '' ? '영' : out
}

/** '2026' 같은 연도를 자연 읽기('이천이십육'). 정수 읽기와 동일하나 의도를 명확히. */
function readYear(numStr: string): string {
  return readInteger(numStr)
}

/**
 * 소수: 정수부.소수부 → "정수부읽기 점 소수부한자리씩".
 * 예: 1.5 -> '일 점 오', 3.14 -> '삼 점 일사', 0.5 -> '영 점 오'
 * 소수부는 자리값 없이 한 글자씩 읽는 게 한국어 관행.
 */
function readDecimal(intPart: string, fracPart: string): string {
  const intRead = readInteger(intPart)
  const fracRead = fracPart
    .split('')
    .map((d) => DIGIT_KO[Number(d)] ?? '')
    .join('')
  return `${intRead} 점 ${fracRead}`
}

// ─────────────────────────────────────────────────────────────
// 단위·통화·퍼센트
// ─────────────────────────────────────────────────────────────

/**
 * 숫자 뒤에 바로 붙는 단위 토큰 → 한국어. 긴 토큰을 먼저 매칭(MB 가 M·B 로 쪼개지지 않게).
 * 대소문자 구분: 데이터 단위는 대문자 표기가 관행이라 그대로, 통화기호·%는 별개.
 */
const UNIT_MAP: Array<[RegExp, string]> = [
  // 데이터 용량 (긴 것 우선)
  [/^TB/, '테라바이트'],
  [/^GB/, '기가바이트'],
  [/^MB/, '메가바이트'],
  [/^KB/, '킬로바이트'],
  [/^kB/, '킬로바이트'],
  [/^Kbps/, '킬로비피에스'],
  [/^Mbps/, '메가비피에스'],
  [/^Gbps/, '기가비피에스'],
  // 거리·길이 (대소문자 민감: km, cm, mm, m)
  [/^km/, '킬로미터'],
  [/^cm/, '센티미터'],
  [/^mm/, '밀리미터'],
  [/^nm/, '나노미터'],
  [/^m(?![a-zA-Z])/, '미터'],
  // 무게
  [/^kg/, '킬로그램'],
  [/^mg/, '밀리그램'],
  [/^g(?![a-zA-Z])/, '그램'],
  // 시간(초/밀리초) — 보수적으로 명백한 약어만
  [/^ms(?![a-zA-Z])/, '밀리초'],
  // 데이터 전송 보조
  [/^Hz/, '헤르츠'],
  [/^kHz/, '킬로헤르츠'],
  [/^MHz/, '메가헤르츠'],
  [/^GHz/, '기가헤르츠'],
]

/**
 * 숫자 직후 위치(idx)에서 단위를 인식하면 [한국어단위, 소비길이]를 반환, 아니면 null.
 * 단위 뒤에 영문자가 더 이어지면(단어 일부) 매칭을 보류(보수적).
 */
function matchUnit(s: string, idx: number): { ko: string; len: number } | null {
  const rest = s.slice(idx)
  for (const [re, ko] of UNIT_MAP) {
    const m = re.exec(rest)
    if (m) {
      const consumed = m[0].length
      // 단위 뒤에 또 영문자가 붙으면(예: 'kmh'처럼 모르는 결합) 보수적으로 포기.
      const after = rest[consumed]
      if (after && /[a-zA-Z]/.test(after)) continue
      return { ko, len: consumed }
    }
  }
  return null
}

// ─────────────────────────────────────────────────────────────
// 영문 약자(연속 대문자) → 알파벳 한글 음독
// ─────────────────────────────────────────────────────────────

/** A~Z 알파벳 음독. */
const ALPHA_KO: Record<string, string> = {
  A: '에이', B: '비', C: '씨', D: '디', E: '이', F: '에프', G: '지',
  H: '에이치', I: '아이', J: '제이', K: '케이', L: '엘', M: '엠', N: '엔',
  O: '오', P: '피', Q: '큐', R: '알', S: '에스', T: '티', U: '유',
  V: '브이', W: '더블유', X: '엑스', Y: '와이', Z: '지',
}

function spellAcronym(letters: string): string {
  return letters
    .split('')
    .map((c) => ALPHA_KO[c] ?? c)
    .join('')
}

// ─────────────────────────────────────────────────────────────
// 기호 → 한국어
// ─────────────────────────────────────────────────────────────

/** 원문자 ①~⑳ → 숫자(1~20). 코드포인트 직접 매핑. */
function circledToNumber(ch: string): string | null {
  const code = ch.codePointAt(0)!
  if (code >= 0x2460 && code <= 0x2473) {
    // ①(2460)=1 … ⑳(2473)=20
    return String(code - 0x2460 + 1)
  }
  return null
}

// ─────────────────────────────────────────────────────────────
// 메인 변환
// ─────────────────────────────────────────────────────────────

/**
 * 한국어 TTS용 발음 텍스트를 만든다(순수 함수).
 *
 * @param text 원문(또는 정제된 chunk.text). 절대 변형되지 않으며, 새 문자열을 반환만 한다.
 * @param opts 규칙별 토글(생략 시 전 규칙 ON).
 * @returns 발음 텍스트(spokenText). 빈 문자열/무음은 그대로 ''.
 */
export function toSpoken(text: string, opts?: SpokenOptions): string {
  if (text === '') return ''
  const o = { ...DEFAULT_OPTS, ...(opts ?? {}) }

  let out = ''
  let i = 0
  const n = text.length

  while (i < n) {
    const ch = text[i]

    // ── 1) 기호(원문자) — 숫자 변환 전에 처리(이후 숫자 규칙이 자연 이어짐) ──
    if (o.symbols) {
      const circ = circledToNumber(ch)
      if (circ !== null) {
        out += circ
        i++
        continue
      }
    }

    // ── 1.5) 날짜·시각·전화·순서·온도 매처 (숫자 덩어리보다 먼저!) ──
    //   이들은 모두 숫자로 시작하므로, 아래 숫자 스캐너가 '6'을 먼저 먹으면 '6/22'가 깨진다.
    //   각 매처는 idx에서 패턴이 명백할 때만 {ko,len}을 주고, 아니면 null(보수적) → 다음으로.
    //   우선순위: 날짜 > 시각 > 전화 > 서수 > 온도 (교차 입력은 서로 null이라 순서 안전).
    // 분수를 날짜보다 먼저: matchFraction은 1/2·3/4 같은 명백한 분수만 확정하고,
    //   6/22처럼 날짜로도 보이는 모호구간은 null을 반환해 아래 matchDate에 양보한다.
    //   (날짜를 먼저 태우면 1/2이 '1월 2일'로 잘못 먹히므로 분수가 반드시 앞서야 한다.)
    if (o.fractions) {
      const m = matchFraction(text, i)
      if (m) { out += m.ko; i += m.len; continue }
    }
    if (o.dates) {
      const m = matchDate(text, i)
      if (m) { out += m.ko; i += m.len; continue }
    }
    if (o.times) {
      const m = matchTime(text, i)
      if (m) { out += m.ko; i += m.len; continue }
    }
    if (o.phones) {
      const m = matchPhone(text, i)
      if (m) { out += m.ko; i += m.len; continue }
    }
    if (o.ordinals) {
      const m = matchOrdinal(text, i)
      if (m) { out += m.ko; i += m.len; continue }
    }
    if (o.temperatures) {
      const m = matchTemperature(text, i)
      if (m) { out += m.ko; i += m.len; continue }
    }
    if (o.currencies) {
      const m = matchCurrency(text, i)
      if (m) { out += m.ko; i += m.len; continue }
    }
    if (o.dimensions) {
      const m = matchDimension(text, i)
      if (m) { out += m.ko; i += m.len; continue }
    }

    // ── 2) 통화 기호가 숫자 앞에 오는 경우($100 -> 백 달러) ──
    if (o.units && ch === '$') {
      // 뒤에 숫자가 이어지면 "<숫자> 달러"로(통화는 뒤에 읽는 게 자연).
      const m = /^\$(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?/.exec(text.slice(i))
      if (m) {
        const intPart = m[1].replace(/,/g, '')
        const frac = m[2] ? m[2].slice(1) : ''
        const numRead = o.numbers
          ? frac
            ? readDecimal(intPart, frac)
            : readInteger(intPart)
          : m[1] + (m[2] ?? '')
        out += `${numRead} 달러`
        i += m[0].length
        continue
      }
      // 숫자 없는 단독 $ 는 보수적으로 그대로 둔다.
    }

    // ── 3) 숫자(아라비아) 덩어리 ──
    if (/\d/.test(ch)) {
      // 숫자열 + (선택) 소수부를 통째로 잡는다. 천단위 콤마(1,000)도 흡수.
      const m = /^(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?/.exec(text.slice(i))!
      const whole = m[0]
      const intRaw = m[1].replace(/,/g, '')
      const frac = m[2] ? m[2].slice(1) : ''
      let consumed = whole.length

      // 숫자 직후 단위가 붙는지 검사(100MB, 40%, 5km …).
      let unitKo: string | null = null
      const afterIdx = i + whole.length
      if (o.units) {
        // 퍼센트
        if (text[afterIdx] === '%') {
          unitKo = '퍼센트'
          consumed += 1
        } else {
          const u = matchUnit(text, afterIdx)
          if (u) {
            unitKo = u.ko
            consumed += u.len
          }
        }
      }

      // 숫자 읽기 결정.
      let numRead: string
      if (!o.numbers) {
        numRead = whole // 숫자 규칙 off 면 원문 숫자열 유지(단위만 한글).
      } else if (frac) {
        numRead = readDecimal(intRaw, frac)
      } else if (
        // 연도 휴리스틱: 4자리 + 단위 없음 + 콤마 없음 → 연도로 읽어도 정수 읽기와 동일.
        /^\d{4}$/.test(intRaw) &&
        !unitKo
      ) {
        numRead = readYear(intRaw)
      } else {
        numRead = readInteger(intRaw)
      }

      out += unitKo ? `${numRead} ${unitKo}` : numRead
      i += consumed
      continue
    }

    // ── 4) 영문 약자(연속 대문자 2~6자) ──
    //   보수적 휴리스틱: 대문자만 2~6개가 "단어 경계"로 고립돼 있을 때만.
    //   앞뒤가 영문자(대/소문자)면 더 긴 단어의 일부일 수 있어 건드리지 않는다.
    if (o.acronyms && /[A-Z]/.test(ch)) {
      const m = /^[A-Z]{2,6}/.exec(text.slice(i))
      if (m) {
        const acro = m[0]
        const before = i > 0 ? text[i - 1] : ''
        const after = text[i + acro.length] ?? ''
        // 경계 검사: 앞 글자가 영문자(대/소)면 단어 중간 → 보류.
        //   뒤 글자가 소문자면 'APIServer'처럼 일반 식별자 → 보류.
        //   뒤 글자가 대문자일 일은 없음(정규식이 최대로 먹음). 숫자는 허용(USB3 등은 드묾이라 보류).
        const beforeIsAlpha = /[A-Za-z]/.test(before)
        const afterIsLowerOrDigit = /[a-z0-9]/.test(after)
        if (!beforeIsAlpha && !afterIsLowerOrDigit) {
          out += spellAcronym(acro)
          i += acro.length
          continue
        }
        // 경계 조건 불충족 → 보수적으로 원문 유지(아래 기본 처리로).
      }
    }

    // ── 5) 단순 기호 치환(& ~ 화살표) ──
    if (o.symbols) {
      // &: 앞뒤 공백 정리해 '그리고'로. (A&B -> A 그리고 B)
      if (ch === '&') {
        // 앞에 공백이 없으면 하나 넣고, 뒤에도 공백 보장.
        if (out.length > 0 && !/\s$/.test(out)) out += ' '
        out += '그리고'
        i++
        // 뒤 공백 흡수해 '그리고B' 방지.
        if (text[i] && !/\s/.test(text[i])) out += ' '
        continue
      }
      // 범위 물결(~)이 숫자 사이/단독: '에서'로 읽으면 자연(3~5 -> 3 에서 5).
      //   단 한국어 텍스트 장식용 ~(말끝 늘임)일 수도 있어, "숫자~숫자" 패턴만 변환.
      if (ch === '~' || ch === '∼' || ch === '〜') {
        const prevIsDigit = /\d/.test(text[i - 1] ?? '')
        const nextIsDigit = /\d/.test(text[i + 1] ?? '')
        if (prevIsDigit && nextIsDigit) {
          out += ' 에서 '
          i++
          continue
        }
        // 그 외 ~ 는 보수적으로 그대로.
      }
      // 화살표: 자주 나오는 → ⇒ 를 '에서'가 아닌 흐름 표현으로. 보수적으로 '에서'대신 빼지 않고 유지하되
      //   명백한 '→'만 공백으로(읽으면 어색한 기호 제거). ⚠️ 의미 왜곡 우려로 단순 제거가 안전.
      if (ch === '→' || ch === '⇒' || ch === '➔' || ch === '➜') {
        // 좌우 공백 정리하며 자연스러운 쉼으로 대체(읽지 않음).
        if (out.length > 0 && !/\s$/.test(out)) out += ' '
        i++
        if (text[i] && !/\s/.test(text[i])) out += ' '
        continue
      }
    }

    // ── 기본: 그대로 복사(보수적 — 확신 없는 건 안 건드린다) ──
    out += ch
    i++
  }

  // ── 6) 호흡(pause) 보강 ──
  if (o.breath) {
    out = reinforceBreath(out)
  }

  return out
}

/**
 * 호흡 보강(과하지 않게):
 *  - 연속 공백을 1칸으로 정리(발음 텍스트라 자유롭게 가능).
 *  - 문장 끝 종결부호 뒤에 공백이 없으면 한 칸 넣어 호흡 경계를 명확히.
 *  ⚠️ 구두점을 새로 "추가"하진 않는다(과한 끊김 방지). 기존 구두점의 띄어쓰기만 정돈.
 */
function reinforceBreath(s: string): string {
  let out = s
  // 종결부호(. ! ? …) 뒤에 곧바로 비공백 문자가 오면 공백 삽입(문장 사이 호흡).
  out = out.replace(/([.!?…])(?=[^\s.!?…)\]"'」』])/g, '$1 ')
  // 쉼표 뒤 비공백도 한 칸(짧은 호흡).
  out = out.replace(/([,，、])(?=\S)/g, '$1 ')
  // 연속 공백 정리.
  out = out.replace(/[ \t]{2,}/g, ' ')
  return out
}
