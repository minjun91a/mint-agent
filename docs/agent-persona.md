# 에이전트 페르소나 — MINT (Agent Persona)

이 repo는 mint-agent **시스템**(엔진+어댑터+Trust)인 동시에, 그 작업을 수행하는 **에이전트 페르소나**
정의를 함께 담는다. 페르소나 = AI 비서가 어떤 정체성·원칙·자율성 경계로 일하는지에 대한 정본이다.

> Defines **MINT**, the work-assistant agent persona that drives this project — its identity, operating
> principles, workflow, and autonomy boundaries. Cross-tool standard via `AGENTS.md`; drop-in to any project.

## 구성 파일

| 파일 | 역할 |
|------|------|
| [`../AGENTS.md`](../AGENTS.md) | 페르소나 정본 — 정체성·운영 원칙·작업 방식·자율성 경계 (교차 도구 표준) |
| [`../CLAUDE.md`](../CLAUDE.md) | Claude Code 로더 — `@AGENTS.md` import로 페르소나 전문 포함 |

- **Claude Code**는 루트 `CLAUDE.md`를 자동 로드하고 import로 `AGENTS.md`를 끌어온다.
- **다른 도구**(Cursor·Codex·Copilot 등)는 `AGENTS.md`를 직접 읽는다.

## 핵심 원칙 (요약)

- **정확성 최우선** — 추측을 사실처럼 말하지 않는다. 확신도 라벨(🟢/🟡/🔴) 의무.
- **능동성** — 요청 해결과 함께 다음 단계·리스크·더 나은 대안 제시.
- **자율성 경계** — 가역·읽기전용은 진행, 비가역·외부 발신은 사전 확인.

## 다른 프로젝트에 재사용

대상 프로젝트 루트에 두 파일을 복사하면 끝.

```bash
# Git Bash
cp /c/MINT-agent/AGENTS.md /c/MINT-agent/CLAUDE.md /path/to/your-project/
```
```powershell
# PowerShell
Copy-Item C:\MINT-agent\AGENTS.md, C:\MINT-agent\CLAUDE.md -Destination C:\path\to\your-project\
```

프로젝트별 규칙·맥락은 대상 프로젝트의 `CLAUDE.md`에 **추가로** 적어 확장한다(`@AGENTS.md` 줄 유지).
페르소나 자체를 바꾸려면 이 repo의 `AGENTS.md`를 수정한 뒤 각 프로젝트로 다시 복사한다.
