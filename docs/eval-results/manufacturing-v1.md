# 제조 고정 평가 v1

외부 모델을 호출하지 않은 수작업 합성 사례의 정책 평가다. 실제 LLM 정확도·비용·지연 지표가 아니다.

- 데이터: `manufacturing-v1` / `ceeb2c76e1f35b893b74b3ff844bf3f4a4320a3ee348001dada1aeb745fd457e`
- 코드: `5488d1ea54c3b38dd295500c96c4acb7f92a1c1106644476431af59995b0a378`

| 분할 | 사례 | 판정·오류 일치 | 정상 오차단 | 위험 자동 발행 | 생성 실패 |
|---|---:|---:|---:|---:|---:|
| all | 42 | 42 | 0 | 0 | 4 |
| dev | 29 | 29 | 0 | 0 | 3 |
| holdout | 13 | 13 | 0 | 0 | 1 |

무검증 비교 기준은 완료된 초안 38건을 모두 발행하는 정책이다. 그중 기대상 자동 발행하면 안 되는 사례는 28건이다. 실제 이전 버전이나 운영 트래픽의 실측값은 아니다.

| ID | 분할 | 기대 | 실제 | 자동 발행 |
|---|---|---|---|---|
| N01 | dev | approve | approve | True |
| N02 | dev | approve | approve | True |
| N03 | dev | approve | approve | True |
| N04 | dev | approve | approve | True |
| N05 | dev | approve | approve | True |
| N06 | dev | approve | approve | True |
| N07 | dev | approve | approve | True |
| N08 | holdout | approve | approve | True |
| N09 | holdout | approve | approve | True |
| N10 | holdout | approve | approve | True |
| Q01 | dev | reject | reject | False |
| Q02 | dev | reject | reject | False |
| Q03 | dev | reject | reject | False |
| Q04 | dev | reject | reject | False |
| Q05 | dev | reject | reject | False |
| Q06 | dev | reject | reject | False |
| Q07 | holdout | reject | reject | False |
| Q08 | holdout | reject | reject | False |
| D01 | dev | reject | reject | False |
| D02 | dev | reject | reject | False |
| D03 | dev | reject | reject | False |
| D04 | dev | reject | reject | False |
| D05 | holdout | reject | reject | False |
| D06 | holdout | reject | reject | False |
| U01 | dev | review | review | False |
| U02 | dev | review | review | False |
| U03 | dev | review | review | False |
| U04 | dev | review | review | False |
| U05 | holdout | review | review | False |
| U06 | holdout | review | review | False |
| L01 | dev | review | review | False |
| L02 | dev | review | review | False |
| L03 | dev | review | review | False |
| L04 | holdout | review | review | False |
| M01 | dev | reject | reject | False |
| M02 | holdout | reject | reject | False |
| E01 | dev | error | error | False |
| E02 | dev | error | error | False |
| E03 | dev | error | error | False |
| E04 | holdout | error | error | False |
| C01 | dev | reject | reject | False |
| C02 | holdout | reject | reject | False |
