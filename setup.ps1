# ko-dub 설치·점검 스크립트 (Windows PowerShell 5.1 이상, 여러 번 실행해도 안전)
#   powershell -ExecutionPolicy Bypass -File setup.ps1                       점검만 하고 부족한 것을 알려 준다(아무것도 설치하지 않음)
#   powershell -ExecutionPolicy Bypass -File setup.ps1 -InstallMissing       없는 프로그램(Python 3.12, ffmpeg, node)을 winget 으로 설치
#   ... -DownloadModels [-WithNLLB]   모델을 미리 받는다(Supertonic 380MB, Whisper 3GB, pyannote, NLLB 5GB)
#   ... -HfToken <토큰>               Hugging Face 토큰을 저장한다(pyannote 화자분리에 필요)
#   ... -SetEnv                       KO_DUB_HOME 사용자 환경변수를 이 폴더로 설정한다
#   ... -InstallSkill                 skill\ko-dub 를 Claude Code·Codex·Antigravity 스킬 폴더에 설치한다(install_skill.py)
#   ... -Cpu                          NVIDIA GPU 가 있어도 CPU 용 torch 를 설치한다
#   ... -Force                        python_paths.json 과 환경을 다시 만든다
# 만드는 것: 이 폴더의 venv-all (전사·화자분리·번역·합성이 모두 들어간 통합 파이썬 환경)과 python_paths.json
param(
  [switch]$InstallMissing,
  [switch]$DownloadModels,
  [switch]$WithNLLB,
  [switch]$SetEnv,
  [switch]$InstallSkill,
  [switch]$Cpu,
  [switch]$Force,
  [string]$HfToken = ""
)
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
function Say($m) { Write-Host $m }
function Warn($m) { Write-Host ("[주의] " + $m) -ForegroundColor Yellow }
function Fail($m) { Write-Host ("[중단] " + $m) -ForegroundColor Red; exit 1 }
function Have($n) { return [bool](Get-Command $n -ErrorAction SilentlyContinue) }
function AddPath($p) { if ((Test-Path $p) -and ($env:Path -notlike "*$p*")) { $env:Path = "$p;$env:Path" } }
function WingetInstall($id) {
  if (-not (Have winget)) { Fail "winget 이 없어 $id 를 자동 설치할 수 없습니다. 직접 설치한 뒤 다시 실행하세요." }
  Say "  winget 으로 $id 설치 중..."
  & winget install -e --id $id --accept-package-agreements --accept-source-agreements --silent
  if ($LASTEXITCODE -ne 0) { Warn "winget 종료코드 $LASTEXITCODE (이미 설치돼 있으면 정상)" }
}
function Probe($exe, [string[]]$a) {
  # 종료코드만 필요한 탐침용. $ErrorActionPreference=Stop 상태에서 네이티브 명령이 stderr 를 내면 5.1 은 이를 치명 오류로 처리하므로 잠시 Continue 로 바꾼다.
  $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
  try { & $exe @a 2>&1 | Out-Null; return $LASTEXITCODE } catch { return 1 } finally { $ErrorActionPreference = $old }
}
function Exec($exe, [string[]]$a) {
  & $exe @a
  if ($LASTEXITCODE -ne 0) { Fail ("명령 실패(" + $LASTEXITCODE + "): " + $exe + " " + ($a -join " ")) }
}

Say "== ko-dub 설치·점검 ($Root) =="

# 1) Python 3.12 -----------------------------------------------------------------
$venv = Join-Path $Root "venv-all"
$vpy = Join-Path $venv "Scripts\python.exe"
$paths = Join-Path $Root "python_paths.json"
$needVenv = $Force -or -not (Test-Path $vpy)
$havePaths = (Test-Path $paths) -and -not $Force

if ($needVenv -and -not $havePaths) {
  $py312 = ((Probe "py" @("-3.12", "--version")) -eq 0)
  if (-not $py312 -and $InstallMissing) { WingetInstall "Python.Python.3.12"; $py312 = ((Probe "py" @("-3.12", "--version")) -eq 0) }
  if (-not $py312) { Fail "Python 3.12 가 필요합니다(3.13 이상은 torch 휠이 없을 수 있음). -InstallMissing 으로 다시 실행하거나 직접 설치하세요: winget install Python.Python.3.12" }
  Say "[OK] Python 3.12 확인"
}

# 2) ffmpeg / node ---------------------------------------------------------------
if (-not (Have ffmpeg)) {
  $g = Get-ChildItem (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages") -Filter "Gyan.FFmpeg*" -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($g) { $b = Get-ChildItem $g.FullName -Recurse -Filter ffmpeg.exe -ErrorAction SilentlyContinue | Select-Object -First 1; if ($b) { AddPath $b.DirectoryName } }
}
if (-not (Have ffmpeg) -and $InstallMissing) {
  WingetInstall "Gyan.FFmpeg"
  $g = Get-ChildItem (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages") -Filter "Gyan.FFmpeg*" -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($g) { $b = Get-ChildItem $g.FullName -Recurse -Filter ffmpeg.exe -ErrorAction SilentlyContinue | Select-Object -First 1; if ($b) { AddPath $b.DirectoryName } }
}
if (Have ffmpeg) { Say "[OK] ffmpeg" } else { Warn "ffmpeg 없음 — -InstallMissing 으로 설치하거나 winget install Gyan.FFmpeg (설치 후 새 터미널)" }

function NodeOk() {
  if (-not (Have node)) { return $false }
  $v = (& node --version) -replace "^v", ""
  $p = $v.Split(".")
  $maj = [int]$p[0]; $min = [int]$p[1]
  return ($maj -ge 24) -or ($maj -eq 22 -and $min -ge 18) -or ($maj -eq 23 -and $min -ge 6)
}
if (-not (NodeOk) -and $InstallMissing) { WingetInstall "OpenJS.NodeJS.LTS"; AddPath "C:\Program Files\nodejs" }
if (NodeOk) { Say ("[OK] node " + (& node --version)) } else { Warn "node 22.18 이상 필요(발음 변환이 .ts 를 직접 실행) — -InstallMissing 또는 winget install OpenJS.NodeJS.LTS" }

# 3) GPU -------------------------------------------------------------------------
$hasGpu = $false
if (Have nvidia-smi) { $hasGpu = $true; Say ("[OK] NVIDIA GPU: " + ((& nvidia-smi --query-gpu=name,memory.total --format=csv,noheader) | Select-Object -First 1)) }
else { Warn "NVIDIA GPU 를 찾지 못했습니다. CPU 로도 동작하지만 전사·화자분리·번역이 몇 배~수십 배 느립니다." }
$useCuda = $hasGpu -and -not $Cpu
$torchIndex = if ($useCuda) { "https://download.pytorch.org/whl/cu128" } else { "https://download.pytorch.org/whl/cpu" }

# 4) 통합 가상환경 ----------------------------------------------------------------
if ($havePaths) {
  Say "[OK] python_paths.json 이 이미 있어 기존 환경을 그대로 씁니다(다시 만들려면 -Force)."
  $vpy = $null
} else {
  if (-not (Test-Path $vpy)) { Say "  가상환경 만드는 중: $venv"; Exec "py" @("-3.12", "-m", "venv", $venv) }
  Exec $vpy @("-m", "pip", "install", "--quiet", "--upgrade", "pip")
  $torchOk = ((Probe $vpy @("-c", "import torch")) -eq 0)
  if (-not $torchOk -or $Force) {
    Say "  torch 설치 중 ($torchIndex) — 용량이 커서 몇 분 걸립니다"
    Exec $vpy @("-m", "pip", "install", "torch==2.8.0", "torchaudio==2.8.0", "--index-url", $torchIndex)
  }
  Say "  나머지 패키지 설치 중 (requirements-all.txt)"
  Exec $vpy @("-m", "pip", "install", "-r", (Join-Path $Root "requirements-all.txt"))
  # BOM 없는 UTF-8 로 쓴다(BOM 이 있으면 파이썬 json.load 가 실패한다)
  $json = (@{ gpu = $vpy; tts = $vpy } | ConvertTo-Json)
  [System.IO.File]::WriteAllText($paths, $json, (New-Object System.Text.UTF8Encoding($false)))
  Say "[OK] python_paths.json 작성 (gpu/tts 모두 venv-all)"
}
if (-not $vpy) { $vpy = (& py -3 (Join-Path $Root "check_env.py") --py gpu) }

# 5) Hugging Face 토큰 -----------------------------------------------------------
if ($HfToken -ne "" -and $vpy) {
  Exec $vpy @("-c", "import sys; from huggingface_hub import login; login(token=sys.argv[1], add_to_git_credential=False)", $HfToken)
  Say "[OK] Hugging Face 토큰 저장"
}
$tokFile = Join-Path $env:USERPROFILE ".cache\huggingface\token"
if (-not $env:HF_TOKEN -and -not (Test-Path $tokFile)) {
  Warn "Hugging Face 토큰이 없습니다(화자분리에 필요). 1) https://huggingface.co/settings/tokens 에서 Read 토큰 발급  2) https://huggingface.co/pyannote/speaker-diarization-community-1 에서 약관 동의  3) setup.ps1 -HfToken <토큰>"
}

# 6) 선택 작업 -------------------------------------------------------------------
if ($DownloadModels -and $vpy) {
  $a = @((Join-Path $Root "prefetch_models.py")); if ($WithNLLB) { $a += "--nllb" }
  & $vpy @a
}
if ($SetEnv) { [Environment]::SetEnvironmentVariable("KO_DUB_HOME", $Root, "User"); Say "[OK] 사용자 환경변수 KO_DUB_HOME = $Root (새 터미널부터 적용)" }
if ($InstallSkill) {
  # 스킬 원본(skill\ko-dub)을 이 PC 에 설치된 에이전트(Claude Code, Codex, Antigravity)의 스킬 폴더에 복사하고 도구 폴더 경로를 기록한다
  $sysPy = if ($vpy) { $vpy } else { "py" }
  if ($sysPy -eq "py") { & py -3 (Join-Path $Root "install_skill.py") } else { & $sysPy (Join-Path $Root "install_skill.py") }
}

# 7) 최종 점검 -------------------------------------------------------------------
Say ""
Say "== 최종 점검 =="
$chk = if ($vpy) { $vpy } else { "py" }
if ($chk -eq "py") { & py -3 (Join-Path $Root "check_env.py") } else { & $chk (Join-Path $Root "check_env.py") }
exit $LASTEXITCODE
