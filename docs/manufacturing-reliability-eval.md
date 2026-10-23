# 제조 API 실패 처리와 고정 평가

## 호출 실패 정책

제조 CLI는 내부 조정안 → 협력업체 요청 → 고객사 회신 순서로 세 번 호출한다.
어느 단계에서든 호출이 실패하거나 응답이 빈 문자열·문자열 이외 값이면 즉시 중단한다.
완성된 앞 단계 초안이 있어도 승인·파일 저장으로 넘기지 않는다. 자동 재시도는 없다.
이미 성공했거나 서버에서 처리된 호출에는 비용이 발생할 수 있다.

기본 Gemini 게이트웨이는 **호출 1회당 30초**를 SDK의 `request_options.timeout`으로 전달하고
`retry=None`으로 SDK 재시도를 끈다. CLI의 `--timeout-seconds 15`로 변경할 수 있다.
0·음수·NaN·무한대는 인자로 받지 않는다. CLI 전체를 30초 안에 끝낸다는 보장은 아니다.
SDK 준비·로컬 처리·세 번의 순차 호출 시간이 더해지며, 별도로 주입한 게이트웨이는 자체적으로
제한 시간을 구현해야 한다. 임의의 Python 함수를 강제 중단하는 장치는 아니다.

기존 `google.generativeai` 전송부를 유지했다. 이 작업은 SDK 교체나 실제 네트워크 지연 실험을 포함하지 않는다.
옵션 전달 방식의 근거는 공식 저장소의 [generate_content 구현](https://github.com/google-gemini/deprecated-generative-ai-python/blob/main/google/generativeai/generative_models.py)과
[RequestOptions 정의](https://github.com/google-gemini/deprecated-generative-ai-python/blob/main/google/generativeai/types/helper_types.py)다.

오류는 제공자 SDK를 직접 의존하지 않는 `DraftGenerationError(stage, kind)`로 변환한다.
CLI에는 단계와 아래 분류만 출력하고 예외 원문·API 키·프롬프트를 복사하지 않는다.
이는 이 경계에서 만든 출력 정책이며 외부 SDK 자체의 로그 설정까지 통제하지는 않는다.

| kind | 분류 기준 예 |
|---|---|
| `timeout` | TimeoutError, DeadlineExceeded, ReadTimeout 등 |
| `dependency_missing` | SDK/전이 의존성 ImportError |
| `authentication` | 401/403, 인증·권한 예외 타입 |
| `rate_limit` | 429, ResourceExhausted 등 |
| `connection` | ConnectionError, 연결 실패 타입 |
| `invalid_response` | 빈/비문자열 응답, 응답 처리의 ValueError·TypeError |
| `provider_error` | 그 밖의 제공자 호출 예외 |

제공자 타입 이름과 상태 코드에 기반한 보수적 분류다. 예컨대 일반 HTTP 503은
`provider_error`로, SDK의 `ServiceUnavailable` 타입은 `connection`으로 분류한다.
알 수 없는 오류도 발행을 중단한다. KeyboardInterrupt·SystemExit는 잡지 않는다.

| CLI 종료 코드 | 의미 |
|---|---|
| 0 | 정상 흐름 완료. 정책상 거절·사람 반려·키 없음도 포함하므로 발행 메시지를 확인 |
| 1 | 제조 입력 오류 또는 발행 허가 불일치 |
| 2 | 인자 오류 또는 ERP 파일 읽기 실패 |
| 3 | 초안 생성 실패. 승인 요청·파일 발행 없음 |

## 고정 평가 v1

[평가 입력](../evals/fixtures/manufacturing-v1.json)은 수작업 합성 사례 **42개**다.
정답은 실행 결과에서 복사하지 않고 가상 근거 사실과 정책으로 작성했다.
코드가 이 정답에 맞는지를 검사하며, 사람의 독립 검수는 아직 남아 있다.

| 범주 | 건수 |
|---|---:|
| 정상 | 10 |
| 수량 오류 | 8 |
| 날짜 오류 | 6 |
| 해석 불확실 표현 | 6 |
| 고객 회신의 내부 정보 | 4 |
| 초안 누락 | 2 |
| 없는 코드 | 2 |
| API 오류 주입 | 4 |

개발용 29개와 예약 분할 `holdout` 13개를 고정했다. 둘 다 코드 작성 중 볼 수 있었으므로
블라인드 평가나 독립적인 일반화 성능의 근거로 쓰지 않는다. 새로운 표현·실제 모델 출력과
사람이 검수한 별도 평가셋을 추가하는 것이 다음 단계다.

일반 사례는 고정 근거 사실과 초안을 실제 `ManufacturingPublisher.authorize/publish`에 넣는다.
사람 검토 콜백은 항상 반려하고, 메모리 sink로 자동 발행 여부를 센다.
오류 사례는 실제 어댑터의 세 단계 생성 경계에 timeout·401·429·ImportError를 주입한다.
외부 모델·API 키·디스크 발행 없이 실행하며 이 네 오류 외의 세부 분류는 회귀 테스트에서 확인한다.
생성 모델·프롬프트의 품질 평가는 포함하지 않는다.

현재 [사례별 결과](eval-results/manufacturing-v1.md)와 [원시 JSON](eval-results/manufacturing-v1.json):

- 판정·오류 종류·오류 단계 일치: **42/42**.
- 정상 오차단: **0/10**. 위험 초안 자동 발행: **0/28**. 오류 사례 자동 발행: **0/4**.
- 사람 검토 판정: **10/42**(약 23.8%). 실제 사람 승인을 받았다는 뜻은 아니다.
- 비교 기준: 생성 완료 38건을 검증 없이 모두 발행하는 합성 정책에서는 위험 초안 28건이 발행된다.
  실제 이전 버전을 실행한 전후 실험이나 운영 성과가 아니다.

JSON에는 4×4 혼동 행렬, 분할별 분모와 지표, 사례별 근거, 데이터/코드 SHA-256을 남긴다.
결과가 맞지 않거나 위험한 자동 발행이 생기면 평가 CLI는 1로 종료한다.
이 수치를 실제 LLM 정확도·환각 감소율·비용 절감률로 인용하지 않는다.

## 재현과 변경 정책

저장소 루트의 개발 가상환경에서 실행한다. PowerShell 예:

```powershell
./.venv/Scripts/python.exe -m pytest
./.venv/Scripts/python.exe -m evals.manufacturing --split dev --output eval-dev.json
./.venv/Scripts/python.exe -m evals.manufacturing --split holdout --output eval-holdout.json
./.venv/Scripts/python.exe -m evals.manufacturing --output docs/eval-results/manufacturing-v1.json --markdown docs/eval-results/manufacturing-v1.md
```

macOS/Linux에서는 같은 인자에 `.venv/bin/python`을 사용한다.
CI는 Python 3.12·3.13·3.14에서 테스트와 두 분할의 평가 CLI를 실행한다.
테스트는 현재 코드의 결과와 체크인한 JSON/Markdown 보고서가 같은지도 검사한다.

v1의 입력·정답·분할은 수정하지 않고, 사례나 라벨 변경이 필요하면 v2 파일과 체크섬을 추가한다.
입력은 `.sha256`과 대조하며 `.gitattributes`로 Windows에서도 LF를 유지한다.
코드만 바꾸었다면 위 마지막 명령으로 결과를 재생성하고 변경 이유와 결과 차이를 PR에 적는다.
해시는 변경 추적 수단이며 정답의 타당성이나 독립 검수를 증명하지 않는다.

연결된 검사: [오류 회귀 테스트](../tests/test_manufacturing_errors.py),
[SDK 옵션 테스트](../tests/test_core_llm.py), [평가 재현 테스트](../tests/test_manufacturing_eval.py).
