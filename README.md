# ko-dub — 영어 영상 → 화자별 한국어 더빙 (로컬·무료)

영어 영상(유튜브 URL, 로컬 mp4, 영상 + 별도 오디오)을 **화자를 구분해 남녀 음성이 다른 한국어 더빙 mp4**로 만드는 도구입니다.
Claude Code · Codex · Antigravity 에서 **스킬(`ko-dub`)** 로 쓰거나, 명령줄(`kodub.py`)로 직접 쓸 수 있습니다.

> 🔒 **개인 학습용입니다.** 원본 영상의 저작권은 원저작자에게 있습니다. 결과물에는 기본으로 "개인 학습용 · 재배포 금지" 워터마크가
> 우상단에 5분마다 8초씩 흐리게 표시됩니다(시청을 거의 방해하지 않는 수준). 만든 영상을 공개 업로드·재배포하지 마세요.
> 유튜브 영상을 받을 때는 해당 서비스의 이용약관을 확인하고 본인 책임으로 사용하세요.

## 동작 방식

```
영상 ─ ffmpeg 16kHz ─ faster-whisper large-v3 (단어 타임스탬프)
     ─ pyannote community-1 (화자분리) ─ 화자별 음높이(F0)로 성별 판정
     ─ 전문용어 고정(terms.json) ─ 문장 단위 EN→KO 번역 (구글 / 막히면 로컬 NLLB-200 1.3B 자동 전환)
     ─ 용어집(glossary.json)으로 영문 고유명사를 한글 발음으로 ─ 발음 변환(speak/, MarkdownRadio 의 toSpoken)
     ─ Supertonic v3 TTS (남 M1~M5 / 여 F1~F5, 문장 길이에 맞춰 1.05~1.45배속)
     ─ 원본 음성을 깔고 한국어가 나올 때 줄이는 덕킹 믹스 ─ 오디오 2트랙(한국어 기본 / 원본) + 워터마크
```

TTS·번역·전사·화자분리가 전부 **로컬**에서 돌아가며 유료 API가 없습니다.

## 요구 사항

- **Windows 11 + NVIDIA GPU(권장)** 에서 검증했습니다. GPU가 없으면 몇 배~수십 배 느립니다. macOS/Linux 용 설치 스크립트는 없습니다(수동 설치 필요, 미검증).
- Python 3.12(도구가 가상환경을 만듭니다), ffmpeg(drawtext 필터 포함 빌드), Node.js 22.18 이상(발음 변환용).
- Hugging Face 토큰 + pyannote 약관 동의(화자분리용, **사용자가 직접**): <https://huggingface.co/pyannote/speaker-diarization-community-1>

## 설치

```powershell
git clone https://github.com/fkrn75/ko-dub "$HOME\ko-dub"
cd "$HOME\ko-dub"

# 1) 점검만 (아무것도 설치하지 않음)
powershell -ExecutionPolicy Bypass -File setup.ps1

# 2) 부족한 것 설치 + 모델 미리 받기 + 에이전트 스킬 설치 (수 GB, 10~30분)
powershell -ExecutionPolicy Bypass -File setup.ps1 -InstallMissing -DownloadModels -SetEnv -InstallSkill -HfToken <토큰>
```

`-InstallSkill` 은 설치된 에이전트 폴더(아래)에 스킬 복사본을 만들고, 복사본마다 이 폴더의 위치를 기록합니다.

| 에이전트 | 스킬 위치 | 호출 |
|---|---|---|
| Claude Code | `~/.claude/skills/ko-dub` | `/ko-dub` |
| Codex | `~/.codex/skills/ko-dub`, `~/.agents/skills/ko-dub` | `$ko-dub` 또는 `/skills` |
| Antigravity | `~/.gemini/config/skills/ko-dub`, `~/.agents/skills/ko-dub` | 스킬 이름을 부르거나 설명에 맞는 요청 |

스킬만 다시 설치/갱신: `py -3 install_skill.py` (`--list` 로 상태 확인).

## 사용

에이전트에게 말하면 됩니다.

> 이 영상 한국어로 더빙해줘 https://www.youtube.com/watch?v=... (720p)

직접 쓰려면 (`KODUB = py -3 kodub.py`):

```powershell
py -3 kodub.py check --net                       # 환경 점검
py -3 kodub.py download "<URL>" --out D:\v --name talk --work D:\w\talk --height 720 --detach
py -3 kodub.py step1 D:\v\talk.mp4 --work D:\w\talk --prompt "고유명사 나열" --detach
py -3 kodub.py wait  D:\w\talk step1
py -3 kodub.py speakers D:\w\talk                # 화자별 F0·성별 판정표
py -3 kodub.py translate D:\w\talk --detach      # 구글 → 막히면 NLLB 자동
py -3 kodub.py scan D:\w\talk                    # 번역 반복 오류 검사(필수)
py -3 kodub.py dry D:\w\talk                     # 화자→음성 배정, 남은 영문 토큰
py -3 kodub.py synth D:\w\talk --detach
py -3 kodub.py mux --video D:\v\talk.mp4 --work D:\w\talk --out "D:\v\talk (KO dub).mp4" --detach
```

오래 걸리는 단계는 `--detach` 로 분리 실행하고 `status` / `wait` 로 확인합니다(종료코드 0 성공, 1 실패, 2 사용법·환경 오류, 3 실행 중).

### 전문용어 고정

번역기는 전문용어를 일반 단어로 옮기기 쉽습니다(shadow map → "그림자 지도"). 그래서 번역 **전에** 용어를 표식(TQA…)으로 잠갔다가
번역 후 `terms.json` 의 한국어 용어로 되돌리고, 조사(을/를·은/는…)를 받침에 맞게 고칩니다. 기본 용어 100여 개가 들어 있고,
영상마다 에이전트가 `terms` 로 후보를 보고 채웁니다(사용자가 할 일 없음).

```powershell
py -3 kodub.py terms D:\w\talk                                   # 전사에서 용어 후보 추출(횟수·예문)
py -3 kodub.py glossary add --term "attenuation radius=감쇠 반경"   # 용어 추가
```

실측(Unreal MegaLights 강연 2,439문장, NLLB): shadow map "그림자 지도" 11/11 → "쉐도우 맵" 11/11,
ray traced 4가지 오역 → "레이 트레이싱", material "물질·재료" → "머티리얼", scene "장면·현장" → "씬". 표식이 사라져 잠금 없이 재번역된 문장은 2개.

### 워터마크

```powershell
py -3 kodub.py mux ... --watermark "내 문구" --wm-every 300 --wm-dur 8   # 문구·주기·표시 길이
py -3 kodub.py mux ... --no-watermark                                    # 끄기(영상 복사라 빠름)
```

워터마크를 켜면 영상을 다시 인코딩합니다(NVENC가 되면 GPU, 아니면 libx264).

## 검증 현황 (정직하게)

- Windows 11 + RTX 4060 Laptop 에서 영상 8편(7~166분)을 실제로 더빙했고, 깨끗한 가상환경 설치와 30초 합성 영상 종단 시험(전사→화자분리→번역→합성→워터마크 합치기)을 통과했습니다.
- **Codex·Antigravity 에서 스킬이 실제로 목록에 뜨고 실행되는지는 직접 확인하지 못했습니다.** 설치 위치와 형식은 각 공식 문서·설치본 내장 안내를 근거로 맞췄습니다.
- NLLB 번역은 직역투이고 가끔 같은 구절을 반복합니다(`scan` 으로 검출·교정). 구글 번역이 열려 있으면 그쪽이 품질이 더 좋습니다.
- 성별은 음높이로 추정하므로 낮은 목소리의 여성·높은 목소리의 남성은 틀릴 수 있습니다(경계 구간은 보고서에 "불확실"로 표시).

## 라이선스와 서드파티

이 저장소의 코드는 MIT 라이선스입니다(`LICENSE`). `speak/` 는 같은 저자의 [MarkdownRadio](https://github.com/fkrn75/MarkdownRadio) 에서 복사했습니다.
이 도구가 **내려받아 쓰는** 모델·패키지는 각자의 라이선스를 따르며, 사용 전에 직접 확인하세요.
특히 NLLB-200(Meta)은 비상업적 용도 제한이 있는 라이선스로 알려져 있고, pyannote 모델은 약관 동의가 필요합니다. 이 저장소는 모델 파일을 포함하지 않습니다.

## 구성

| 파일 | 역할 |
|---|---|
| `kodub.py` | 통합 명령줄 도구(모든 단계) |
| `skill/ko-dub/` | 에이전트 스킬 원본(`SKILL.md`, 런처, Codex 메타) |
| `setup.ps1` · `check_env.py` · `install_skill.py` · `prefetch_models.py` | 설치·점검·모델 받기·스킬 설치 |
| `step1~3_*.py` · `diarize_lite.py` · `scan_repeat.py` · `verify_ko.py` | 파이프라인 단계 |
| `terms.json` · `terms_lock.py` | 전문용어 고정(번역 전 잠금·복원·조사 교정·후보 추출) |
| `glossary.json` · `ko_fixes.json` | 영문→한글 발음 용어집, 번역 오역 교정 |
| `speak/` · `spoken.mjs` | 한국어 TTS 발음 변환(MarkdownRadio) |
| `export_kit.ps1` | 다른 PC로 옮길 zip 만들기 |
