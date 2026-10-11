# 에이전트 안내 (Codex · Antigravity · Claude Code 공통)

이 저장소는 영어 영상을 **화자별 남녀 음성의 한국어 더빙**으로 만드는 도구다. 더빙 요청을 받으면 `skill/ko-dub/SKILL.md`를 먼저 읽고 그 단계대로 `kodub.py` 명령만 써서 진행한다.

반드시 지킬 것:
- TTS는 **Supertonic v3**(`supertonic` 패키지, 모델 `Supertone/supertonic-3`)만 쓴다. edge-tts·gTTS·클라우드 TTS 등으로 대체하지 않는다.
- **화자분리(pyannote)와 화자별 성별 판정을 건너뛰지 않는다.**
- 번역은 `kodub.py translate`(구글 → 막히면 로컬 NLLB-200 1.3B, 전문용어 고정)로만 한다. Argos Translate·deep-translator 직접 호출·예전 `dub.py`로 바꾸지 않는다.
- 다른 더빙 스킬(edge-tts·`dub.py` 계열)이 함께 설치돼 있어도 이 도구를 쓴다.
- 환경이 준비되지 않았으면(`py -3 kodub.py check`에 `[없음] 필수`) 대체 구현을 만들지 말고, 부족한 것과 해결 명령(`setup.ps1`, `py -3 kodub.py models`)을 사용자에게 알린 뒤 허락을 받아 설치한다.
- Hugging Face 토큰과 pyannote 약관 동의는 사용자가 직접 한다(토큰을 채팅에 받지 않는다).
