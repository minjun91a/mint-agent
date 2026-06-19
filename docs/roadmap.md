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
- [ ] `publish` 이식 (`uploader`, YouTube API라 목 필요) + produce 나머지 모듈
- [ ] 엔진 나머지 이식 (persona·control·orchestrator) + 모델 호출 게이트웨이
- [ ] 2중 결합 분해 (Discord·YouTube를 어댑터 뒤로)

## ⬜ Phase 2 — 신뢰 레이어 1차

- [ ] eval 하니스 (`compliance_audit_*.jsonl` → 자동 환각/위반 체크)
- [ ] 모델 호출 게이트웨이 + **복잡도 기반 비용 라우터**(Opus/Sonnet/Haiku) 명시화
- [ ] 관측성 로깅 → README 지표 테이블 실측값 채우기
- [ ] 발행 전 가드레일 정비

## ⬜ Phase 3 — 두 번째 어댑터 (research/digest)

- [ ] `adapters/research/` (소스 수집 → RAG 요약 → HITL → 발행)
- [ ] pgvector 도입
- [ ] **엔진 변경 0으로 어댑터 추가** = 추상화 증명

## ⬜ Phase 4 — 채용용 마감

- [ ] 아키텍처 다이어그램·지표·데모 GIF
- [ ] 이력서 문장 2종 (콘텐츠 자동화 / 지식워크 자동화)
