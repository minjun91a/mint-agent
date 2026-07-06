# 로드맵 (Roadmap)

mint-agent의 Phase 진행을 추적하는 **단일 출처**. "지금 어디까지 왔나"는 이 파일에서 본다.
세부 결정은 [ADR](adr/), 빌드 기록은 [블로그](blog/) 참조.

> 범례: ✅ 완료 · 🔄 진행 중 · ⏭️ 다음 · ⬜ 예정

---

## ✅ Phase 0 — 정리 & 결정 *(완료 2026-06-18)*

- [x] 코드 위치·git 현황 검증 (3곳 분산 확인)
- [x] 결정 3가지 확정 → [ADR 0001](adr/0001-레포-전략-모노레포.md)·[0002](adr/0002-git-안전망-우선.md)·[0003](adr/0003-2번째-어댑터-research.md)
- [x] youtube-agent git 안전망 스냅샷 (`705c0d3`, 150파일, 비밀 0건)
- [x] 엔진/어댑터/Trust 전수 분류 → [inventory.md](inventory.md)
- [x] 개발 블로그 시작 → [blog/](blog/)

## 🔄 Phase 1 — 엔진/어댑터 경계 분리 *(진행 중)*

- [x] **선결: 동작 동일성 테스트 하니스** — pytest 골격(`tests/`, `pyproject.toml`), 원본 대조 parity 검증
- [x] `review` 단계 — `core/trust/verify.py`(Verdict/Violation/Reviewer 포트) + `adapters/youtube/review.py`(compliance 동작 동일 이식, 특성화 10건 + 원본 parity)
- [x] `produce/review/publish` 계약 골격 — `core/pipeline.py`(PlatformAdapter Protocol)
- [x] `produce` 단계 이식 — `captions`(자막 빌더, 특성화 18건) · `clip_extractor`(롱폼→숏폼, 특성화 9건), 각 원본 parity. 누적 테스트 37 green
- [x] 엔진 첫 조각 — `core/memory.py`(`ChannelMemory`, DB 경로 주입으로 위치 결합 제거; 특성화 14건 + 원본 parity). 누적 테스트 51 green
- [x] 통합 — `adapters/youtube/adapter.py`(`YouTubeAdapter` = `PlatformAdapter` 구현) + 엔진 발행 게이트 `core.pipeline.gate()`. **포트 분리 증명**: 엔진 게이트가 youtube 아닌 어댑터에도 동작(adapter-agnostic 테스트). 누적 58 green
- [x] `publish` 이식 — `adapters/youtube/publish.py`(`build_video_body` 순수 + `YouTubePublisher` 백엔드 주입). **첫 mock 패턴**(fake 백엔드, 외부 의존 0). `YouTubeAdapter.publish` 연결. 누적 72 green
- [x] `produce` LLM 모듈 첫 이식 — `adapters/youtube/seo.py`(순수 로직 + LLM 생성기 주입, 특성화 14건 + 순수 helper·fallback parity). 누적 86 green
- [x] **모델 게이트웨이 추출** — `core/llm.py`(`LLMGateway` 포트 + `GeminiGateway`). seo·review가 각자 반복하던 genai 호출(지연 import→configure→GenerativeModel→generate_content)을 **단일 seam**으로 통합. 부수효과: review의 LLM 심층 경로가 게이트웨이 주입으로 **결정적 테스트 가능**해짐(룰+LLM 병합·오류 안전화 4건 + 게이트웨이 특성화 8건). **Phase 2 비용 라우팅·관측의 연결점.** 누적 98 green
- [x] `produce` 스크립트 모듈 — `adapters/youtube/story_writer.py`(트로프→1분 영문 스크립트). **`OpenAIGateway` 추가(프로바이더 #2)** = 게이트웨이 포트 payoff 증명(같은 `complete` 포트가 Gemini·OpenAI에 동일 매핑, 엔진/어댑터 불변). 순수 `format_tropes` + `TropeLibrary`(경로·clock·rng 주입으로 결정적) + 게이트웨이 주입. 원본 parity(SYSTEM_PROMPT 3314자·포맷 IDENTICAL). 누적 114 green
- [x] `produce` 자산화 첫 모듈 — `adapters/youtube/image_gen.py`(FLUX→DALL-E 폴백·검열 3단 재시도). **`ImageBackend` 포트 + 백엔드 주입**(run/download/image_url을 주입해 폴백·검열 재시도를 결정적 테스트). 순수 `sanitize_prompt`/`enrich_prompt`/`dalle_attempt_prompts` 원본 parity(IDENTICAL). 특성화 29건. 누적 143 green
- [x] `produce` 자산화 — `adapters/youtube/video_builder.py`(음성+이미지+자막→쇼츠 mp4). **바이너리도 순수/IO 분리**: ffmpeg 인자 리스트를 순수 커맨드 빌더로 뽑고 실행은 `run` 주입 → 실 ffmpeg 없이 클립→concat→자막→mux 명령 시퀀스·실패·자막 cwd를 결정적 검증. 원본 SRT 스타일·ffmpeg 리터럴 parity(IDENTICAL). 특성화 16건. 누적 159 green
- [x] `produce` 자산화 — `adapters/youtube/thumbnail_gen.py`(배경 이미지 + Pillow 텍스트 오버레이, A/B 변형). 배경 생성기(`gen_bg`) 주입으로 실 이미지 API 없이 렌더·AB 분기·임시파일 정리 검증. `_VARIANT_STYLES`·`FONT_CANDIDATES` 원본 parity(IDENTICAL). 특성화 6건. 누적 165 green
- [x] `produce` 자산화 완결 — `adapters/youtube/tts_engine.py`(음성 합성). **세 주입 패턴 총동원**: 순수 `split_by_speaker` + `VoiceLibrary`(경로·rng 주입, 보이스 회전) + `SynthBackend`(ElevenLabs/OpenAI 주입·폴백) + 멀티보이스 ffmpeg(러너 주입). ffmpeg 2번째 사용 → 공유 seam **`_ffmpeg.py` 추출**(러너·길이 파싱; video_builder도 그 위로 재배선). `split_by_speaker` 원본 parity(IDENTICAL). 특성화 18건. 누적 183 green. **→ produce 전 단계(스크립트→음성→이미지→영상→썸네일) 모노레포 이식 완료**
- [x] 엔진 이식 — `control` 라우터: **inventory의 "경계 혼재" 해소**. 라우팅 메커니즘을 `core/control.py`(`IntentRouter` = 룰 + 게이트웨이 LLM 폴백, 플랫폼 무관)로, youtube intent 어휘·정규식 룰·arg 추출은 `adapters/youtube/control.py`로 분리. 룰 경로 원본 parity(IDENTICAL x15), 게이트웨이 재사용. 특성화 24건. 누적 207 green. (`dispatch`=액션 배선은 미이식 pipeline 의존 → orchestration 슬라이스로 미룸)
- [x] 엔진 이식 — `persona`: control과 같은 분리(엔진 `core/persona` = `Persona`·`PersonaRegistry`·`ask`[메모리·게이트웨이·agent_runner 주입] / 어댑터 `adapters/youtube/persona` = 페르소나 정의·Discord 채널 매핑). memory=`core/memory` 재사용, Gemini=게이트웨이(`json_mode=False`), claude=`agent_runner` seam(미이식 claude_runner 대체). **AXIS 언급 제거**(범위 제외 결정). 구조 parity(키·env맵·기본값 동일, AXIS 2→0). 특성화 17건. 누적 224 green
- [x] 엔진 이식 — `orchestrator`: 수집→LLM 브리핑 메커니즘을 `core/orchestrator`(수집기·게이트웨이·시계 **주입**, 소스별 실패 격리)로, youtube 수집(`collect_yt`, `build_report` 주입)·프롬프트는 `adapters/youtube/orchestrator`로 분리. **원본 AXIS 수집부(`collect_axis`, master_dashboard.db)·프롬프트 AXIS 언급 제거**(범위 제외; 원본 8참조→0). 특성화 11건. 누적 235 green. → **엔진 이식 골격 완료**(잔여: control `dispatch`)
- [ ] control `dispatch`(핸들러 주입 — 미이식 pipeline 배선)
- [ ] 2중 결합 분해 (Discord·YouTube를 어댑터 뒤로)

## 🔄 Phase 2 — 신뢰 레이어 1차 *(착수)*

- [x] **모델 게이트웨이 프로바이더 #3** — `core/llm.py`에 `ClaudeGateway` 추가(지연 anthropic, messages API 매핑, json_mode=system 지시, Windows 한글 헤더 회피 상속). Gemini·OpenAI·Claude가 같은 `complete` 포트 → 비용 라우터 기본 라우트(Opus/Sonnet/Haiku)가 실제 도달 대상을 가짐. (fake anthropic 매핑·strip·빈응답 폴백 테스트)
- [x] **복잡도 기반 비용 라우터** — `core/routing.py`. **`CostRouter`가 `LLMGateway`를 구현**해 기존 게이트웨이를 감싸는 드롭인(어댑터 코드 0줄 변경으로 라우팅·계측 삽입). 순수 `classify`(키워드·길이·json_mode → 티어+점수+근거) + `estimate_cost`/`DEFAULT_PRICING`(**운영 `claude_runner._PRICING`·`_estimate_cost`에서 상속** — 지어낸 수치 없음, 주입 가능) + `estimate_tokens`(근사, 실측 usage는 아래 과제) + `CallRecord`·`jsonl_sink`(경로·시계 주입)·`aggregate`(비용 리포트). 토이 라우트/가짜 게이트웨이로 **엔진 범용성 증명**(Claude 몰라도 동작). 특성화 32건. 누적 **267 green**
- [x] **파이프라인 관측 배선** — `core/gateway.py` 조립점(composition root) 추가. 어댑터·엔진의 7개 게이트웨이 생성점(`else GeminiGateway()/OpenAIGateway()`)을 `default_gemini()/default_openai()`(=`CostRouter` 관측 래핑)으로 교체 → 라이브 경로의 모든 LLM 호출이 토큰·티어·근거로 계측됨. **관측 전용**(명시 모델 안 덮음)이라 기존 동작 불변, 라우팅은 `model="auto"` 옵트인 시만. **비용 정직성**: 단가표에 있는 모델만 비용 계상, Gemini/OpenAI는 미가격(None) — 지어내지 않음(`is_priced`). 특성화 +10. 누적 **277 green**
- [ ] **Gemini/OpenAI 실단가 확보** → 가격표 주입으로 라이브 경로 비용 켜기(현재 미가격 None)
- [ ] **auto 라우팅 옵트인 경로** — 가변 복잡도 + 단가 있는(Claude) 호출 경로에 `model="auto"` 적용
- [ ] **실측 usage 연동** — 게이트웨이 포트가 provider usage(input/output tokens)를 노출 → `estimate_tokens` 근사 대체
- [ ] eval 하니스 (`compliance_audit_*.jsonl` → 자동 환각/위반 체크)
- [ ] 멀티모델 컨센서스 검증
- [ ] 관측성 로깅 → README 지표 테이블 실측값 채우기
- [ ] 발행 전 가드레일 정비

## ⬜ Phase 3 — 두 번째 어댑터 (research/digest)

- [ ] `adapters/research/` (소스 수집 → RAG 요약 → HITL → 발행)
- [ ] pgvector 도입
- [ ] **엔진 변경 0으로 어댑터 추가** = 추상화 증명

## ⬜ Phase 4 — 채용용 마감

- [ ] 아키텍처 다이어그램·지표·데모 GIF
- [ ] 이력서 문장 2종 (콘텐츠 자동화 / 지식워크 자동화)
