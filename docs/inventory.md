# inventory.md — 엔진/어댑터/Trust 분류표 (Phase 0)

> 작성: 2026-06-18 · mint-agent Phase 0 산출물
> 분류 대상: `C:\Users\최민준\youtube-agent` (운영 중, git 스냅샷 `705c0d3`로 안전망 확보)
> 목적지 레포: `C:\MINT-agent` (새 모노레포 — 결정포인트 3)
> 확신도: 🟢 직접 검증(독스트링/코드/명령 출력 관측) · 🟡 부분검증(파일명·README·추론) · 🔴 미검증

---

## 0. 한눈에 (핵심 발견 3가지)

1. **결합면이 "하나의 출구"가 아니라 셋이다.** 온보딩 브리프는 "유튜브 = 엔진의 한 출구"라 했지만,
   실제 코드에는 **① Discord(컨트롤·HITL·알림 입출력) · ② YouTube(콘텐츠 출구) · ③ AXIS(외부 비즈니스
   데이터, `master_dashboard.db`)** 세 결합면이 있다. Phase 1 경계 분리의 진짜 난이도는 여기 있다. 🟢
2. **엔진의 씨앗은 실재한다.** `claude_runner`(도구사용 에이전트 코어 + 비용계측), `agent_perms`(채널 권한),
   `memory`, `persona`, `control`(intent 라우터), `orchestrator`가 플랫폼-무관 엔진의 후보다. 다만 현재
   Gemini/Discord/youtube에 직접 결합돼 있어 "추출"이 필요하지 "이미 분리됨"이 아니다. 🟢
3. **Trust 레이어도 부분적으로 이미 돌고 있다.** `compliance`(발행 전 게이트), `approvals_db`+`approval_views`
   (Discord HITL), `policy_watcher`(정책 감시·자가수정 제안)가 작동 중. 실제 eval 데이터는
   `logs/compliance_audit_*.jsonl`(26개)다 — 브리프가 말한 `cleanup-execution-log.md`의 실제 대응물. 🟢

---

## 1. 분류 원칙

3계층(브리프 2장) 기준 + 이 레포의 목표 구조에 매핑한다.

| 계층 | 정의 | 목표 위치 |
|---|---|---|
| **엔진(core)** | 플랫폼을 모르는 오케스트레이션·레지스트리·라우팅·메모리·스케줄 추상 | `core/` |
| **어댑터(adapter)** | 교체 가능한 입출력 — produce/review/publish 구현 | `adapters/youtube/`, (Phase3) `adapters/research/` |
| **Trust(cross-cutting)** | 모든 동작을 가로지르는 검증·HITL·비용라우팅·관측성 | `core/trust/` (youtube용으로 먼저, 추출은 나중) |
| **IO 어댑터** | 사람·외부 시스템 접점 (Discord, AXIS) | `adapters/discord/`, `adapters/axis/` |
| **인프라** | 스케줄러 진입점·keepalive | `infra/` 또는 `core/scheduler` |

> 원칙(브리프 8장): **지금은 분류만.** 런타임 동작은 불변. 일반화는 2번째 어댑터가 강제할 때.

---

## 2. 엔진 후보 (`core/`) — 🟢 독스트링 직접 확인

| 모듈 (youtube-agent/) | 역할 | 엔진성 | 결합/주의 |
|---|---|---|---|
| `agents/claude_runner.py` | Claude 도구사용 멀티스텝 러너, 채널별 권한, **`cost_usd` 누적 추정** | 코어 에이전트 루프 + 비용계측 씨앗 | Anthropic 직접. 비용계측 → Trust 비용라우터의 기반 |
| `agents/agent_tools.py` | 도구 정의(read/write/bash) + 권한 기반 실행 | 에이전트 도구 레이어 | 경로 화이트리스트가 youtube/axis 하드코딩 |
| `agents/agent_perms.py` | 채널별 권한 매트릭스(도구·경로·DB·Discord 범위) | 거버넌스 | `YT_AGENT`,`AXIS` 절대경로 상수 → 결합 |
| `agents/memory.py` | 채널별 대화 메모리(SQLite, 슬라이딩 N턴) | 메모리 | 순수도 높음(결합 적음) |
| `agents/persona.py` | 채널별 시스템프롬프트 + **Gemini 호출 헬퍼** | 페르소나 + 모델호출 추상 씨앗 | Gemini 직접 결합. `.env`의 채널ID 의존 |
| `agents/control.py` | 자연어→슬래시 intent 라우터(룰 우선, Gemini 폴백) | 라우팅 | intent 목록이 youtube-특화(story/series). 경계 혼재 |
| `agents/orchestrator.py` | 일일 우선순위 브리핑 + 자유질의 | 오케스트레이션 | **AXIS + youtube 동시 수집 → 강결합** |
| `agents/notifier.py` | 파이프라인 알림 단일 진입점(`pipeline_run` 컨텍스트) | 관측성/알림 | Discord(Bot Token REST) 직접 결합 |

---

## 3. Trust 레이어 (cross-cutting) — 🟢

| 모듈 | Trust 기능(브리프 5장) | 비고 |
|---|---|---|
| `agents/compliance.py` | **① 검증/가드레일** — 업로드 직전 게이트(AI라벨·금지표현·저작권·의료/금융/법률·출처·EU AI Act Art.50) | 게이트 메커니즘=엔진, 규칙=콘텐츠 특화 → 추출 시 분리 대상 |
| `agents/approvals_db.py` | **② HITL** — 승인요청 영속(타입: publish/policy/alpha_review) | SQLite. request_id 기반 |
| `bot/approval_views.py` | **② HITL** — Discord 영구 버튼 UI(✅/❌/🔁), 재시작 후에도 생존 | Discord 결합. `_HANDLERS` 레지스트리로 dispatch |
| `agents/policy_watcher.py` | **①+② 정책 거버넌스** — 정책페이지 변화 감지→영향 코드 제안→HITL 승인→자동 패치(일부) | 매주 일요일 schtasks |
| `agents/claude_runner.py`(재게재) | **③ 비용** — `cost_usd` 추정 보유 | 단, **복잡도→Opus/Sonnet/Haiku 중앙 라우터는 미확인** 🟡(아래 6장) |
| `agents/notifier.py`(재게재) | **④ 관측성** — 파이프라인 START/OK/FAIL 구조화 알림 | |

---

## 4. YouTube 어댑터 (`adapters/youtube/`) — 🟢

인터페이스(브리프 로드맵) `produce / review / publish`에 매핑.

| 단계 | 모듈 | 역할 |
|---|---|---|
| **produce(생성)** | `agents/story_writer.py` | GPT-4o-mini 1분 한국어 스크립트 |
| | `agents/story_writer_gem.py` | Gemini Gem 5대 카테고리 스토리 |
| | `agents/longform_writer.py` | 롱폼(10~15분 5~8챕터) |
| | `agents/content_planner.py` | 콘텐츠 후보 발굴(외부 트렌드+채널 성과 합성) — 채널 특화 |
| | `agents/clip_extractor.py` | 롱폼 → 숏폼 후크 추출 |
| **produce(자산화)** | `agents/tts_engine.py` | TTS(ElevenLabs+OpenAI 폴백, 보이스 회전) |
| | `agents/captions.py` | 멀티보이스 타임라인 → SRT 영문 자막 |
| | `agents/image_gen.py` | 이미지(FLUX 주, DALL-E 폴백) |
| | `agents/thumbnail_gen.py` | 썸네일(DALL-E + Pillow 텍스트) |
| | `agents/video_builder.py` | ffmpeg 합성(1080x1920 mp4) |
| **메타(SEO)** | `agents/seo.py` | 영문 제목/설명/태그/해시태그 |
| | `agents/manhwa_seo.py` | Gemini 영문 SEO(채널 특화) |
| **publish** | `agents/uploader.py` | YouTube Data API 업로드(AI 라벨 메타) |
| | `tools/youtube_daily.py` | 일일 채널 리포트 빌더(분석) 🟢 |
| **파이프라인 조립** | `tools/story_pipeline.py` | 쇼츠 풀파이프(스크립트→TTS→이미지→영상→썸네일→**PRIVATE 업로드=인적검토 게이트**) |
| | `tools/story_pipeline_gem.py` | Gem 작가 → 영상 자동 |
| | `tools/story_pipeline_custom.py` | 사용자 작성 JSON → 영상 |
| | `tools/series_pipeline.py` | 롱폼 1 + 숏폼 N 자동 |

> 핵심: `tools/*_pipeline.py`가 produce→publish를 묶는 **어댑터 파이프라인**이다. **엔진의 오케스트레이션 추상은
> 여기서 추출**된다(Phase 1). 현재는 youtube 모듈을 직접 import.

---

## 5. IO 어댑터 / 외부 시스템

### 5-1. Discord (컨트롤·HITL·알림 접점) — 🟢
| 모듈 | 역할 |
|---|---|
| `bot/discord_bot.py` (1013줄) | 컨트롤 패널, 슬래시 커맨드, on_ready alive |
| `bot/setup_channels.py` | 채널 자동 생성 + `.env` 자동 기록 |
| `bot/post_channel_guide.py` / `_rest.py` / `post_usage_guide.py` | 가이드 채널 게시(봇/REST 두 경로) |

### 5-2. AXIS (외부 비즈니스 자동화 — 엔진도 youtube도 아님) — 🟢
| 모듈 | 역할 |
|---|---|
| `agents/axis_reply_watcher.py` | AXIS `master_dashboard.db` replies 폴러 → Discord 알림(label별 라우팅) |
| `agents/orchestrator.py`(재게재) | AXIS 계약/갱신/dispatch/회신 수집 |

> AXIS는 `C:\Users\최민준\axis-automation`의 별도 시스템(코드는 이 인벤토리 범위 밖). mint-agent 관점에선
> **또 하나의 어댑터 후보(`adapters/axis/`)** 또는 외부 데이터소스. Phase 1 경계 정의에서 다룬다.

### 5-3. CLI 진입점 (`tools/*_cli.py`) — 🟢
`agent_cli` · `control_cli` · `research_cli` · `content_planner_cli` · `orchestrator_cli` · `push_daily_to_discord` —
Discord 없이 단발 실행. 엔진/어댑터 호출의 얇은 진입점(라우팅·인프라 경계).

### 5-4. research 도메인 (Phase 3 2번째 어댑터의 씨앗) — 🟢
`agents/research.py`(DuckDuckGo 검색→본문 페치→Gemini 요약, 출처 강제). 현재 `content_planner`가 소비하지만,
**결정포인트 2 = research/digest 어댑터**의 핵심 재료. "엔진 vs 어댑터" 경계의 시험대.

---

## 6. 데이터 / eval / 인프라

| 분류 | 자산 | 비고 |
|---|---|---|
| **Trust eval 데이터** | `logs/compliance_audit_*.jsonl` (26개) | 컴플라이언스 게이트 감사 로그 = 실제 환각/위반 라벨 데이터셋 |
| 운영 이력 | `data/orchestrator/*.md`, `data/planner_reports/*.json`, `data/youtube_daily/*.md` | git 스냅샷에 포함(텍스트) |
| 정책 상태 | `data/policy_snapshots/*.json` | policy_watcher 직전 해시 |
| 시드 설정 | `data/tropes.yaml`, `voices.yaml`, `image_models.yaml` | 동작 영향 → 커밋됨 |
| 런타임 상태 | `data/approvals.db`, `channel_memory.db` | gitignore(재생성) |
| 스케줄러 | `*_scheduled.ps1` 7종(youtube_daily/orchestrator/content_planner/policy_watcher/series/story_gem/weekly_trend) | Windows 작업 스케줄러 진입점 → 엔진 스케줄러 추상 대상 |
| keepalive | `bot_keepalive.{ps1,bat,vbs}` | 봇 상시가동 |
| 제외(git) | `output/`(352MB 생성미디어), `venv/`, `credentials/`, `.env*` | `.gitignore` 강화로 누락 검증 완료 🟢 |

---

## 7. 온보딩 브리프 vs 실제 코드 — 불일치 보정 (정확성)

| 브리프 4장 표현 | 실제 | 확신도 |
|---|---|---|
| `AGENT_REGISTRY`(Phase2 정규화 완료, 엔진) | youtube-agent 코드에 동명 파일 **없음**. `agent_perms.py`+`agent_tools.py`가 유사 개념. 실제 `AGENT_REGISTRY.md`는 홈(`~/`)의 **문서**(글로벌 CLAUDE.md가 import) | 🟡 코드≠문서 |
| `cleanup-execution-log.md`(24+ 환각 로그) | youtube-agent에 **없음**. 실제 대응물은 `logs/compliance_audit_*.jsonl` | 🟡 위치 상이 |
| `BOT/ALPHA 토큰 라우팅`→비용 라우터 | 별도 라우터 파일 미발견. 모델 선택은 현재 **per-call 플래그**(`--opus`,`--model`)·하드코딩 산재. `claude_runner`가 비용 추정만. **중앙 복잡도 라우터는 Phase 2 명시화 대상** | 🔴 미확인, 검증필요 |
| `AXIS Inbox Sync 스케줄러` | `axis_reply_watcher.py`(봇 백그라운드 폴러)로 존재 | 🟢 |
| `alpha_review` 핸들러 | `approvals_db.py`의 승인 타입으로 존재 | 🟢 |
| `manhwa_seo.py` | `agents/manhwa_seo.py` 존재 | 🟢 |
| 멀티 프로바이더 | Claude(runner/cli) · **Gemini(persona/seo/manhwa_seo/planner/research/gem/control폴백/policy/orchestrator)** · OpenAI(story_writer/tts폴백) · ElevenLabs · FLUX/DALL-E 혼재 | 🟢 → 비용라우팅 설계 시 핵심 |

---

## 8. Phase 1 경계 분리 시 핵심 쟁점

1. **3중 결합 분해** — Discord(IO)·YouTube(출력)·AXIS(외부데이터)를 각각 어댑터로 빼고, `core/`는 인터페이스만 의존.
2. **모델 호출 추상화** — Gemini/OpenAI/Claude 직접호출이 모듈마다 산재 → `core/` 단일 모델 게이트웨이로 모으면
   비용라우팅(③)·관측성(④)이 자동으로 한 곳에 모인다.
3. **파이프라인 인터페이스(`produce/review/publish`)** — `tools/*_pipeline.py`에서 youtube 의존을 인터페이스 뒤로.
4. **동작 동일성 보증** — 리팩터 전후 산출물 동일성 테스트(브리프 8장). 현재 자동 테스트 부재 → Phase 1 선결과제.

---

## 9. 다음 단계

- [ ] **모노레포 골격 스캐폴딩** — `core/` · `adapters/youtube/` · `docs/` 디렉터리와 빈 인터페이스(`produce/review/publish`) 정의 (Phase 1 진입, 별도 브리핑 후 진행)
- [ ] youtube-agent 동작 동일성 테스트 하니스 (Phase 1 선결)
- [ ] 모델 호출 지점 전수조사 → 중앙 게이트웨이 설계 (Phase 2 비용라우터 명시화)
- [ ] `cleanup-execution-log.md` 실제 위치 확인 또는 `compliance_audit_*.jsonl`로 대체 확정 🔴

> 막히면 브리프 2장(목적)·8장(작업규칙)으로 복귀.
