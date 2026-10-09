# 다른 PC로 가져갈 키트 만들기: 도구 폴더(큰 작업폴더·가상환경·캐시 제외, 스킬 원본 skill/ko-dub 포함)를 zip 하나로 묶는다.
#   powershell -ExecutionPolicy Bypass -File export_kit.ps1 [-Out C:\경로\ko-dub-kit.zip]
# 새 PC에서: zip 을 풀고  toolkit\setup.ps1 -InstallMissing -DownloadModels -SetEnv -InstallSkill  (토큰은 -HfToken 으로 직접)
param([string]$Out = (Join-Path ([Environment]::GetFolderPath("Desktop")) "ko-dub-kit.zip"))
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
if (-not (Test-Path (Join-Path $Root "skill\ko-dub\SKILL.md"))) { throw "스킬 원본이 없습니다: $Root\skill\ko-dub" }

$stage = Join-Path ([System.IO.Path]::GetTempPath()) ("ko-dub-kit-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
$tk = Join-Path $stage "toolkit"
New-Item -ItemType Directory -Force $tk | Out-Null

# 도구 폴더: 작업폴더(work*), 가상환경, 캐시, 파이썬 캐시, 검증용 임시 파일, 이 PC 전용 경로 파일은 제외
& robocopy $Root $tk /E /NFL /NDL /NJH /NJS /NP /XD "work" "work_*" ".venv" "venv-all" "__pycache__" "tts_cache" /XF "python_paths.json" "probe_*.wav" "*.log" "*.bak_before_fix" "tts_probe.py" "glossary_add_astra.py" | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy 실패($LASTEXITCODE)" }

if (Test-Path $Out) { Remove-Item $Out -Force }
Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $Out
Remove-Item $stage -Recurse -Force
$mb = [math]::Round((Get-Item $Out).Length / 1MB, 1)
Write-Host "키트 생성: $Out ($mb MB)"
Write-Host "포함: toolkit(스크립트·용어집·발음 변환·스킬 원본 skill/ko-dub). 제외: 작업폴더, 가상환경, 모델 캐시, python_paths.json(이 PC 전용)"
