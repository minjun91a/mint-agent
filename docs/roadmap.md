# 로드맵 (Roadmap)

mint-agent의 Phase 진행을 추적하는 **단일 출처**. "지금 어디까지 왔나"는 이 파일에서 본다.
세부 결정은 [ADR](adr/), 빌드 기록은 [블로그](blog/) 참조.

> **개발이 끝나면** → [코드 리뷰 로드맵](review-roadmap.md). 만든 것을 처음부터 순차로 읽어
> 전부 파악하고 보완하는 별도 트랙(R0~R6). Phase 3 완주 시점에 발동한다.

> 범례: ✅ 완료 · 🔄 진행 중 · ⏭️ 다음 · ⬜ 예정

---

## ✅ Phase 0 — 정리 & 결정 *(완료 2026-06-18)*

- [x] 코드 위치·git 현황 검증 (3곳 분산 확인)
- [x] 결정 3가지 확정 → [ADR 0001](adr/0001-레포-전략-모노레포.md)·[0002](adr/0002-git-안전망-우선.md)·[0003](adr/0003-2번째-어댑터-research.md)
- [x] youtube-agent git 안전망 스냅샷 (`705c0d3`, 150파일, 비밀 0건)
- [x] 엔진/어댑터/Trust 전수 분류 → [inventory.md](inventory.md)
- [x] 개발 블로그 시작 → [blog/](blog/)

## ✅ Phase 1 — 엔진/어댑터 경계 분리 *(완료 2026-07-06)*

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
- [x] control `dispatch` — **핸들러 주입**. 엔진 `core.control.dispatch`(intent→핸들러 라우팅, 미주입 intent는 "미구현"으로 정직 응답)와 어댑터 `adapters/youtube/control.build_handlers`(intent→파이프라인 매핑, 파이프라인은 콜러블 주입; `discord=True` IO 결합은 콜러블 책임으로 제거)로 분리. 원본 arg 추출(no_upload·category/model·shorts·query/top) parity. 토이 핸들러로 엔진 범용성 증명. 특성화 +17. 누적 294 green
- [x] **2중 결합 분해 (Discord·YouTube를 어댑터 뒤로)** — YouTube(출력)는 이미 `YouTubeAdapter`=`PlatformAdapter`. Discord(IO)는 youtube persona 어댑터에 섞여 있던 **채널 정체성 매핑을 `adapters/discord/`로 분리**(`channels.channel_key_from_env` + `personas.resolve`). 의존 방향 IO→콘텐츠(discord→youtube)로 정리, youtube persona는 **Discord를 import 안 함**(콘텐츠 전용, resolver 주입). 엔진은 양쪽 다 모름(`PersonaRegistry`가 resolver 주입). 원본 env 매핑 parity. 특성화 +7. 누적 **301 green**. *(경계 분리 완료. 실제 봇 런타임 discord_bot·notifier·approvals 이식은 범위 밖 — 잔여 Discord 문자열: engineer 페르소나 prompt 언급·`perms_env_key`는 agent_perms 이식 시 정리)*

> **Phase 1 완료.** 엔진(`core/`)은 플랫폼(YouTube·Discord)을 모른 채 포트로만 의존하고, 플랫폼 로직은 어댑터에 격리됨을 테스트로 증명. produce 전 단계 + review/publish + 엔진 5모듈(llm·memory·pipeline·control·persona·orchestrator) + 게이트웨이 3프로바이더 + 비용 라우터(Phase 2 선행 착수) 이식 완료.

## 🔄 Phase 2 — 신뢰 레이어 1차 *(코드 항목 완료 2026-07-10 · daily-drill만 범위 밖)*

- [x] **모델 게이트웨이 프로바이더 #3** — `core/llm.py`에 `ClaudeGateway` 추가(지연 anthropic, messages API 매핑, json_mode=system 지시, Windows 한글 헤더 회피 상속). Gemini·OpenAI·Claude가 같은 `complete` 포트 → 비용 라우터 기본 라우트(Opus/Sonnet/Haiku)가 실제 도달 대상을 가짐. (fake anthropic 매핑·strip·빈응답 폴백 테스트)
- [x] **복잡도 기반 비용 라우터** — `core/routing.py`. **`CostRouter`가 `LLMGateway`를 구현**해 기존 게이트웨이를 감싸는 드롭인(어댑터 코드 0줄 변경으로 라우팅·계측 삽입). 순수 `classify`(키워드·길이·json_mode → 티어+점수+근거) + `estimate_cost`/`DEFAULT_PRICING`(**운영 `claude_runner._PRICING`·`_estimate_cost`에서 상속** — ⚠️ *당시 "지어낸 수치 없음"이라 적었으나, 상속한 `opus-4-7` 단가가 3배 틀렸음이 나중에 드러난다 → 아래 "실단가 확보 + 상속한 단가 오류 교정" 참조*) + `estimate_tokens`(근사, 실측 usage는 아래 과제) + `CallRecord`·`jsonl_sink`(경로·시계 주입)·`aggregate`(비용 리포트). 토이 라우트/가짜 게이트웨이로 **엔진 범용성 증명**(Claude 몰라도 동작). 특성화 32건. 누적 **267 green**
- [x] **파이프라인 관측 배선** — `core/gateway.py` 조립점(composition root) 추가. 어댑터·엔진의 7개 게이트웨이 생성점(`else GeminiGateway()/OpenAIGateway()`)을 `default_gemini()/default_openai()`(=`CostRouter` 관측 래핑)으로 교체 → 라이브 경로의 모든 LLM 호출이 토큰·티어·근거로 계측됨. **관측 전용**(명시 모델 안 덮음)이라 기존 동작 불변, 라우팅은 `model="auto"` 옵트인 시만. **비용 정직성**: 단가표에 있는 모델만 비용 계상, Gemini/OpenAI는 미가격(None) — 지어내지 않음(`is_priced`). 특성화 +10. 누적 **277 green**
- [x] **실단가 확보 + 상속한 단가 오류 교정** *(2026-07-10)*. Gemini(2.5-flash·flash-lite·pro)·
  OpenAI(gpt-4o-mini)·Claude 신세대(opus-4-8·sonnet-5) 단가를 **프로바이더 공식 문서로 직접 확인**해
  `DEFAULT_PRICING`에 등록 → 라이브 경로 비용이 켜졌다(이전엔 전부 미가격 None).
  **★ 결함 교정**: 운영 `claude_runner._PRICING`에서 상속한 `claude-opus-4-7`이 **15.00/75.00**이었는데
  실제 공시가는 **5.00/25.00**이다(구세대 Opus 4.1 단가가 남아 있던 것). 🟢 100k in/20k out 호출 기준
  `$3.00 → $1.00`, **정확히 3배 과대계상**하고 있었다. roadmap·코드 주석은 이 값을 "출처가 붙은 값,
  지어낸 수치 없음"이라고 적어 두었다 — **출처가 있다는 것과 정확하다는 것은 다르다.** 상속한 수치도
  검증 대상임을 회귀 테스트로 고정.
  **컨텍스트 티어링**: `gemini-2.5-pro`는 프롬프트 200k 토큰 초과 시 단가가 인상된다(1.25/10.00 →
  2.50/15.00) → `estimate_cost`가 입력 토큰으로 티어를 고른다(`_resolve_price`). 단 입력이 근사
  (`estimate_tokens`)일 땐 임계값 판정도 근사 — `token_source`가 그 구분을 남긴다.
  표에 없는 모델은 여전히 미가격(None). 특성화 +9. 누적 **552 green**
- [x] **auto 라우팅 옵트인 경로 + 프로바이더 라우트 불일치 교정** *(2026-07-10)*. `model="auto"`를
  넘기면 복잡도로 티어를 골라 호출한다(기본은 여전히 관측 전용 = 명시 모델 통과).
  **★ 잠복 버그 발견**: 세 팩토리(`default_gemini/openai/claude`)가 전부 `DEFAULT_ROUTE`(**Claude 모델**)를
  쓰고 있었다. 관측 전용이라 아무도 `auto`를 넘기지 않아 드러나지 않았지만, 🟢 재현 결과
  `default_gemini().route_for(..., model="auto")` → **`claude-haiku-4-5-20251001`**. auto를 켜는 순간
  Claude 모델명이 Gemini SDK로 넘어간다. *이 슬라이스가 그 버그를 드러냈다.*
  **교정**: 조립점이 프로바이더별 라우트를 붙인다 — `GEMINI_ROUTE`(flash-lite→flash→pro,
  같은 SDK 안에서 티어링) · `CLAUDE_ROUTE`(haiku→sonnet→opus, `default_claude()` 신설) ·
  `OPENAI_ROUTE = {}`(쓰는 모델이 `gpt-4o-mini` 하나뿐 → **없는 라우트를 지어내지 않는다**;
  `auto`는 명시적 `ValueError`, 명시 모델은 그대로 통과). 엔진 `route_for`도 티어 미보유 시
  조용히 넘어가지 않고 즉시 알린다. 라우트의 모든 모델은 실단가 보유(테스트로 강제).
  🟢 절감폭(10k in/2k out): flash-lite `$0.0018` vs pro `$0.0325` — **18배**. 특성화 +14. 누적 **566 green**
- [x] **실측 usage 연동** — `core/llm.py` 하위호환 포트 확장: `complete_ex(...) -> Completion(text, usage)` + `UsageAwareGateway` 프로토콜. 3 게이트웨이가 provider usage 추출(Gemini `usage_metadata`, OpenAI/Claude `usage`), `complete`는 `complete_ex().text`로 위임(기존 호출부 불변). `CostRouter`가 usage-aware 게이트웨이면 **실측 토큰**, 아니면 문자수 근사로 폴백 → `CallRecord.token_source`("measured"|"estimated")로 정직 라벨, `aggregate.measured_calls` 집계. usage 미노출이면 None(안 지어냄). 특성화 +11. 누적 **345 green**
- [x] **eval 하니스** — `core/trust/eval.py`. 발행 게이트 결정 로그(`compliance_audit_*.jsonl`)를 읽어 **집계 지표**(`summarize`: 통과율·권고 분포·점수/위반 통계·채널별)와 **자체 모순 감사**(`check_invariants`: 정답 라벨 없이 게이트가 자기 규칙과 모순되지 않는지 — `reject⟹¬passed`·`approve⟹passed`·`score∈[0,1]`, 원본 compliance 설계 근거)로 변환. 경로 주입(실 로그는 외부), 합성 레코드/임시 파일로 결정적 테스트 17건. 누적 **318 green**. **실 로그 검증(🟢 관측, 2026-07-06)**: 36건 중 pass_rate 2.78%, agreement consensus 15/disagree 21, **모순 7건 검출**(전부 `score_range` — LLM 심층 경로가 점수를 정규화 안 해 score=40 등 스펙[0,1] 위반 → 실 데이터 품질 결함을 하니스가 잡음)
- [x] **eval 후속 — 게이트 결함 교정** — eval이 검출한 score-range 결함을 `adapters/youtube/review.py` LLM 심층 경로에 **클램프**(score→[0,1]) + recommendation enum 검증(미확인→review 안전화)으로 반영. **원본과 의도적 차이**(원본 미정규화 점수를 계약에 맞게 교정). **find→fix→verify 루프 닫힘**: 교정된 Verdict가 `check_invariants`를 통과하는 테스트로 재확인. 특성화 +4. 누적 **322 green**
- [x] **eval 후속 — 검출 패턴을 규칙 후보로(엔진 절반)** *(2026-07-10)*.
  **★ 발견한 구멍 ③**: 감사 로그가 위반 **개수만** 남기고 **어떤 규칙이 터졌는지**를 버리고 있었다.
  🟢 원본 로그 40건 키 전수 집계 = `{ts, channel, title, passed, score, recommendation,
  agreement, violation_count}` — 카테고리 없음. **우리가 버린 게 아니라 원본도 없었다.** 그래서
  원본도 이식본도 "가장 많이 터지는 규칙은?"을 로그로 답할 수 없었고, *검출 패턴 → 규칙 후보*가
  스키마에서 끊겨 있었다. `audit_record`가 `violations`(카테고리·심각도)를 남기게 하고
  (`detail`은 **의도적 제외** — 콘텐츠 조각이 감사 로그에 남으면 안 된다), `eval.rule_candidates`가
  반복 패턴을 후보로 올린다. **하위호환**: `violations is None`(미기록) ≠ `()`(위반 없음) —
  뭉개면 구 로그 40건이 전부 '위반 0건'이 되고 신규 불변식 2개가 40건을 오탐한다(🟢 재검증: 오탐 0).
  **★ 동시발생 ≠ 인과**: `always_rejected`만 보면 high 위반 옆에 업혀 뜬 med 규칙이 승격 후보로
  둔갑한다. `solo_count`(그 규칙이 **유일한 위반**이던 결정 수)가 그 구분을 진다 —
  🟢 데모에서 `citation_out_of_range`만 승격 후보, `no_citation`은 high인데도 관찰(단독 0).
  후보는 **제안일 뿐 자동 적용하지 않는다.** 특성화 +42. 누적 **641 green**
- [ ] eval 후속 — [[daily-drill]] 검증규칙 파이프 연결(detection producer 배선).
      *엔진 쪽은 위에서 완료. 남은 것은 외부 입력 연결뿐 — 본 트랙 범위 밖,
      mint-agent 완성 후 사용자가 처음부터 끝까지 직접 진행(2026-07-10 결정).*
- [x] **멀티모델 컨센서스 검증** — `core/trust/consensus.py`. 같은 콘텐츠를 N개 리뷰어로 교차검증 → 순수 `combine`(보수적 결합: reject 우선·전원 통과여야 passed·score 최댓값·위반 병합 + `agreement` consensus/disagree) + `run`(리뷰어 thunk 실행, 실패는 보수적 review로 격리). 리뷰어·모델 주입(엔진은 youtube·모델수 무지). **정직성**: 원본 `double_check` 소스가 youtube-agent에 부재 → *이식이 아니라 재구성*(단 `agreement` 의미는 실 로그에서 관측, 보수적 결합은 프로젝트 원칙에서 도출). 특성화 12건. 누적 **334 green**
- [x] **관측 대시(리포트 렌더러)** — `core/observability.py`. 계측 데이터를 사람이 읽는 Markdown으로: `render_cost_report`(CallRecord→호출·비용·**실측/근사 비율**·미가격·티어/모델별 표) + `render_gate_report`(AuditRecord→통과율·권고/합의 분포·점수·**모순 건수**) + `dashboard`(묶음). 순수·결정적 테스트 10건. **실 로그 렌더(🟢)**: 게이트 감사 36건 → 통과율 2.8%·reject35/review1·disagree21/consensus15·모순 7건(score_range)까지 legible. 누적 **355 green**
- [x] **발행 전 가드레일 정비** *(2026-07-10)*. `core/pipeline.py`. 게이트에 세 가지를 더했다.
  **★ 발견한 구멍 ①**: `gate()`가 **아무 기록도 남기지 않았다.** 그래서 `core/trust/eval`은 *원본 운영
  로그*만 감사할 수 있었고 — **우리가 만든 게이트는 감사 대상이 아니었다.** `audit_record`(순수, `AuditRecord`
  호환) + `jsonl_audit_sink`(경로·시계 주입) 추가 → 🟢 `gate → jsonl → eval.evaluate →
  observability.render_gate_report` 관통 확인(모순 감사 0건). **find→fix→verify 루프가 우리 시스템 안에서 닫혔다.**
  **★ 발견한 구멍 ②**: `core/trust/consensus`를 **발행 경로에서 아무도 부르지 않았다**(엔진에만 존재).
  `gate_consensus(reviewers)` 신설 — 리뷰어는 thunk 주입(엔진은 몇 개 모델이 도는지 모름), 한 명이라도
  거절하면 보류, 갈리면(`disagree`) 사람에게, 리뷰어가 터져도 그 표를 보수적 `review`로 격리, **빈 리뷰어
  목록은 통과시키지 않는다**(아무도 검증 안 한 것). 불일치는 감사 로그의 `agreement`에 남는다.
  **③ HITL 지점 명시**: `requires_human`/`rejected` — `proceed=False`만으로는 **거절과 보류가 구분되지
  않았다.** 보류만 사람에게 올린다. 단일 리뷰어 경로의 `agreement`는 빈 값(없는 신호를 지어내지 않음).
  감사 실패는 게이트를 막지 않는다(관측은 주 경로를 막지 않는다). 특성화 +22. 누적 **588 green**

> **Phase 2 코드 항목 완료.** 남은 것은 외부 입력이 필요한 [[daily-drill]] 검증규칙 파이프뿐
> (엔진은 `rule_candidates`로 그 입력을 받을 준비가 끝났다).

## ✅ Phase 3 — 두 번째 어댑터 (research/digest) *(완료 2026-07-10)*

- [x] **`produce` 이식** — `adapters/research/`(`_http`·`search`·`fetch`·`summarize`·`adapter`).
  원본 `youtube-agent/agents/research.py`(DDG 검색 → 본문 페치 → Gemini 출처인용 요약)를 순수/IO로 갈라
  이식했다. **순수**(DDG 정규식 파싱·리다이렉트 해소·태그 제거·문자 예산 배분·프롬프트 조립)는
  **원본 parity IDENTICAL**(정규식 6·상수 5·순수함수 4종 대조, `_clean`은 304 fuzz 샘플).
  **IO**는 `HttpFetcher` 백엔드 주입 — `search`·`fetch` 두 곳이 urlopen을 쓰므로 `_http.py` 공유 seam으로
  추출(`_ffmpeg.py`와 같은 근거). **LLM**은 `LLMGateway` 주입(`default_gemini()` 조립점 → 관측 자동 승계).
  `ResearchAdapter`가 `PlatformAdapter` 포트를 만족. **★ 엔진 변경 0줄**(`git diff -- core/` 공백)
  = [ADR 0003](adr/0003-2번째-어댑터-research.md) 명제 1차 증명. 특성화 53건. 누적 **408 green**
- [x] **엔진 결함 교정 — 산문 경로의 JSON 폴백 누수** *(2번째 어댑터가 폭로)*. `core/llm.py`의 빈 응답
  폴백 `_EMPTY_JSON="{}"`이 `json_mode`와 무관하게 3 게이트웨이 전부에 적용되고 있었다. 지금까지
  호출부가 모두 JSON을 기대해 아무도 안 밟았지만, 산문을 기대하는 호출부는 이미 넷이다
  (`core/orchestrator` ×2 · `core/persona` · 새 `research/summarize`) → 모델이 빈 응답을 내면
  **사용자가 리서치 요약으로 `"{}"`를 본다**(재현 확인). `_empty_fallback(json_mode)`로 교정:
  JSON 요청엔 `"{}"`, 산문 요청엔 `""`(원본 research의 `(resp.text or "")`와도 일치).
  **find→fix→verify 루프**(eval 결함 교정 `8fc909f`와 같은 리듬). 특성화 +5. 누적 **413 green**
- [x] **`review` — 출처 인용 검증 + 예고된 계약 정제**. `adapters/research/review.py`(`CitationReviewer`).
  **원본에 대응물 없음 → 신규 구축**(consensus와 같은 정직 표기). 규칙의 근거는 `summarize.SYSTEM_PROMPT`가
  모델에게 **약속시킨 것들**이다 — *프롬프트가 계약이고, review는 그 계약의 테스트*. 룰 층은 **완전히
  결정적**(LLM 불필요): `citation_out_of_range`(존재하지 않는 출처 인용=환각) · `citation_unfetched_source`
  (페치 실패해 요약 입력에 없던 소스를 인용) · `no_citation` · `missing_sources_section`(med) ·
  `unused_source`(low). `deep=True` + 근거 본문(`context={"bodies":...}`) 주입 시에만 LLM 근거 대조.
  **정책은 youtube와 다르다**(어댑터 자율): high → `reject`(사람 안 기다림), med/low → `review`, 무위반 → `approve`.
  **★ 계약 정제 실행**: `PlatformAdapter.review(content, seo)` → `review(content, context=None)`.
  `core/pipeline.py`·`core/trust/verify.py`가 "2번째 어댑터가 강제할 때 정제한다"고 예고해 둔 지점을 실행 —
  `seo`는 youtube 어휘였고 리서치엔 SEO가 없다. `context` = "검증에 필요한 부가 자료, 해석은 어댑터의 몫"
  (youtube=SEO 메타, research=근거 본문). 엔진은 그 안을 보지 않는다. `core/trust/verify.py`는 **한 줄도 안 바뀜**
  (결과 어휘만 공유하는 얕은 설계가 옳았음을 2번째 어댑터가 검증). **정제의 파장**: `context`가 optional이 되자
  `gate(adapter, content)`가 가능해졌고 youtube가 `None.get`으로 깨졌다 → 어댑터에서 `seo or {}` 방어 + 회귀 테스트.
  특성화 +34. 누적 **447 green**
- [x] **`publish`** — `adapters/research/publish.py`. youtube publish와 같은 두 층 분리:
  순수 렌더(`slugify`·`build_meta`·`render_digest`) + `DigestSink` **백엔드 주입**(`FileDigestSink`는
  경로 주입, 시계 주입으로 결정적). **원본에 발행 단계 없음 → 신규 구축**. **정직성 두 가지**:
  ① 발행물이 **페치 실패를 숨기지 않는다**(수집 정보 표에 "❌ 페치 실패" — `review`의
  `citation_unfetched_source`가 보는 것과 같은 신호). ② **덮어쓰기 안전** — 발행은 되돌리기 어려운
  외부 행위라 기본적으로 덮어쓰지 않고 `-2`·`-3` 접미사를 붙이며, 어댑터는 기본 sink를 몰래 만들지 않고
  **명시적 배선을 요구**한다. **3단 계약 관통 테스트**: `produce → gate → publish`에서 게이트가
  통과시킨 다이제스트만 발행되고, 환각 인용(`[출처 9]`)이면 파일이 생기지 않음을 고정. 특성화 +24.
  누적 **471 green**
- [x] **RAG + pgvector** — 엔진 `core/embedding.py`(`EmbeddingGateway` 포트 + `GeminiEmbedder`·`OpenAIEmbedder`,
  `LLMGateway`와 같은 모양) + `core/retrieval.py`(순수 `chunk_text`·`chunk_documents`·`cosine`·SQL 빌더 /
  `VectorStore` 포트 / `InMemoryVectorStore` 결정적 구현 / `PgVectorStore`) + 어댑터 `adapters/research/rag.py`
  (`RagSelector` — 요약 전에 본문을 질의 관련 발췌로 좁힘, **옵트인**).
  **★ 정직 표기 ①**: 이건 *어댑터가 강제한 엔진 변경이 아니다*. research는 이미 엔진 0줄로 붙었고
  produce·review·publish가 그 상태로 돈다. RAG는 **새 엔진 역량**이며 플랫폼을 모른다(토이 문서로 검증).
  **★ 정직 표기 ②**: `PgVectorStore`는 **연결 객체를 주입**받으므로 이 모듈에 psycopg 의존이 없다
  (`GoogleUploadBackend`와 같은 패턴) → 가짜 연결로 SQL·바인딩을 고정. 🔴 **실 Postgres 왕복은 미검증**
  (로컬 DB 미배선) — 지어내지 않는다. 테이블명은 SQL 보간 지점이라 화이트리스트 검증.
  **인용 무결성 불변식**: 선택기는 본문과 **같은 길이·같은 순서**를 돌려주고 `Chunk.source_id`가 1-based
  문서 번호라 `[출처 N]`이 어긋나지 않는다(2번 문서가 탈락해도 3번은 `[출처 3]`). RAG 실패·임베더 미가용은
  원본 본문으로 폴백. 특성화 +72. 누적 **543 green**
- [x] **엔진 변경 0으로 어댑터 추가 = 추상화 증명** — produce·review·publish 3단 모두 달성.
      *유일한 엔진 변경은 예고된 계약 정제(`seo` → `context`)였고, `core/trust/verify.py`는 한 줄도 안 바뀌었다.*

> **Phase 3 완료.** 도메인이 전혀 다른 두 번째 어댑터가 붙었고, 그 과정에서 엔진의 숨은 가정을 두 번
> 폭로했다(산문 경로 JSON 폴백 누수 `c3a30d1` · `review`의 youtube 어휘 `6854f19`). 예고해 둔 자리에서만
> 계약이 정제됐다는 것이 설계가 옳았다는 증거다.

## ⬜ 리뷰 라운드 — 개발 완료 후 순차 코드 리뷰 *(Phase 3 완주 시 발동)*

- [ ] R0 지도 그리기(`CODE_MAP.md`) · R1 계약 계층 · R2 프로바이더·비용 · R3 엔진 메커니즘 ·
      R4 신뢰 레이어 · R5 어댑터 · R6 보완 → 통과 후 **push**
- 프로토콜·예측 드릴·기록 형식 → [review-roadmap.md](review-roadmap.md)

## 🔄 Phase 4 — 채용용 마감 *(착수 2026-07-10)*

- [x] **데모 진입점** — `demo.py`. 이 저장소엔 **실행 가능한 진입점이 하나도 없었다**(라이브러리 + 테스트뿐).
  데모·지표·README가 전부 여기에 의존하므로 먼저 만들었다. **API 키·네트워크 없이** 도는 조각만 보여준다:
  발행 게이트(룰) · 환각 인용 검증(LLM 불필요) · 복잡도 라우팅 미리보기 · 컨센서스 게이트 ·
  **감사 루프 관통**(게이트 → JSONL → eval → 리포트). 실 LLM 호출·업로드·임베딩은 키가 필요하므로
  넣지 않았다 — *없는 것을 있는 척하지 않는다.* 🟢 SDK 차단 환경에서도 `exit 0`.
  스모크 테스트 9건(`tests/test_demo.py`)으로 CI가 매번 검증 — README가 약속한 출력이 조용히 깨지지 않는다.
- [x] **실측 지표** — [metrics.md](metrics.md). 모든 숫자에 **측정 명령**을 붙였다.
  테스트 597 · 테스트:프로덕션 코드 ≈ 1:1 · 커밋 56 · 프로바이더 3 · 어댑터 3.
  **엔진 0줄 증명**(`git show --stat 38473b5 -- core/` → 출력 없음), **비용 절감 18배**,
  **검출·교정한 결함 5건**(실 로그 감사 1 + 재현 4)을 커밋 해시와 함께 표로. "아직 아닌 것" 섹션 포함.
- [x] **아키텍처 다이어그램** — 기존 mermaid는 *"목표 아키텍처(비전)"* 캡션이 붙은 라우팅 흐름도였다.
  실제 모듈 의존 그래프(어댑터 → 엔진, 단방향)와 발행 경로 다이어그램으로 교체하고, 🟢구현/🔴미배선을 표기.
- [ ] 블로그 #10~ (Phase 3 · Phase 2 결함들) — 트랙 유지
- [ ] **이력서 문장 2종** (콘텐츠 자동화 / 지식워크 자동화) — ⏭️ **리뷰 라운드(R0~R6)로 코드를 전부
      파악·보완한 뒤에 쓴다**(사용자 결정 2026-07-10). 설명할 수 없는 코드를 이력서에 적지 않는다.

> **데모 GIF 대신 실제 출력 캡처.** GIF는 생성 도구 설치가 필요하고, 코드가 바뀌면 조용히 낡는다.
> 텍스트 출력은 GitHub에서 검색·복붙되고, **CI가 그 출력을 매번 검증**한다(`tests/test_demo.py`).
