// MarkdownRadio 의 toSpoken(발음 변환)을 그대로 재사용: stdin JSON 배열 → stdout JSON 배열
// speak/ 폴더에 복사해 둔 모듈을 상대 경로로 불러온다(도구 폴더만 옮겨도 동작).
import { toSpoken } from './speak/speak.ts'
let buf = ''
process.stdin.setEncoding('utf8')
process.stdin.on('data', (d) => (buf += d))
process.stdin.on('end', () => {
  const arr = JSON.parse(buf)
  process.stdout.write(JSON.stringify(arr.map((t) => toSpoken(t))))
})
