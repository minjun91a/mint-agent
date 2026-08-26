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

## 2026-08-26 · 트랙 B 생략 — review.py 확장 + demo.py CLI 배선

- **슬라이스 1**: `review.py`에 `check_customer_leak`(med, 고객사 회신 내부정보 노출) 추가 —
  이전 라운드가 남긴 "정보 노출 미검증" 한계를 닫음. 동시에 이 리뷰어가 `review`(HITL)를
  낼 수 있는 유일한 경로가 됨(코드 환각은 항상 high→reject). 124줄/2파일, 테스트 8건.
- **슬라이스 2**: `demo.py`에 `manufacturing` 데모(6번째) + `console_ask` 추가.
  `console_ask`(실 `input()` 사용)는 **기본 실행 경로에서 안 부른다** — CI가 인자 없이 모든
  데모를 도는데 blocking input은 그걸 멈추게 한다. 대신 `core.approval.resolve`를
  승인/반려 두 결과로 시뮬레이션. 142줄/2파일, 테스트 6건.
- **생략 사유**: 동일. 검증: 테스트 713 green, `python demo.py manufacturing` 수동 실행 확인.

## 2026-08-26 · 트랙 B 생략 — console_ask 이동 + run_manufacturing.py (e2e 진입점)

- **슬라이스 1(리팩터)**: `console_ask`를 `demo.py`→`core/approval.py`로 이동. 두 번째
  소비자(`run_manufacturing.py`)가 생기며 데모→프로덕션 역방향 의존이 드러나 정정.
- **슬라이스 2**: `run_manufacturing.py` 신설 — `demo.py`와 달리 **produce를 진짜 부른다**
  (produce→gate→resolve→publish 전 구간 실배선). `docs/metrics.md`의 "end-to-end 파이프라인
  실배선 없음"을 manufacturing 세로선에서 닫음.
- **★ 실행해서 직접 찾은 결함**: 스크립트를 실제로 돌려보니 `log.warning`이 Windows 콘솔에서
  깨졌다(cp949). `demo.py`는 produce를 안 불러 이 경로를 한 번도 안 밟았던 것 — stdout만
  UTF-8로 바꾸던 기존 패턴이 stderr(logging 기본 출력)엔 안 먹힌다는 걸 이번에 처음 발견.
  stderr도 같이 reconfigure하도록 고침.
- **★ 테스트 작성 중 또 하나**: 첫 "approve 되는지" 테스트에서 가짜 게이트웨이가 세 프롬프트에
  전부 같은 텍스트를 돌려주게 짰더니 review가 **정확하게** 정보 노출로 reject했다 — 프로덕션
  버그가 아니라 테스트 픽스처가 "고객사 회신도 내부 정보를 담는" 비현실적 상황을 만든 것.
  프롬프트별로 다른 텍스트를 내는 가짜로 교체해 해결(review가 제대로 작동하는 증거로 남김).
- 생략 사유: 동일. 검증: 테스트 722 green, `python run_manufacturing.py` 수동 실행(키 없이
  reject 확인) + 가짜 게이트웨이로 approve/reject/review 3경로 전부 테스트.

## 2026-08-26 · 트랙 B 생략 — 보조 시나리오(공급사발, 순방향) + 데이터 결함 수정

- **슬라이스 1**: `analyze.py`에 `MaterialDelayTrigger`·`apply_material_delay`·
  `analyze_material_delay` 추가. 새 계산 로직 없음 — 지연 반영한 스냅샷 위에 기존
  `analyze_shipment_change`를 재사용(같은 코어가 양방향을 돈다는 D5 증거).
- **★ 실제로 계산해보고서야 찾은 결함**: P-200/고객사B를 "무관한 대조군"이라고 서사에만
  써두고 `analyze_shipment_change`로 검증한 적이 없었다. M-01·M-02를 P-100과 공유하는데
  두 품목 합산 수요를 CSV가 못 채워서, 트리거 없이도 이미 infeasible이었다. `data/manufacturing/
  inbound_schedule.csv`에 2행 보강(M-01 08-30/1000, M-02 08-29/200) — 주 시나리오
  숫자(440)는 안 건드림(late_arrivals만 2건으로 늘어남).
- **슬라이스 2**: `adapter.py`에 `Trigger` 유니언 + `_compute_impact` 분기 한 곳. `publish.py`의
  `build_meta`를 `content["trigger"]`가 아니라 `content["impact"]`에서만 읽도록 정정
  (두 번째 트리거 타입엔 `new_ship_date` 필드가 없어서 예전 코드가 깨질 뻔했다).
- **슬라이스 3**: `demo.py`에 보조 시나리오 섹션 추가(M-04 지연 전/후 feasible 대조).
- 생략 사유: 동일. 검증: 테스트 730 green, `python demo.py manufacturing` 수동 실행 확인.
