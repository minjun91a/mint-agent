# 제조 수량·날짜 검증과 발행 승인

적용 범위: manufacturing 어댑터와 제조 CLI. 기존 계산식과 다른 어댑터는 유지한다.

## 판단 정책

`CascadeImpact`를 근거로 세 초안을 검사한다. 숫자가 전체 사실 목록 어딘가에 있다는
이유로 통과시키지 않고 자재·항목 또는 날짜 역할을 함께 연결한다.

| 초안 예시 | 판정 | 이유 |
|---|---|---|
| `M-01 부족 수량 440개` | approve | M-01의 부족분과 일치 |
| `M-01 필요 1,040개 / 확보 600개 / 부족 440개` | approve | 항목별 수량 일치 |
| `M-01 부족 수량 500개` | reject | 500은 출하수량이며 M-01 부족분이 아님 |
| `M-01 필요 수량 440개` | reject | 부족분을 필요량으로 잘못 사용 |
| `신규 출하일 2026-08-29` | reject | 8/29는 입고일이며 신규 출하일은 8/28 |
| `M-01 440개 검토` | review | 수량 항목이 불명확 |
| `출하일 8월 28일`, `내일 출하` | review | 연도 없는 날짜·상대 날짜는 자동 확정하지 않음 |

표는 포함된 가상 고객사A/P-100 시나리오 기준이다. 실제 판정은 세 초안의 모든 위반을
합산하며 high가 있으면 reject, med만 있으면 review, 위반이 없으면 approve다.
명확한 불일치에 대한 reject는 사람의 승인으로 덮어쓰지 않는다.

## 지원 표현과 한계

- 수량: 숫자와 `개`, `EA`, `일`. 천단위 쉼표와 전각 숫자 정규화를 지원한다.
- 수량 항목: 자재코드와 부족/추가, 필요/소요, 확보/가용. 출하수량·남은 생산량·앞당김/연기 일수도 구분한다.
- 입고 수량/날짜는 해당 자재의 지연 입고 근거가 한 값으로 확정되는 경우에만 대조한다.
- 날짜: 연도를 포함한 `2026-08-28`, `2026/8/28`, `2026.8.28`, `2026년 8월 28일`.
  기존/신규 출하일과 자재 입고일을 구분한다.
- 대상이나 항목을 못 찾거나 여러 근거가 가능하면 `unverified_numeric_claim`으로 사람 검토를 요청한다.
  지원 밖 단위·남은 숫자·인식한 한글 수사/상대 날짜도 같은 정책이다.
- `재고`는 특정 마감일까지의 확보량과 같다고 가정하지 않는다. 근거가 없으므로 review다.

이 검사는 제한된 문법을 사용하는 규칙이다. 부정·가정·인과·날짜 이동의 모든 의미를
이해하지 않으며, 숫자 없는 일반 문장의 사실 정확도도 보장하지 않는다.
처리하지 못한 자유 문장은 사람이 실제 내용과 근거를 확인해야 한다.
거절/보류 비율과 문장 품질 평가는 후속 고정 평가셋에서 측정한다.

구현: [facts.py](../adapters/manufacturing/facts.py), [review.py](../adapters/manufacturing/review.py)

## 승인 경계

제조 발행 API는 다음 순서를 요구한다.

```python
authorization = adapter.authorize_publish(content, ask=console_ask)
if authorization.outcome.publish:
    result = adapter.publish(content, permit=authorization.permit)
```

1. publisher가 렌더러의 입력인 영향 계산 결과와 초안 3종을 깊은 복사로 고정한다.
2. 스냅샷을 gate로 검토한다. approve는 규칙 허가, review는 사람에게 확인, reject는 허가 없음이다.
3. 허용된 경우에만 해당 publisher가 일회성 토큰과 내용의 SHA-256을 보관한다.
4. publish는 토큰·발행 인스턴스·내용 일치를 검사한다. 원문과 근거의 중첩 목록 변경도 탐지한다.
5. 허가를 소비한 뒤 검증한 복사본으로 렌더링하고 sink를 호출한다.

직접 만든 permit 객체, 다른 publisher의 permit, 재사용 permit은 저장하지 않는다.
승인 뒤 내용이 달라지면 다시 검토해야 한다. sink가 실패해도 이미 소비한 허가로
자동 재시도하지 않는다. 전달 여부를 확인하고 다시 승인해야 한다.

제조 CLI는 사람 판단이 필요한 경우 사실 블록과 초안 3종을 먼저 출력한다.
확인 중에는 파일을 쓰지 않으며, 승인 콜백이 원본 내용을 바꿔도 승인한 스냅샷과
최종 내용이 다르면 저장하지 않는다.

허가는 메모리 안에 있으며 publisher 인스턴스와 프로세스 수명에 한정된다.
별도 TTL·영속 승인 DB·사용자 인증은 없다. 임의 Python 코드나 저수준 sink 직접 호출을
격리하는 보안 샌드박스가 아니라 제조 발행 API의 호출 순서를 강제하는 장치다.

구현: [publish.py](../adapters/manufacturing/publish.py), [CLI](../run_manufacturing.py)

## 회귀 검사

- 정상 수량·날짜, 다른 항목/자재의 값 차용, 잘못된 날짜, 지원 밖 표현
- 사람 승인/반려와 허가 없는 발행, 승인 중/후 문장·계산 근거 변경
- 가짜 허가, 다른 인스턴스에서 사용, 재사용, sink 실패 후 재시도
- CLI의 전체 미리보기와 잘못된 수량의 파일 미생성

```powershell
./.venv/Scripts/python.exe -m pytest tests/test_manufacturing_review.py tests/test_manufacturing_publish.py tests/test_manufacturing_adapter.py tests/test_run_manufacturing.py
```

실제 모델의 응답 품질·비용·지연은 이 테스트 대역 기반 회귀 검사에 포함되지 않는다.
