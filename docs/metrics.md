# 실측 지표 (Metrics)

> 이 문서의 모든 숫자는 **직접 측정한 값**이다. 측정 명령을 함께 적어 두었으니 재현할 수 있다.
> 측정일: 2026-07-10 (커밋 `f73c180` 기준)

---

## 규모

| 항목 | 값 | 측정 방법 |
|---|---:|---|
| 테스트 | **641 green** | `python -m pytest` |
| 엔진 `core/` | 16 파일 · 2,528 줄 | `find core -name '*.py' \| wc -l` |
| 어댑터 `adapters/` | 29 파일 · 3,652 줄 | `find adapters -name '*.py' \| wc -l` |
| 테스트 `tests/` | 36 파일 · 6,296 줄 | `find tests -name '*.py' \| wc -l` |
| 테스트 : 프로덕션 코드 비율 | **약 1 : 1** | 6,296 / (2,528 + 3,652) |
| 커밋 | 60 | `git rev-list --count HEAD` |
| LLM 프로바이더 | 3 (Gemini · OpenAI · Claude) | `core/llm.py` |
| 플랫폼 어댑터 | 3 (youtube · research · discord) | `adapters/` |

**테스트는 외부 SDK·네트워크·API 키 없이 전부 돈다.** 프로바이더 SDK(`google-generativeai`·
`openai`·`anthropic`·`psycopg`)를 import 차단한 상태에서 641건이 그대로 통과한다 —
모든 외부 경계가 주입 지점이기 때문이다. CI가 매 push마다 Python 3.12·3.13·3.14에서 확인한다
(개발 환경이 3.14다 — 매트릭스에서 빼 두었다가 argparse 동작 차이를 CI에서야 발견한 적이 있다).

---

## 추상화 증명: 엔진 변경 0줄로 두 번째 어댑터

도메인이 전혀 다른 research 어댑터(웹 검색 → 본문 페치 → 출처 인용 요약)를 붙이는 동안
엔진(`core/`)은 **한 줄도 바뀌지 않았다.**

```bash
git show --stat 38473b5 -- core/    # 출력 없음
```

유일한 엔진 변경은 `core/pipeline.py`가 **미리 예고해 둔** 계약 정제 하나였다
(`review(content, seo)` → `review(content, context)` — `seo`는 youtube 어휘였다).
`core/trust/verify.py`는 끝까지 한 줄도 바뀌지 않았다: youtube와 research는 검증 **규칙도
정책도** 다르지만(전자는 위반 시 `review`, 후자는 `reject`) 결과 어휘만 공유한다.

---

## 비용

**실단가는 프로바이더 공식 문서로 확인한 값이다**(2026-07-10). 단가표에 없는 모델은 비용을
계상하지 않고 미가격(`None`)으로 남긴다 — 지어내지 않는다.

같은 요청(입력 10k · 출력 2k 토큰)을 복잡도 티어별로 보내면:

| 티어 | 모델 | 비용 |
|---|---|---:|
| CHEAP | `gemini-2.5-flash-lite` | $0.00180 |
| BALANCED | `gemini-2.5-flash` | $0.00800 |
| DEEP | `gemini-2.5-pro` | $0.03250 |

→ **저난도 요청을 최상위 모델로 보내면 18배를 더 낸다.** `model="auto"` 옵트인 시 라우터가
복잡도로 티어를 고른다. 라우트는 프로바이더별이라 SDK가 바뀌지 않는다.

```bash
python demo.py routing    # 위 표를 직접 출력한다
```

---

## 신뢰 레이어가 실제로 잡은 것

정답 라벨 없이 품질을 재는 두 가지 방법(자체 모순 감사 · 컨센서스 불일치)과, 그 결과 **찾아서
고친 결함 여섯 건.** 전부 재현 → 교정 → 회귀 테스트 순으로 닫았다.

| # | 결함 | 어떻게 찾았나 | 실제 영향 | 커밋 |
|---|---|---|---|---|
| 1 | 게이트 점수가 스펙 범위를 벗어남 (`score=40`, 스펙은 `[0,1]`) | **실 운영 로그**를 eval 하니스가 감사 → 모순 검출 | 감사 로그가 계약을 위반 | `8fc909f` |
| 2 | 산문 경로에 JSON 폴백 누수 | 두 번째 어댑터가 첫 산문 호출부로서 드러냄 | 모델이 빈 응답을 내면 **사용자가 요약으로 `{}`를 봄** | `c3a30d1` |
| 3 | 상속한 `claude-opus-4-7` 단가가 3배 틀림 (`$15/$75` → 실제 `$5/$25`) | 실단가 확보 중 원 출처와 대조 | 비용을 **3배 과대계상** | `3de39f2` |
| 4 | 조립점의 세 팩토리가 전부 Claude 라우트를 씀 | `auto` 옵트인을 열며 재현 | `default_gemini()`가 **Claude 모델명을 Gemini SDK로** 넘김 | `8ea987f` |
| 5 | `context`를 optional로 넓히자 youtube가 `None.get`으로 깨짐 | 계약 정제 직후 재현 | `gate(adapter, content)` 호출 시 예외 | `6854f19` |
| 6 | 감사 로그가 위반 **개수**만 남기고 **카테고리**를 버림 | "검출 패턴 → 규칙 후보"를 만들려다 스키마에서 막힘 | *어떤 규칙이 가장 많이 터지나*를 로그로 **영영 답할 수 없었다** | `f73c180` |

**#3의 교훈:** 그 단가표는 운영 코드에서 상속한 것이고, 주석에는 *"지어낸 수치 없음, 출처가 붙은 값"*
이라고 적혀 있었다. **출처가 있다는 것과 정확하다는 것은 다르다.**

**#4의 교훈:** 관측 전용이라 아무도 `auto`를 넘기지 않아 잠들어 있던 버그다. 기능을 켜는 슬라이스가
그 기능의 전제를 검증한다.

**#6의 교훈:** 이건 이식 과정에서 생긴 버그가 아니다 — 🟢 원본 로그 40건의 키를 전수 집계하니
카테고리가 아예 없었다. **상속한 스키마도 검증 대상이다.** 그리고 고치려는 기능(규칙 후보 추출)이
없었다면 이 구멍은 계속 안 보였을 것이다.

```bash
# 원본 로그가 남긴 키 전수 집계 — 카테고리 없음을 직접 확인
python -c "import json,pathlib,collections;
k=collections.Counter();
[k.update(json.loads(l)) for f in pathlib.Path(LOGS).glob('compliance_audit_*.jsonl')
                          for l in f.read_text(encoding='utf-8').splitlines() if l.strip()];
print(dict(k))"
```

---

## 감사 루프가 닫혀 있다

발행 게이트의 결정은 `AuditRecord` 호환 JSONL로 남고, **그 로그를 같은 저장소의 eval 하니스가
읽는다.** 게이트가 자기 규칙과 모순되는지(`reject ⟹ ¬passed` · `approve ⟹ passed` ·
`score ∈ [0,1]` · `violation_count = len(violations)`) 정답 라벨 없이 검사한다. 그리고 반복해서
터지는 규칙을 **후보로 올린다** — 자동 적용은 하지 않는다.

```
gate() ──▶ gate_audit.jsonl ──▶ trust.eval.evaluate ──▶ observability.render_gate_report
                                        └── rule_candidates ──▶ 사람의 판단
```

**동시발생 ≠ 인과.** "등장하면 항상 reject"라는 이유만으로 규칙을 승격하면, high 위반 옆에 함께
뜬 med 규칙이 승격 후보로 둔갑한다. `solo_count`(그 규칙이 **유일한 위반**이던 결정 수)가 그
구분을 진다. 🟢 데모 8건에서 `citation_out_of_range`(단독 2)만 승격 후보로 오르고,
`no_citation`은 **high인데도** 관찰에 머문다(단독 0 — 항상 다른 규칙과 함께 떴다).

```bash
python demo.py audit    # 이 루프를 처음부터 끝까지 실행한다
```

이전에는 게이트가 아무 기록도 남기지 않아, eval 하니스는 **원본 운영 로그만** 감사할 수 있었다.
하니스는 있는데 우리 시스템이 그 하니스에 걸리지 않았다.

---

## 아직 아닌 것 (정직 표기)

- 🔴 **`PgVectorStore`의 실 Postgres 왕복은 미검증.** SQL 생성과 바인딩은 가짜 연결로 고정했고,
  psycopg 의존은 모듈에 없다(연결 객체 주입). 로컬 DB를 배선하지 않았다.
- 🔴 **Discord 봇 런타임 미이식.** 채널→역할 경계는 `adapters/discord/`로 분리했지만, 실제 승인 UI는
  없다. HITL은 `GateDecision.requires_human`까지 표현되고 그 다음이 비어 있다.
- 🔴 **end-to-end 파이프라인 실배선 없음.** `produce → gate → publish`는 테스트로 관통하지만,
  이를 구동하는 프로덕션 진입점은 없다(데모 CLI는 오프라인 조각만 보여준다).
- 🟡 **Gemini/OpenAI 임베딩 비용은 계측되지 않는다.** `CostRouter`는 LLM 호출만 감싼다.
- 🟡 **규칙 후보는 우리 게이트가 남긴 로그에서만 나온다.** 외부 검출기(daily-drill)를 producer로
  붙이는 배선은 아직 없다. 그리고 상속한 실 운영 로그 40건은 카테고리가 없는 구 스키마라
  후보를 낼 수 없다 — 리포트가 그 사실을 명시한다(없는 표를 지어내지 않는다).
- 🟡 **`solo_count`는 인과의 근사일 뿐이다.** "단독으로 떴고 그때 reject였다"는 강한 신호지만,
  결정 하나에 규칙이 하나만 뜨는 데이터가 없으면 어떤 규칙도 승격 후보가 되지 못한다.
  후보를 자동 적용하지 않는 이유가 이것이다.
