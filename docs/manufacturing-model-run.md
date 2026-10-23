# 정답 검수와 실제 모델 측정 준비

현재 상태는 **실행 준비 완료 / 실제 모델 미실행 / 사용자 정답 검수 대기**다.
합성 평가의 42/42 일치를 실제 모델 정확도로 사용하지 않는다.

## 먼저 정답을 검수하기

[42건 상세 자료](eval-review/manufacturing-v1-review.md)에서 근거 사실·초안 3종·판정 이유를 읽고
[빈 응답 CSV](eval-review/manufacturing-v1-decisions.csv)를 별도 복사해 작성한다.
Excel에서는 UTF-8 BOM이 있는 CSV로 열린다. 같은 ID를 기준으로 의견을 남기면 된다.

| 칸 | 작성 내용 |
|---|---|
| `review_status` | `agree` 동의 / `change` 수정 제안 / `unsure` 판단 보류 |
| `proposed_label` | 수정할 경우 approve / review / reject / error |
| `reviewer_reason` | 근거 사실, 정책상 이견 또는 추가 확인할 내용 |
| `proposed_error_kind`, `proposed_error_stage` | 오류 사례의 종류·단계를 수정할 경우 |
| `reviewer`, `reviewed_on` | 직접 검수한 사람과 날짜 |

검수 순서는 N01~N10 정상 → Q01~Q08 수량 → D01~D06 날짜 → U01~U06 모호한 표현 →
L01~L04 정보 노출 → M01~M02 누락 → E01~E04 생성 실패 → C01~C02 없는 코드를 권한다.
`approve`는 업무 문서 완성도 검사가 아니라 현재 규칙상 발행 허용이다.
특히 N07의 짧은 일반 문장, U03의 한글 수사, L 계열의 사람 검토 정책에 동의하는지 확인한다.
두 자재를 쓰는 Q08/L04는 CSV 결과가 아닌 합성 근거이며 자료에 별도로 표시했다.

자료에는 자동 판정 결과를 표시하지 않지만 기대 정답은 보이므로 블라인드 검수가 아니다.
아직 전부 미검수다. 빈 칸이나 기한 경과를 동의로 간주하지 않는다.
이견은 v1 원본을 수정하지 않고 별도 기록한 뒤 v2 정답·근거·체크섬으로 반영한다.
이미 작성한 응답을 지우지 않도록 내보내기 명령은 기존 파일 덮어쓰기를 거절한다.

```powershell
./.venv/Scripts/python.exe -m evals.review_packet --out-dir output/my-label-review
```

## 모델 실행의 범위

[측정 진입점](../evals/live_manufacturing.py)은 고정된 가상 CSV의 고객사A/P-100 출하 변경 한 건을
현재 어댑터의 produce→review로 실행한다. 반복 1회는 문서 3종의 순차 호출이며 반복은 1~3회다.
실패하면 이후 초안·후속 반복을 중단하고 승인 요청이나 확정 문서 발행은 하지 않는다.
입력/출력은 실행 기록으로만 남긴다. 평가셋의 조작된 42개 초안을 모델에 보내는 실험은 아니다.

전송부는 Python 표준 라이브러리의 Gemini Developer API `v1beta` REST다. 추가 SDK 설치가 필요 없다.
기존 제조 CLI의 SDK 경로는 그대로이며, 이 도구의 지연을 기존 SDK 경로의 실측으로 주장하지 않는다.
계산·프롬프트·검증은 기존 어댑터를 공유한다. 텍스트 단일 응답만 지원하며 도구·검색·배치·명시적 캐시는 요청하지 않는다.
`MAX_TOKENS` 등 STOP 이외 종료나 빈 응답도 실패로 기록한다.

공식 [generateContent 요청·응답/토큰 정의](https://ai.google.dev/api/generate-content)와
[현재 가격표](https://ai.google.dev/gemini-api/docs/pricing)를 기준으로 실행 직전 모델 이용 권한·가용성·단가를 확인한다.
모델 ID는 자동 선택하지 않는다. 요청 모델과 실제 응답의 `modelVersion`을 모두 기록한다.
별칭은 시간이 지나면 바뀔 수 있으므로, 제공되는 경우 고정 버전 ID를 사용한다.

## 키 없이 준비와 리허설

저장소 루트에서 개발 가상환경을 사용한다. 기본 명령은 키를 읽거나 네트워크를 호출하지 않는다.

```powershell
./.venv/Scripts/python.exe -m evals.live_manufacturing --out-dir output/model-plan
./.venv/Scripts/python.exe -m evals.live_manufacturing --simulate --out-dir output/model-rehearsal
```

첫 명령은 `manifest.json`만 생성한다. 두 번째 명령은 가짜 응답으로 계산·생성·검증·기록 경로를 확인한다.
`mode=simulation`, 모델 버전 `SIMULATED-NO-MODEL`이며 토큰은 합성 값이다.
이때의 시간·비용·판정은 실제 모델 성능으로 인용하지 않는다. CI도 이 두 모드만 실행한다.
출력 디렉터리는 매번 새 경로여야 하며 기존 기록을 덮어쓰지 않는다.

## 실제 실행 명령 준비

1. 구체적인 모델 ID와 계정 이용 권한을 확인한다.
2. [단가 양식](../evals/fixtures/pricing-template.json)을 `output/pricing-verified.json`으로 복사한다.
   같은 모델의 **USD/100만 토큰, standard, 텍스트** 요율·확인일을 직접 채운다.
   긴 컨텍스트·서비스 등급별 요율이 있으면 이 입력에 해당하는 구간인지 확인한다.
   양식의 null·날짜 자리표시는 그대로 실행할 수 없다. 키는 이 파일에 넣지 않는다.
3. 단가 없이도 호출·토큰·지연은 기록할 수 있다. 이 경우 `--pricing`을 생략하며 비용은 null로 남는다.
4. 최초에는 반복 1회·최대 출력 2,048토큰으로 시작한다. 기본 최대 생성 호출 수는 3회다.
   호출 수·출력 토큰 제한은 달러 예산 상한이 아니다. 이미 처리됐거나 타임아웃 난 호출도 과금될 수 있다.

아래 명령은 **실제 외부 호출을 발생시키는 실행 양식**이다. 모델 값을 채우고 실행 범위·비용을 확인한 후 사용한다.
이번 준비 작업에서는 실행하지 않았다.

```powershell
$modelId = Read-Host "이용 가능 여부를 확인한 Gemini 모델 ID"
$secureKey = Read-Host "Gemini API 키" -AsSecureString
$env:GEMINI_API_KEY = [System.Net.NetworkCredential]::new('', $secureKey).Password
try {
  ./.venv/Scripts/python.exe -m evals.live_manufacturing --live --model $modelId --repeats 1 --timeout-seconds 30 --max-output-tokens 2048 --pricing output/pricing-verified.json --out-dir output/model-first-live
} finally {
  Remove-Item Env:GEMINI_API_KEY -ErrorAction SilentlyContinue
  Remove-Variable secureKey -ErrorAction SilentlyContinue
}
```

macOS/Linux에서는 `.venv/bin/python`을 사용하고 키는 현재 셸의 `GEMINI_API_KEY`로 전달한다.
키는 고정 HTTPS 호스트의 헤더로만 전달하며 URL·파일·콘솔에 기록하지 않는다. 리다이렉트와 자동 재시도는 하지 않는다.
30초 옵션은 urllib 소켓 작업의 제한이며 DNS·여러 읽기·전체 프로세스의 절대 마감은 보장하지 않는다.
실패 코드는 3, 준비/기록 실패는 2다. 정상 완료 0도 게이트 approve나 실제 발행을 뜻하지 않는다.

## 기록을 읽는 방법

| 파일 | 내용 |
|---|---|
| `manifest.json` | 모드, UTC 시작일, Python/OS, REST/API 버전, 모델/설정/단가, 가상 입력·프롬프트·코드 해시 |
| `calls.jsonl` | 호출별 단계·UTC 시작·시간·응답 버전·종료 이유·초안·사용량·추정 비용. 호출 종료마다 추가 |
| `report.json` | 반복별 검증 판정·위반·실패 단계와 전체 집계. 부분 실패도 기록 |

예상하지 못한 인터럽트·디스크 오류에는 최종 보고서가 없을 수 있다. 이미 기록된 JSONL을 보존한다.
각 호출의 시간은 전송·응답 처리까지, 파이프라인 시간은 계산·호출·검증·JSONL 기록을 포함한다.
집계는 실패 호출도 포함하며 실패 건수를 함께 표시한다. p50은 중앙값, p95는 nearest-rank 방식이다.
표본 3~9개의 기술 통계이며 서비스 SLA나 충분한 성능 벤치마크가 아니다. 첫 호출 워밍업도 제외하지 않는다.

비용은 제공자가 반환한 토큰 수 × 직접 확인한 단가의 **추정치**이며 결제 내역이 아니다.
일반 입력=`promptTokenCount - cachedContentTokenCount`, 캐시 입력은 별도 요율,
출력은 `candidatesTokenCount + thoughtsTokenCount`로 계산한다.
사고 토큰이 생략되면 total − prompt − candidates로 유도하고, 캐시 값 생략은 0으로 가정한 사실을 기록한다.
합계 모순·필수 사용량 누락·도구 토큰·standard 이외 등급·미확인 단가는 비용을 null로 남긴다.
실패 응답의 사용량이 없으면 앞선 호출의 알려진 소계만 표시하고 전체 비용은 null이다.
환율·세금·할인·계정 무료 할당·정산 보정은 계산하지 않는다.

사용자 검수 후 실제 출력에도 사실 일치·문서 완성도·정보 노출을 별도로 확인한다.
규칙의 approve를 사람이 확인한 정확도로 바꾸지 않는다. 단일 모델 첫 실행만으로 비용 절감률을 주장하지 않는다.
비교할 때는 같은 입력·설정·단가 기준·반복 횟수·실패 포함 규칙과 실제 응답 버전을 기록한다.
원시 기록은 기본 `output/` 아래 두며 Git에서 제외한다. 공유할 때는 내용을 먼저 검토한다.
