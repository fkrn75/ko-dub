# 4단계: 한국어 더빙 트랙 + 원본 음성(덕킹) 믹스 → mp4 (영상 복사, 오디오 2트랙: 1=한국어 더빙 믹스, 2=원본)
# -OrigAudio 를 주면 영상에 소리가 없고 오디오가 별도 파일(weba 등)인 경우에 그 파일을 원본 음성으로 쓴다.
# 사용: powershell -File step4_mux.ps1 -Video <mp4> -Work <작업폴더> -Out <결과mp4> [-OrigAudio <오디오파일>] [-BgVol 0.30]
param(
  [Parameter(Mandatory = $true)][string]$Video,
  [Parameter(Mandatory = $true)][string]$Work,
  [Parameter(Mandatory = $true)][string]$Out,
  [string]$OrigAudio = "",
  [double]$BgVol = 0.30
)
$ErrorActionPreference = "Stop"

# ffmpeg 인자는 전부 배열로 만든다(빈 문자열 인자가 조용히 사라져 "-map 이 필터로 해석"되던 사고 방지)
$bg = $BgVol.ToString([System.Globalization.CultureInfo]::InvariantCulture)
$origIdx = if ($OrigAudio -ne "") { 2 } else { 0 }   # 원본 음성이 있는 입력 번호
$filter = "[$($origIdx):a]aresample=44100,volume=$bg[o];" +
          "[1:a]aresample=44100,asplit=2[d1][d2];" +
          "[o][d1]sidechaincompress=threshold=0.02:ratio=14:attack=15:release=600[duck];" +
          "[duck][d2]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11[mix]"

$ff = @("-y", "-hide_banner", "-loglevel", "error", "-i", $Video, "-i", (Join-Path $Work "dub_ko.wav"))
if ($OrigAudio -ne "") { $ff += @("-i", $OrigAudio) }
$ff += @("-filter_complex", $filter, "-map", "0:v:0", "-map", "[mix]", "-map", "$($origIdx):a:0",
         "-c:v", "copy", "-c:a:0", "aac", "-b:a:0", "192k")
# 별도 오디오(opus 등)는 mp4 호환을 위해 aac 로 변환, 영상 내장 오디오는 그대로 복사
if ($OrigAudio -ne "") { $ff += @("-c:a:1", "aac", "-b:a:1", "128k") } else { $ff += @("-c:a:1", "copy") }
$ff += @("-metadata:s:a:0", "language=kor", "-metadata:s:a:0", "title=한국어 더빙",
         "-metadata:s:a:1", "language=eng", "-metadata:s:a:1", "title=Original",
         "-disposition:a:0", "default", "-disposition:a:1", "0", "-movflags", "+faststart", $Out)

& ffmpeg @ff
if ($LASTEXITCODE -ne 0) { throw "ffmpeg 실패 (exit $LASTEXITCODE)" }
Get-Item -LiteralPath $Out | Select-Object Name, @{n = 'MB'; e = { [math]::Round($_.Length / 1MB, 1) } }
