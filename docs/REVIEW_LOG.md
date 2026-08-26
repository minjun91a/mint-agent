# REVIEW_LOG

기록 형식·트랙 정의 → [review-roadmap.md](review-roadmap.md). 트랙 A(소급 리딩)는 Phase 5 완료 후로
보류 중(2026-08-26 사용자 결정). 아래는 트랙 B(diff 리뷰) 진행분만 기록한다.

## 2026-08-26 · 트랙 B 생략 — manufacturing 슬라이스 ①②

- **슬라이스**: `adapters/manufacturing/erp_data.py`·`analyze.py` (+테스트, `__init__.py`) —
  471줄/5파일, 상한(300줄·5파일) 초과라 커밋 2개로 분할해 진행.
- **제시한 예측 질문 3개**: ① BOM 붙은 CSV를 `utf-8-sig`로 읽을 때 결과 ② 실 데모 데이터로
  `analyze_shipment_change` 호출 시 부족 자재·수량 ③ `_find_shipment`가 없는 조합을 받았을 때
  반환 방식(예외 vs 사유 문자열)과 그 이유.
- **사용자 결정**: "문제는 나중에, 우선 계속 진행해" — 예측 생략, 커밋 진행.
- **검증은 됨**: 테스트 654 green(코드 정확성 자체는 확인됐다). 다만 예측→대조 학습 루프는
  이 라운드에서 돌지 않았다 — 나중에 이 두 파일을 열어보면 "내가 예측 안 해본 코드"라는 뜻이다.

## 2026-08-26 · 트랙 B 생략 — manufacturing 슬라이스 ③④

- **슬라이스**: `draft.py`(LLM 초안 3종, 독립 호출) + `adapter.py`(`ManufacturingAdapter.produce`
  배선) + 테스트 — 391줄/4파일, 상한 초과라 커밋 2개로 분할.
- **생략 사유**: 직전 라운드와 같은 사용자 결정("문제는 나중에, 우선 계속 진행해")이 이어짐 —
  라운드마다 다시 묻지 않고 이 방침을 표준으로 적용, 매번 로그만 남긴다.
- **검증은 됨**: 테스트 671 green. `[모름]` 후보(나중에 예측해볼 만한 지점): draft.py가 왜
  1회 통합 호출이 아니라 3회 독립 호출을 택했는지, `render_impact_facts`가 왜 순수 함수로
  분리됐는지, `ManufacturingAdapter.review/publish`가 왜 지금 `NotImplementedError`인지.

## 2026-08-26 · 트랙 B 생략 — manufacturing 슬라이스 ⑤ (review.py)

- **슬라이스**: `review.py`(환각 코드 검증, 순수·결정적) + 테스트 + `adapter.py` review 배선 —
  223줄/4파일, 상한 이내라 커밋 1개.
- **생략 사유**: 동일(사용자가 이 라운드부터는 매번 다시 묻지 않는 표준으로 확인).
- **★ 이 슬라이스에서 실제로 결함을 하나 찾아 그 자리에서 고쳤다(find→fix→verify)**:
  코드 정규식을 `\b[A-Z]{1,4}-\d+\b`로 짰더니, "M-99를"처럼 코드 **바로 뒤에 한국어 조사가
  붙으면 매치가 안 됐다.** Python `re`의 `\b`는 유니코드 `\w` 기준인데, 한글 음절도 `\w`로
  쳐서 숫자→한글 전환 지점에 "경계"가 없다고 판정한다(영어 문서였으면 안 터졌을 결함 —
  한국어 도메인 문서라서 처음부터 드러남). 테스트가 실제로 이걸 잡아냈다(`test_check_draft_flags_each_unknown_code_once`가
  기대한 2건 중 1건만 검출). `\b` 대신 lookaround(`(?<![A-Za-z0-9])...(?!\d)`)로 교체해 해결,
  회귀 테스트로 고정.
- **검증**: 테스트 683 green.

## 2026-08-26 · 트랙 B 생략 — manufacturing 슬라이스 ⑥⑦ (publish)

- **슬라이스**: `publish.py`(확정 문서 렌더+발행) + `adapter.py` publish 배선 + 3단 관통 테스트 —
  261줄/2파일, 71줄/2파일로 2커밋 분할(상한 이내씩).
- **생략 사유**: 동일(표준화된 방침 유지).
- **produce·review·publish 3단 전부 완성** — `ManufacturingAdapter`가 `PlatformAdapter`를
  완전히 만족한다. 결함 없이 첫 실행에 테스트 전부 통과(694 green).

## 2026-08-26 · 트랙 B 생략 — core.approval (HITL 승인 로직)

- **슬라이스**: `core/approval.py`(`resolve` — GateDecision → 최종 실행 여부) + 테스트 —
  146줄/2파일, 상한 이내라 커밋 1개.
- **엔진 레벨로 배치한 이유**: HITL은 어댑터 무관 관심사 — youtube·research·manufacturing
  전부 같은 `resolve()`를 쓴다. 실제 입출력(콘솔 input 등)은 의도적으로 넣지 않음 — 그건
  다음 슬라이스(CLI 배선)의 몫.
- **생략 사유**: 동일(표준화된 방침 유지). 검증: 테스트 699 green.
