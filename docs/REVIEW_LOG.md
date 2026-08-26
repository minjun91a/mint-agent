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
