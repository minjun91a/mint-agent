# 코드 리뷰 로드맵 (Review Roadmap)

> **발동 조건: 개발 완료 후.** Phase 3(research 어댑터) 완주 시점에 이 문서를 열고 R0부터 순서대로 진행한다.
> 개발 로드맵이 "무엇을 만들 것인가"라면, 이 문서는 **"만든 것을 어떻게 내 것으로 만들 것인가"**다.
>
> 목표는 암기가 아니다. **AI가 쓴 코드를 읽고, 틀린 걸 잡아내고, 왜 이 구조인지 설명하는 능력**이다.
> 진행 추적은 [roadmap.md](roadmap.md), 결정 기록은 [adr/](adr/), 빌드 서사는 [blog/](blog/).

---

## 0. 원칙 (변경 금지)

1. **리뷰에서 나온 보완은 리뷰를 통과해야 push한다.** 개발이 끝난 코드는 Phase 단위로 push하되
   (Phase 3는 `c79b3fc`에서 push됨), 리뷰 라운드가 만든 수정·보완 커밋은 그 라운드를 마친 뒤 나간다.
   *원안은 "리뷰 통과 전 push 금지"였으나, 사용자 결정(2026-07-10)으로 Phase 완료 시 push로 조정.*
2. **순차 리뷰. 무작위 열람 금지.** 의존성 바닥(계약·포트)부터 위(어댑터)로 올라간다. 아래 R0~R6 순서.
3. **predict-before-run.** 함수를 읽었으면 실행 결과를 **먼저 예측**하고, 그 다음 실행해 대조한다.
   예측이 빗나간 지점이 그날의 드릴이다.
4. **대신 설명받지 않는다.** 에이전트는 내가 예측·이해를 말하기 전에 정답을 주지 않는다.
   막히면 힌트까지만. (명시적으로 "그냥 설명해줘"라고 하면 예외)
5. **[모름]을 숨기지 않는다.** 모르는 모듈은 `[모름]`으로 적는다. 그 목록이 곧 작업 목록이다.
6. **리뷰 중 발견한 결함은 그 자리에서 닫는다.** find → fix → verify(테스트로 고정). 단 **신규 기능은 금지** —
   리뷰 라운드에서 하는 것은 이해와 보완뿐이다.

> **방침 변경 기록(2026-07-10, 사용자 결정).** 이전 초안은 "소급 전체 리딩을 하지 않는다"였다.
> 현재 방침은 **개발 완료 후 첫 단계부터 전체를 순차 리딩**하는 것으로 대체됐다. 무작위 소급 리딩을
> 금지하는 정신은 유지되며, 순서(R0~R6)가 그 통제 장치다.

---

## R0 — 지도 그리기 *(에이전트 개입 금지)*

**산출물:** `docs/CODE_MAP.md`

1. 저장소 트리를 펴놓고 각 모듈이 뭘 담당하는지 **내 손으로** 한 줄씩 쓴다. 설명 못 하는 건 `[모름]`.
2. 아래 **설계상 핵심 경로**의 파일만 연다. 다른 파일은 열지 않는다.
3. 다 쓴 뒤에만 에이전트에게: *"내가 쓴 CODE_MAP의 틀린 부분만 지적해줘. 대신 써주지 마."*

### 설계상 핵심 경로 (⚠️ 실배선은 아직 없음)

```
자연어 명령
  → core.control.IntentRouter.route      (룰 + LLM 폴백)
  → core.control.dispatch                (intent → 핸들러)
  → adapters.youtube.control.build_handlers   (핸들러 → 파이프라인 콜러블 주입)
  → core.gateway.default_gemini()        (조립점: CostRouter 관측 래핑)
  → core.routing.CostRouter.complete     (복잡도 분류 · 계측)
  → core.llm.GeminiGateway.complete_ex   (프로바이더 호출 · 실측 usage)
  → adapters.youtube.review.ComplianceReviewer.review  → core.trust.verify.Verdict
  → core.pipeline.gate                   (approve만 통과)
```

> 🟢 **정직 표기:** `core.pipeline.gate()`를 호출하는 **프로덕션 코드는 아직 없다**(테스트만 호출).
> 봇 런타임(discord_bot·notifier·approvals)이 미이식이라 end-to-end 배선이 끊겨 있다.
> 위는 **설계상** 경로다. 리뷰 중 이 사실을 직접 확인하는 것(`grep -rn "gate("`)이 R1의 첫 드릴이다.

### CODE_MAP.md 템플릿

```markdown
# CODE_MAP
최종 갱신: YYYY-MM-DD

## 모듈 지도
| 경로 | 담당 역할 (내 이해) | 상태 |
|------|--------------------|------|
| core/routing.py | 복잡도 판정 후 티어 라우팅 + 호출 계측 | 이해함 |
| core/persona.py | | [모름] |

## 핵심 경로 추적
- `함수명()` : 역할 한 줄

## 블랙박스 목록 (면접 질문 확률 순)
1. [모름] 모듈명 — 상/중/하
```

> 우선순위 기준: **신뢰 레이어 · 라우팅 = 1순위.** 유틸성 코드는 후순위.

---

## R1 — 계약 계층 (포트와 어휘)

의존이 가장 얕은 곳. 이 세 파일이 "엔진이 무엇을 강제하는가"를 정의한다.

| 파일 | 읽을 것 |
|---|---|
| [core/trust/verify.py](../core/trust/verify.py) | `Violation` · `Verdict` · `Reviewer` |
| [core/pipeline.py](../core/pipeline.py) | `PlatformAdapter` · `GateDecision` · `gate` |
| [core/llm.py](../core/llm.py) | `LLMGateway` · `UsageAwareGateway` · `Completion` · `Usage` (구현체는 R2) |

**예측 드릴** (실행 전 답을 적을 것)
- `recommendation="review"`인 `Verdict`를 `gate()`에 넣으면 `proceed`는?
- `_empty_fallback(json_mode=False)`의 반환은? `json_mode=True`면?
- `Reviewer.review`의 시그니처가 왜 `*args, **kwargs`인가? ([verify.py:9-11](../core/trust/verify.py#L9-L11) 주석이 답을 예고한다)

**완료 조건**: "이 포트가 무엇을 강제하고 **무엇을 일부러 강제하지 않는지**"를 3줄로 쓸 수 있다.

---

## R2 — 프로바이더와 비용 (신뢰 레이어 하부)

| 파일 | 읽을 것 |
|---|---|
| [core/llm.py](../core/llm.py) | `GeminiGateway` · `OpenAIGateway` · `ClaudeGateway` · `_usage_from` · `_empty_fallback` |
| [core/embedding.py](../core/embedding.py) | `EmbeddingGateway` · `_as_vectors` · `GeminiEmbedder` · `OpenAIEmbedder` |
| [core/routing.py](../core/routing.py) | `Tier` · `classify` · `Classification` · `estimate_cost` · `is_priced` · `estimate_tokens` · `CallRecord` · `jsonl_sink` · `aggregate` · `AUTO` · `CostRouter` |
| [core/gateway.py](../core/gateway.py) | `set_observer` · `wrap` · `default_gemini` · `default_openai` |

**예측 드릴**
- `classify("", "이 아키텍처를 분석·리팩터해줘")`의 `Tier`는? `classify("", "요약해줘")`는?
- `is_priced("gemini-2.5-flash")`는? 그 결과가 `CallRecord.cost_usd`에 어떻게 나타나는가?
- usage를 노출하지 않는 게이트웨이를 `CostRouter`가 감싸면 `token_source`는 무엇이 되는가?
- `default_gemini()`가 반환하는 객체의 **타입**은? (`GeminiGateway`가 아니다 — 왜 그래도 호출부가 안 바뀌는가?)

**완료 조건**: "`CostRouter`가 왜 `LLMGateway`를 **구현**하는가"를 설명할 수 있다. (드롭인 데코레이터)

---

## R3 — 엔진 메커니즘

"메커니즘은 엔진, 정의는 어댑터" 분리가 실제로 어떻게 생겼는지 보는 라운드.

| 파일 | 읽을 것 |
|---|---|
| [core/memory.py](../core/memory.py) | `ChannelMemory` (경로 주입) |
| [core/control.py](../core/control.py) | `Command` · `Rule` · `IntentRouter` · `describe` · `dispatch` |
| [core/persona.py](../core/persona.py) | `Persona` · `PersonaRegistry` · `ask` |
| [core/orchestrator.py](../core/orchestrator.py) | `collect_sources` · `build_briefing` · `answer` · `run_brief` |
| [core/retrieval.py](../core/retrieval.py) | `chunk_text` · `chunk_documents` · `cosine` · `VectorStore` · `InMemoryVectorStore` · `PgVectorStore` · `Retriever` |

**예측 드릴**
- `dispatch`에 **핸들러가 주입되지 않은** intent를 주면 반환은?
- `collect_sources`에서 수집기 하나가 예외를 던지면 나머지 소스는 어떻게 되는가?
- `PersonaRegistry`가 `channel_key=None`을 받으면?
- `chunk_text("abcdefghij", size=4, overlap=1)`은 몇 조각이고 각각 무엇인가?
- `chunk_documents(["a", "", "c"])`에서 "c"의 `source_id`는? 그 선택이 `[출처 N]`과 무슨 관계인가?
- `PgVectorStore`는 왜 psycopg를 import하지 않는가? `GoogleUploadBackend`와 무엇이 같은가?

**완료 조건**: 각 모듈에서 "엔진에 남은 것"과 "어댑터로 간 것"을 한 쌍씩 지목할 수 있다.

---

## R4 — 신뢰 레이어 상부

| 파일 | 읽을 것 |
|---|---|
| [core/trust/eval.py](../core/trust/eval.py) | `AuditRecord` · `load_jsonl` · `load_dir` · `summarize` · `check_invariants` · `evaluate` |
| [core/trust/consensus.py](../core/trust/consensus.py) | `combine` · `run` · `ConsensusVerdict` |
| [core/observability.py](../core/observability.py) | `render_cost_report` · `render_gate_report` · `dashboard` |

**예측 드릴**
- `score=40`인 `AuditRecord`를 `check_invariants`에 넣으면 무엇이 몇 건 나오는가? (실 로그에서 7건 나왔다)
- `combine`에 리뷰어 3명이 `[reject, approve, approve]`를 냈다. `passed`는? `agreement`는? `score`는?
- 리뷰어 하나가 **예외를 던지면** `run`은 어떻게 처리하는가? 왜 그 방향이 "보수적"인가?

**완료 조건**: "정답 라벨 없이 품질을 재는 두 방법"(자체 모순 감사 · 컨센서스 불일치)을 설명할 수 있다.

---

## R5 — 어댑터

순수 → 주입 → 통합 순서로. 여기서 **세 가지 주입 패턴**을 눈으로 확인한다.

| 묶음 | 파일 |
|---|---|
| youtube 순수 | `captions` · `clip_extractor` · `seo`(순수부) · `story_writer`(순수부) |
| youtube 주입 | `image_gen`(백엔드) · `video_builder` · `tts_engine` · `_ffmpeg`(러너) · `thumbnail_gen`(gen_bg) |
| youtube 통합 | `review` · `publish` · `adapter` · `control` · `persona` · `orchestrator` |
| discord | `channels` · `personas` (의존 방향 IO→콘텐츠) |
| research | `_http` · `search` · `fetch` · `summarize` · `review` · `adapter` |

**예측 드릴**
- `parse_results(html, top_k=0)`이 반환하는 hit 개수는? **왜** 그런가?
- `build_sources_block`에서 2번 소스의 본문이 빈 문자열이면 3번 소스의 출처 번호는? 왜 다시 매기지 않는가?
- 위 답과 `review.check_citations`의 `citation_unfetched_source` 규칙을 **연결**해 보라. 왜 그 규칙이 성립하는가?
- `video_builder`는 실제 ffmpeg 없이 어떻게 테스트되는가?
- youtube는 high 위반에 `review`를, research는 `reject`를 낸다. 엔진이 안 깨지는 이유는?

**완료 조건**: 세 주입 패턴(**게이트웨이** / **백엔드 콜러블** / **경로·clock·rng**)을 각각 실제 파일로 지목할 수 있다.

---

## R6 — 보완 라운드

1. CODE_MAP의 `[모름]`을 우선순위 순으로 소진한다.
2. 리뷰 중 적어둔 결함을 **find → fix → verify**로 닫는다 (선례: [8fc909f](../docs/roadmap.md) 게이트 score 클램프, `c3a30d1` 산문 폴백 누수).
3. 테스트 공백을 메운다. *새 기능은 만들지 않는다.*
4. 리뷰가 만든 보완 커밋을 **push** → Phase 4(채용용 마감)로.

---

## 기록 형식

**`docs/REVIEW_LOG.md`** (라운드 진행 중 매일)

```markdown
## YYYY-MM-DD · R{n}
- 파일/함수:
- 내 예측:
- 실제 결과:
- 빗나간 이유 / 몰랐던 문법·개념:
- 이 함수가 왜 이 구조인지 (한 줄):
```

라운드가 끝날 때마다: 목표 달성 여부(됐다/안 됐다 + 한 줄 이유) · 다음 라운드 · 남은 `[모름]` 하나를 딥다이브 주제로 지정.
이 기록은 그대로 회고 블로그 재료가 된다.

---

## 에이전트 규칙 (리뷰 라운드 한정)

리뷰 라운드 중에는 에이전트(Claude Code · Cursor 등)가 아래를 따른다.

1. **예측 먼저.** 새 코드를 보여준 뒤엔 반드시 요구한다 — *"머지 전에 이 diff를 리뷰하세요. `함수명()`의 실행 결과를 먼저 예측해보세요."* 사용자가 예측을 말하기 전에는 **정답을 알려주지 않는다.**
2. **설명 대신 되묻기.** "이 코드 설명해줘" → *"먼저 본인의 이해를 한 줄로 말해보세요. 그 다음 틀린 부분만 교정합니다."* (단 "그냥 설명해줘"라고 명시하면 설명한다)
3. **CODE_MAP을 대신 쓰지 않는다.** 틀린 항목만 지적하고 왜 틀렸는지 말한다. `[모름]` 항목은 **채워주지 않고 힌트만** 준다.
4. **세션 끝에 REVIEW_LOG 초안**을 요약한다 — 다룬 파일/함수, 빗나간 예측, 새로 나온 문법·개념.
5. **리뷰 라운드에서 신규 기능 요청이 오면 한 번 확인한다.** *"리뷰 라운드 중입니다. 보완이 아니라 신규 기능인데 진행할까요?"* 사용자가 확인하면 따른다.

> ⚠️ **적용 범위는 아직 보류(2026-07-10).** 이 규칙은 현재 **문서로만** 존재한다.
> Claude Code는 `CLAUDE.md`→`AGENTS.md`만 로드하고 `.cursorrules`는 읽지 않으므로,
> 실제로 강제하려면 둘 중 하나를 해야 한다:
> - `AGENTS.md`에 이 절을 참조하는 섹션 추가 → **모든 도구**에 적용
> - `.cursorrules`에 추가 → **Cursor에만** 적용 (Claude Code는 지금처럼 동작)

---

## 면접 스탠스

- *"이 부분은 AI로 생성했고 아직 깊게 안 판 영역입니다. 대신 신뢰 레이어는 전부 설명할 수 있습니다."*
- 어설프게 다 아는 척 → 무너진다. **명확한 깊이 + 솔직한 경계**가 강하다.
- diff 리뷰·보완 이력 자체가 "AI 생성 코드를 검토하고 개선한 증거"로 포트폴리오가 된다.
