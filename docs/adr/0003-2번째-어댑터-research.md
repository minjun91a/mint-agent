# ADR 0003 — 두 번째 어댑터: research/digest

- **상태(Status):** Accepted (방향 확정, 구현은 Phase 3)
- **날짜:** 2026-06-18
- **관련 Phase:** 0 결정 / 3 구현

## 맥락 (Context)

"플랫폼 독립 엔진"이라는 주장을 **증명**하려면, 엔진을 바꾸지 않고 두 번째 어댑터를 붙일 수
있어야 한다. 후보는 둘이었다:

1. **research/digest** — 소스 수집 → RAG 요약 → HITL → 발행. (지식워크 자동화)
2. **멀티채널 배포** — 기존 콘텐츠를 여러 채널로. (빠르지만 "콘텐츠 팜" 인상)

## 결정 (Decision)

**research/digest** 어댑터를 두 번째로 선택한다(Phase 3). 엔진(`core/`) 변경 없이
`adapters/research/`만 추가해 **추상화를 증명**하는 것이 목표.

- 씨앗 코드: `youtube-agent/agents/research.py`(검색→페치→Gemini 요약, 출처 강제) 이미 존재.
- 벡터 DB: **pgvector**로 시작(~10M 벡터 미만).

## 결과 (Consequences)

- ➕ youtube와 결이 다른 도메인이라 "엔진이 플랫폼 무관"임을 강하게 입증.
- ➕ 채용 관점에서 "콘텐츠 자동화"를 넘어 "지식워크 자동화"로 읽힘.
- ➖ RAG·벡터 DB 신규 학습/인프라 필요 → Phase 3에서 다룸.
- ⚠️ research 외 다른 도메인은 명시적으로 범위 밖 — "돌아가는 것 먼저"(브리프 8장) 원칙으로 어댑터 수를 의도적으로 제한한다.
