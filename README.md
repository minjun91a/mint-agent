<div align="center">

# 🌿 mint-agent

**운영 중인 YouTube 자동화를 _플랫폼 무관 엔진 + 교체 가능한 어댑터_ 로 가르고,
그 위에 검증·HITL·비용 라우팅 _신뢰 레이어(trust layer)_ 를 얹는다.**

<sub>A platform-agnostic multi-agent automation engine + trust layer, carved out of a YouTube
pipeline running in production since April 2026. *The platform is an adapter; the engine is the asset.*</sub>

[![CI](https://github.com/minjun91a/mint-agent/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/minjun91a/mint-agent/actions/workflows/ci.yml)
![status](https://img.shields.io/badge/status-Phase_1%2B3_done_|_Phase_2_WIP-blue)
![python](https://img.shields.io/badge/python-3.12+-blue)
![tests](https://img.shields.io/badge/tests-641_passing-brightgreen)
![LLM](https://img.shields.io/badge/LLM-Gemini_%7C_OpenAI_%7C_Claude-green)
![license](https://img.shields.io/badge/license-MIT-green)
![last commit](https://img.shields.io/github/last-commit/minjun91a/mint-agent)

[아키텍처](#-아키텍처) · [빠른 시작](#-빠른-시작) · [개발 블로그](docs/blog/) · [로드맵](docs/roadmap.md) · [ADR](docs/adr/)

</div>

---

## mint-agent란?

운영 중인 YouTube 콘텐츠 자동화(2026-04~)를 **플랫폼 무관 엔진(`core/`) + 교체 가능한
어댑터(`adapters/`)** 로 가르는 중인 프로젝트다. 엔진은 모델 호출·검증·발행 게이트를 *플랫폼을
모른 채* 담고, 플랫폼별 로직은 어댑터에 격리한다. 최종 목표는 여기에 **신뢰 레이어**(복잡도 기반
비용 라우팅 · 검증 · HITL · 관측)를 얹어, 데모가 아니라 **운영 위에서 검증되는 시스템**을 만드는 것.

> **Phase 1(엔진/어댑터 경계 분리)·Phase 3(2번째 어댑터) 완료 · Phase 2(신뢰 레이어) 진행 중 — 특성화 테스트 641개 green.**
> 아래 "핵심 기능"은 ✅(현재 작동) / 🔜(계획) 을 정직하게 구분한다.

## 핵심 기능

- **🔌 플랫폼 무관 어댑터 구조 — ✅ 작동** · `core/`는 어댑터 구체 타입을 모른다. 발행 게이트가
  youtube가 *아닌* 가짜 어댑터에도 동작함을 테스트로 증명(adapter-agnostic).
- **🔬 2번째 어댑터(research/digest) — ✅ produce·review·publish 3단 작동** · 도메인이 전혀 다른
  어댑터(웹 검색 → 본문 페치 → 출처 인용 요약 → 인용 검증 → 마크다운 발행)를 붙였다. produce를 붙이는
  동안 `core/`는 **한 줄도 바뀌지 않았다**(`git diff -- core/` 공백). HTTP·LLM·발행 sink가 전부 주입
  지점이라 네트워크·API 키 없이 전 구간 테스트된다. `produce → gate → publish` 관통 테스트로 3단
  계약이 실제로 이어짐을 고정 — 환각 인용이면 파일이 생기지 않는다.
- **🧠 RAG (청킹·임베딩·유사도 검색) — ✅ 엔진 작동, 옵트인** · 본문을 앞에서부터 자르는 대신 질의에
  관련된 발췌만 골라 모델 입력에 넣는다(`core/retrieval.py` + `core/embedding.py`). 임베더(Gemini·OpenAI)와
  저장소(인메모리·pgvector)가 **둘 다 주입**이라 엔진은 프로바이더도 DB도 모른다. **인용 무결성 불변식**:
  선택기는 본문과 같은 길이·순서를 돌려주므로 2번 문서가 탈락해도 3번은 여전히 `[출처 3]`이다.
  `PgVectorStore`는 연결 객체를 주입받아 psycopg 의존이 없다 — 🔴 *실 Postgres 왕복은 미검증(로컬 DB 미배선).*
- **🔎 출처 인용 검증(환각 게이트) — ✅ 작동, LLM 불필요** · 요약이 **존재하지 않는 출처**를 인용했거나
  (`[출처 7]`인데 출처는 3건) **페치에 실패해 읽지도 않은 소스**를 인용하면 발행을 거절한다. 이건 LLM 없이
  검증 가능한 **사실 문제**라 룰 층이 완전히 결정적이다. 근거 본문을 주면 LLM 근거 대조(발췌에 없는 주장)까지.
  검증 규칙의 출처는 요약 프롬프트가 모델에게 약속시킨 항목들 — **프롬프트가 계약이고, review는 그 계약의 테스트**다.
- **🔗 어댑터마다 다른 검증 정책, 같은 결과 어휘 — ✅ 증명** · youtube는 금지표현·AI 라벨을 보고 위반 시 `review`,
  research는 인용 무결성을 보고 위반 시 `reject`. **규칙도 정책도 다른데** `core/trust/verify.py`는 한 줄도 안 바뀌었다.
  엔진 게이트는 둘을 구분하지 않는다(`gate()`가 research 어댑터를 구동하는 테스트로 고정).
- **🧩 모델 게이트웨이 — ✅ 작동** · Gemini·OpenAI·Claude를 같은 `complete` 포트 뒤로 통합.
  프로바이더를 늘려도 엔진/어댑터 코드는 **0줄 변경**.
- **🧭 복잡도 기반 비용 라우터 — ✅ 작동, 실단가로 라이브 비용 계상** · 요청 복잡도를 추정해
  티어(Opus/Sonnet/Haiku)를 고르고, 호출마다 (모델·토큰·비용·근거)를 계측한다. 라우터 자신이
  게이트웨이 포트를 구현해 **드롭인**이라, 조립점(`core/gateway.py`)에서 어댑터·엔진의 모든
  게이트웨이를 감싸 라이브 호출을 계측한다(명시 모델은 안 덮음 = 동작 불변). Gemini·OpenAI·Claude
  단가를 **공식 문서로 직접 확인**해 등록했고(`gemini-2.5-pro`의 200k 컨텍스트 티어링 포함),
  표에 없는 모델은 여전히 미가격(None) — *지어내지 않는다.* **토큰은 provider 실측 usage가 있으면
  실측, 없으면 문자수 근사**(`token_source`로 구분 표기). `model="auto"` **옵트인** 시 복잡도로 티어를
  고른다 — 라우트는 **프로바이더별**이라(Gemini는 flash-lite→flash→pro) SDK가 바뀌지 않는다.
  🟢 절감폭 18배(10k in/2k out: `$0.0018` vs `$0.0325`). 쓰는 모델이 하나뿐인 OpenAI는
  **라우트를 비워 두고** `auto`에 명시적 에러를 낸다 — *없는 라우트를 지어내지 않는다.*
- **🧾 물려받은 것도 검증한다 — ✅ 결함 2건 검출·교정** · ① 운영 코드에서 상속한 `claude-opus-4-7`
  단가가 `$15/$75`(구세대 Opus 4.1 값)였고 실제 공시가는 `$5/$25`였다. 문서에는 *"출처가 붙은 값,
  지어낸 수치 없음"*이라고 적혀 있었다 — **출처가 있다는 것과 정확하다는 것은 다르다**(100k/20k 호출
  기준 `$3.00 → $1.00`). ② 조립점의 세 팩토리가 전부 **Claude 라우트**를 쓰고 있어, `auto`를 켜는
  순간 `default_gemini()`가 Claude 모델명을 Gemini SDK로 넘겼다. 관측 전용이라 아무도 `auto`를
  안 써서 잠들어 있던 버그다. 둘 다 재현 → 교정 → 회귀 테스트로 고정.
- **🛡️ 발행 전 Trust 게이트 + HITL 정책 — ✅ 작동** · `approve`만 통과. **거절과 보류는 다르다** —
  `requires_human`(보류)과 `rejected`를 구분해, 사람을 부를 것만 사람에게 올린다. 게이트 결정은
  `AuditRecord` 호환 JSONL로 남고, **그 로그를 우리 eval 하니스가 그대로 읽는다**(🟢 gate → jsonl →
  eval → 리포트 관통 확인). 감사 기록 실패는 발행 판단을 막지 않는다.
- **🤝 컨센서스 발행 게이트 — ✅ 배선됨** · `gate_consensus(reviewers)`가 N개 리뷰어를 교차검증해
  보수적으로 결합한다. 한 명이라도 거절하면 보류, 갈리면(`disagree`) 사람에게, 리뷰어가 터져도 그 표를
  보수적 `review`로 격리, **빈 리뷰어 목록은 통과시키지 않는다.** 리뷰어는 thunk 주입 — 엔진은 몇 개
  모델이 도는지 모른다. 불일치는 감사 로그에 남아 리포트에 뜬다.
- **🧪 특성화 테스트 + 원본 parity — ✅ 641 green** · 운영 코드를 안 깨고 이식했음을 테스트로 보증.
- **🔬 Trust eval 하니스 — ✅ 작동** · 발행 게이트의 실제 결정 로그를 집계 지표 + **자체 모순 감사**로
  변환(`core/trust/eval.py`). 정답 라벨 없이 게이트가 자기 규칙과 모순되는지 검사 — 실 운영 로그에서
  **점수 스펙 위반(score∉[0,1]) 실사례를 검출**했다(LLM 리뷰어의 미정규화 점수).
- **🧭 규칙 후보 추출 — ✅ 작동** · 반복해서 터지는 위반 카테고리를 규칙 후보로 올린다
  (`eval.rule_candidates`). 원본 감사 로그는 위반 **개수만** 남기고 어떤 규칙이 터졌는지를 버려서
  이 질문 자체가 불가능했다 — 로그 스키마부터 고쳤다. **동시발생 ≠ 인과**: 단독으로 뜬 적 없는
  규칙은 승격 근거가 없다(`solo_count`). 후보는 **제안일 뿐, 적용은 사람이 한다.**
- **📊 멀티모델 컨센서스 검증 — ✅ 엔진 작동** · 같은 콘텐츠를 N개 리뷰어(모델·규칙)로 교차검증해
  판정을 보수적으로 결합하고, 리뷰어들이 갈리면(`disagree`) 사람 판단으로 돌린다(`core/trust/consensus.py`).
  리뷰어는 주입 — 엔진은 몇 개 모델을 쓰는지 모른다. 발행 게이트에 배선됨(`gate_consensus`).
- **📈 관측 대시(리포트 렌더러) — ✅ 작동** · 비용·호출 계측과 게이트 감사를 사람이 읽는 Markdown
  리포트로 렌더링(`core/observability.py`). 실측/근사 비율·미가격·모순 건수·**카테고리 미기록
  레코드 수**를 숨기지 않는다 — 🟢 실 운영 로그(2026-07-10 기준 40건)를 통과율 2.5%·권고분포·
  모순 9건(전부 `score_range`)까지 legible한 리포트로 뽑았다. 그 40건은 카테고리가 없는 구 스키마라
  리포트가 "어떤 규칙이 터졌는지는 이 로그로 답할 수 없다"고 **말한다** — 없는 표를 지어내지 않는다.
- **🔗 daily-drill 검증규칙 파이프 — 🔜 Phase 2** · 매일의 코드 검증 훈련([daily-drill](https://github.com/minjun91a/daily-drill))에서
  반복 발견한 *"그럴듯하지만 틀린"* 패턴을 trust 검증 규칙으로 코드화 — 현재 3일차(SQL·pandas·Python), 규칙 후보 축적 중.
- **💬 Discord IO 경계 분리 — ✅ 채널 정체성 분리** · 채널→역할 매핑을 `adapters/discord/`로 분리
  (의존 방향 IO→콘텐츠), youtube 어댑터는 Discord를 모른다. *봇 런타임(discord_bot·notifier·approvals)
  이식은 🔜 별도 IO 어댑터 작업.*

## 🏗 아키텍처

**의존은 한 방향으로만 흐른다: 어댑터 → 엔진.** 엔진은 어떤 플랫폼이 붙어 있는지 모른다.

```mermaid
flowchart TB
    subgraph adapters["adapters/ — 플랫폼이 갇히는 곳"]
        yt["youtube<br/><sub>스크립트·TTS·영상·썸네일<br/>컴플라이언스 게이트</sub>"]
        rs["research<br/><sub>검색·페치·인용 요약<br/>인용 무결성 게이트</sub>"]
        dc["discord<br/><sub>채널 → 역할</sub>"]
    end

    subgraph core["core/ — 플랫폼 무관 엔진"]
        pipe["pipeline<br/><sub>PlatformAdapter · gate · gate_consensus</sub>"]
        trust["trust/<br/><sub>verify · eval · consensus</sub>"]
        gw["llm · embedding · gateway<br/><sub>프로바이더 포트 + 조립점</sub>"]
        rt["routing<br/><sub>복잡도 → 티어 · 비용 계측</sub>"]
        rag["retrieval<br/><sub>청킹 · 유사도 · pgvector</sub>"]
        obs["observability<br/><sub>Markdown 리포트</sub>"]
    end

    yt & rs -.->|"PlatformAdapter 구현"| pipe
    dc -->|"IO → 콘텐츠"| yt
    yt & rs -->|"complete() 포트"| gw
    rs -->|"발췌 선택(옵트인)"| rag
    pipe --> trust
    rt -->|"드롭인 데코레이터"| gw
    pipe -.->|"감사 JSONL"| trust
    trust & rt --> obs
```

<details>
<summary><b>발행 한 건이 지나가는 길</b> (클릭)</summary>

```mermaid
flowchart LR
    P["produce<br/><sub>어댑터</sub>"] --> R{"review<br/><sub>1인 또는 N인 교차검증</sub>"}
    R -->|"approve"| PUB["publish"]
    R -->|"review<br/>(불일치 포함)"| H["사람 판단<br/><sub>requires_human</sub>"]
    R -->|"reject"| X["발행 안 함"]
    R -.->|"결정 기록"| A["감사 JSONL"]
    A --> E["eval<br/><sub>모순 감사 · 규칙 후보</sub>"] --> RP["리포트"]
    RP -.->|"승격/강등 제안"| H
    H -.->|"🔴 미배선"| D["Discord 승인 UI"]
```

</details>

> 🟢 **구현됨**: 복잡도 라우팅(`core/routing.py`, 실단가 기준 비용 계상, `auto` 옵트인) ·
> 컨센서스 게이트(`gate_consensus`) · 감사 로그와 모순 감사 루프 · HITL 분기(`requires_human`) ·
> 규칙 후보 추출(`rule_candidates` — 제안까지, **적용은 사람**).
> 🔴 **미배선**: Discord 승인 UI(경계는 `adapters/discord/`에 분리돼 있고 봇 런타임 이식은 별도 작업) ·
> 실 Postgres 왕복. 전체 목록은 [docs/metrics.md](docs/metrics.md#아직-아닌-것-정직-표기).

## 🚀 빠른 시작 — API 키 없이 30초

```bash
git clone https://github.com/minjun91a/mint-agent.git && cd mint-agent
pip install -r requirements-dev.txt   # 외부 SDK 불필요 — 주입 설계
python demo.py                        # 키·네트워크 없이 실제로 도는 코드
```

<details open>
<summary><b><code>python demo.py</code> 실제 출력</b> (일부)</summary>

```text
 1. 발행 게이트 — approve만 통과. 애매하면 멈추고 사람에게
──────────────────────────────────────────────────────────────────
  AI 라벨 있음       → approve  proceed=True   발행 진행
  AI 라벨 없음       → review   proceed=False  사람 판단 대기
                   └ [med] ai_labeling

 2. 환각 인용 검증 — LLM 없이 잡는다 (사실 문제이므로)
──────────────────────────────────────────────────────────────────
  정상 인용              → approve  —
  없는 출처 인용           → reject   citation_out_of_range
  안 읽은 소스 인용         → reject   citation_unfetched_source
  인용 없음              → reject   no_citation

 3. 복잡도 비용 라우팅 — 쉬운 일에 비싼 모델을 쓰지 않는다
──────────────────────────────────────────────────────────────────
  저난도  이 문단을 요약해줘           Gemini→gemini-2.5-flash-lite  Claude→claude-haiku-4-5-20251001
  고난도  이 아키텍처를 분석·리팩터해줘     Gemini→gemini-2.5-pro         Claude→claude-opus-4-7

  같은 요청(10k in / 2k out)의 실단가:
    저난도 티어 flash-lite  $0.00180
    고난도 티어 pro         $0.03250   (18배)

 4. 컨센서스 게이트 — 리뷰어가 갈리면 사람에게
──────────────────────────────────────────────────────────────────
  전원 승인        → approve  consensus  → 발행
  한 명 거절       → reject   disagree   → 거절
  의견 갈림        → review   disagree   → 사람
  리뷰어 장애       → review   disagree   → 사람
  리뷰어 없음       → review   disagree   → 사람

 5. 감사 루프 — 게이트 결정 → JSONL → eval 하니스 → 리포트
──────────────────────────────────────────────────────────────────
  게이트 결정 8건을 기록했다: gate_audit.jsonl

  ## 발행 게이트 감사 리포트

  - **결정** 8건 (기간 2026-07-10T09:00:00 ~ 2026-07-10T09:00:00)
  - **통과율** 50.0%
  - **권고** reject=4 · review=3 · approve=1
  - **합의** disagree=1
  - **점수** 평균 0.2 (min 0.0 · max 0.45)
  - **모순 감사** 0건 ✅ 없음

  ### 터진 규칙

  | 규칙 | 심각도 | 결정 수 | 단독 | support |
  |---|---|---:|---:|---:|
  | ai_labeling | med | 2 | 2 | 25% |
  | citation_out_of_range | high | 2 | 2 | 25% |
  | missing_sources_section | med | 2 | 0 | 25% |
  | no_citation | high | 2 | 0 | 25% |
  | unused_source | low | 2 | 0 | 25% |

  ### 규칙 후보 (2회 이상)

  | 규칙 | 결정 수 | 제안 |
  |---|---:|---|
  | ai_labeling | 2 | 👀 관찰 |
  | citation_out_of_range | 2 | 🔺 사전 차단 후보 (단독으로도 reject) |
  | missing_sources_section | 2 | 👀 관찰 — 항상 다른 규칙과 함께 떴다(동시발생 ≠ 인과) |
  | no_citation | 2 | 👀 관찰 — 항상 다른 규칙과 함께 떴다(동시발생 ≠ 인과) |
  | unused_source | 2 | 👀 관찰 — 항상 다른 규칙과 함께 떴다(동시발생 ≠ 인과) |

  > '단독'은 그 규칙이 **유일한 위반**이었던 결정 수다. 0이면 항상 다른 규칙에
  > 업혀 뜬 것이므로 승격 근거가 없다. 제안일 뿐 자동 적용하지 않는다 —
  > 규칙을 바꾸는 것은 사람의 몫이다.
```

> `no_citation`은 **high 위반인데도** 승격 후보가 아니다 — 단독으로 뜬 적이 없다. 빈도만 보면
> `citation_out_of_range`와 똑같아 보이지만, 그 reject를 낸 게 자기라는 근거가 없다.
> **동시발생은 인과가 아니다.**

</details>

> 데모는 **실제로 도는 코드만** 보여준다. 실 LLM 호출·업로드·임베딩은 키가 필요하므로 넣지 않았다 —
> 없는 것을 있는 척하지 않는다. `python demo.py gate routing`처럼 골라 실행할 수도 있다.
> 이 출력은 CI가 매번 검증한다([tests/test_demo.py](tests/test_demo.py)).

```bash
# 특성화 테스트 (이식 동일성 보증) — 641 green, CI에서도 동일 실행
python -m pytest
```

> 전체 파이프라인(실 LLM·이미지·업로드) 실행에는 프로바이더 SDK(`openai`·`google-generativeai`·
> `replicate`·`pillow`·`imageio-ffmpeg` 등)가 **호출 시점에 지연 로드**되며 API 키가 필요하다
> (`.env`: `GEMINI_API_KEY` / `OPENAI_API_KEY` / `REPLICATE_API_TOKEN` …).

### 최소 예제 — 엔진이 어댑터를 포트로 구동한다 (키 불필요)

```python
from core.pipeline import gate
from adapters.youtube.adapter import YouTubeAdapter

# 엔진은 어댑터 구체 타입을 모른다 — PlatformAdapter 포트로만 다룬다.
adapter = YouTubeAdapter(review_deep=False)   # 룰 기반 컴플라이언스 게이트 (오프라인)

script = {"title": "The Murim Genius", "body": "A young martial artist levels up."}
seo = {"title": "K-Webtoon Short", "description": "Epic short. AI-generated assets used."}

decision = gate(adapter, script, seo)
print(decision.proceed)                 # True  → 발행 진행
print(decision.verdict.recommendation)  # "approve" | "review" | "reject"
```

> `description`에서 AI 라벨(예: "AI-generated")을 빼면 게이트가 `review`로 바뀌어
> `proceed=False`(HITL 보류)가 된다 — **Trust ②(사람 승인)를 엔진 레벨에서 강제**한다.

### 복잡도 비용 라우터 — 쉬운 일에 비싼 모델을 쓰지 않는다 (키 불필요)

```python
from core.gateway import default_gemini, default_claude

# 조립점이 프로바이더에 맞는 라우트를 붙여 준다 (호출 없이 미리보기 — 키 불필요).
gemini = default_gemini(api_key="x")
print(gemini.route_for("", "이 문단을 요약해줘", model="auto")[0])            # gemini-2.5-flash-lite
print(gemini.route_for("", "이 아키텍처를 분석·리팩터해줘", model="auto")[0])   # gemini-2.5-pro

print(default_claude(api_key="x").route_for("", "설계를 분석해줘", model="auto")[0])  # claude-opus-4-7
```

```python
from core.routing import CostRouter, aggregate, estimate_cost

# 같은 요청, 다른 티어 — 실단가 기준 절감폭 (10k in / 2k out)
estimate_cost("gemini-2.5-flash-lite", 10_000, 2_000)   # $0.00180
estimate_cost("gemini-2.5-pro",        10_000, 2_000)   # $0.03250  (18배)

records = []
router = CostRouter(any_gateway, sink=records.append)   # any_gateway: Gemini/OpenAI/Claude…
router.complete("", "요약해줘", model="auto")
print(aggregate(records))   # {'calls': 1, 'cost_usd': ..., 'by_tier': {'cheap': {...}}}
```

> 라우터 자신이 `LLMGateway` 포트라, 어댑터는 `GeminiGateway`를 쓰는지 `CostRouter(GeminiGateway())`를
> 쓰는지 **모른 채** 동일하게 동작한다 — 라우팅·비용 계측이 코드 변경 0줄로 들어온다.
> 기본은 **관측 전용**(명시 모델 통과). `model="auto"`는 호출부가 명시적으로 옵트인할 때만 켜진다.

## 📂 프로젝트 구조

```text
mint-agent/
├── core/                   # 플랫폼 무관 엔진
│   ├── llm.py              # 모델 게이트웨이 (Gemini · OpenAI · Claude 프로바이더)
│   ├── embedding.py        # 임베딩 게이트웨이 (Gemini · OpenAI) — llm.py와 같은 모양
│   ├── retrieval.py        # RAG 메커니즘 (청킹 · 코사인 · VectorStore 포트 · pgvector)
│   ├── routing.py          # 복잡도 비용 라우터 (LLMGateway 데코레이터 + 계측)
│   ├── gateway.py          # 게이트웨이 조립점 + 관측 배선 (default_gemini/openai)
│   ├── pipeline.py         # PlatformAdapter 포트 + 발행 게이트(감사 로그·HITL·컨센서스)
│   ├── control.py  persona.py  orchestrator.py   # 라우팅·페르소나·브리핑 메커니즘
│   ├── memory.py           # 채널 메모리 (경로 주입)
│   ├── observability.py    # 비용·게이트 계측 → Markdown 리포트 렌더러
│   └── trust/              # verify(검증 어휘) · eval(게이트 로그 하니스) · consensus(교차검증)
├── adapters/
│   ├── youtube/            # produce · review · publish 구현 + 도메인 어휘 (콘텐츠)
│   │   ├── story_writer.py  seo.py  image_gen.py  captions.py  tts_engine.py
│   │   ├── video_builder.py  thumbnail_gen.py  clip_extractor.py
│   │   └── publish.py  review.py  adapter.py  control.py  persona.py  orchestrator.py
│   ├── research/           # 2번째 어댑터 — 검색·페치·요약·인용검증·발행 (+ RAG 선택기)
│   │   └── _http.py  search.py  fetch.py  summarize.py  rag.py  review.py  publish.py  adapter.py
│   └── discord/            # Discord IO 경계 (채널 정체성 → 페르소나 라우팅)
├── docs/                   # roadmap · metrics · inventory · ADR · 개발 블로그
├── tests/                  # 641 characterization tests
├── demo.py                 # 키 없이 도는 데모 진입점
├── pyproject.toml
└── LICENSE                 # MIT
```

## 🗺 로드맵

- [x] **Phase 0** — 정리·결정·git 안전망·엔진/어댑터 인벤토리
- [x] **Phase 1 — 완료 ✅** · 엔진/어댑터 경계 분리: `review`·`produce` 전 단계(story_writer·seo·
  image_gen·captions·clip_extractor·tts·video·thumbnail)·`publish` 이식 + 모델 게이트웨이(Gemini·OpenAI·Claude) +
  엔진 control(라우터+dispatch)/persona/orchestrator + `YouTubeAdapter`·발행 게이트 + **2중 결합 분해**(Discord IO를
  `adapters/discord/`로, youtube는 콘텐츠 전용). 엔진은 플랫폼을 모른 채 포트로만 의존 — 테스트로 증명
- [ ] **Phase 2 — 진행 중 🔄** · 신뢰 레이어 1차: 복잡도 비용 라우터 **✅** + 관측 배선·실측 usage **✅** +
  Trust eval 하니스 **✅**(실 로그에서 게이트 결함 검출·교정) + 멀티모델 컨센서스 검증 **✅** + 관측 리포트 **✅** ·
  *남음: daily-drill 규칙 파이프(외부 입력 필요)*
- [x] **Phase 3 — 완료 ✅** · 2번째 어댑터(research/digest): `produce`(검색·페치·출처인용 요약, 순수 로직은
  원본 parity IDENTICAL) + `review`(출처 인용 검증 = 환각 게이트) + `publish`(마크다운 다이제스트, sink 주입) +
  RAG(청킹·임베딩·pgvector, 옵트인). **엔진 변경 0줄로 어댑터 추가 = 추상화 증명** — 유일한 엔진 변경은
  `core/pipeline.py`가 미리 예고해 둔 계약 정제(`review(content, seo)` → `review(content, context)`)뿐이고,
  `core/trust/verify.py`는 한 줄도 안 바뀌었다
- [ ] **Phase 4** — 채용용 마감: 아키텍처 다이어그램 · 실측 지표 · 데모

전체 진행 추적 → [docs/roadmap.md](docs/roadmap.md) · 결정 기록 → [docs/adr/](docs/adr/) · 빌드 과정 → [docs/blog/](docs/blog/)

## 📜 License

MIT

---

<div align="center"><sub>개발자(민준)의 AI/백엔드 포트폴리오 — 빌드 과정을 커밋·ADR·블로그로 공개 기록한다.</sub></div>
