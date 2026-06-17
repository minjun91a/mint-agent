# MINT — 작업 비서 에이전트

MINT는 개발·리서치·문서작성·업무관리를 통합 보조하는 **작업 비서 에이전트**의
페르소나 정의입니다. 특정 프로젝트에 종속되지 않은 순수 에이전트 구성만 담고 있어,
어떤 프로젝트에든 그대로 떨어뜨려(drop-in) 적용할 수 있습니다.

## 구성

| 파일 | 역할 |
|------|------|
| `AGENTS.md` | 페르소나 정본 — 정체성·운영 원칙·작업 방식·자율성 경계 (교차 도구 표준) |
| `CLAUDE.md` | Claude Code 로더 — `@AGENTS.md` import로 페르소나 전문을 포함 |

- **Claude Code**는 프로젝트 루트의 `CLAUDE.md`를 자동 로드하고, import를 통해 `AGENTS.md`를 끌어옵니다.
- **다른 도구**(Cursor·Codex·Copilot 등)는 `AGENTS.md`를 직접 읽습니다.

## 새 프로젝트에 적용하기

대상 프로젝트 루트에 두 파일을 복사하면 끝입니다.

```bash
# Git Bash 기준
cp /c/MINT-agent/AGENTS.md /c/MINT-agent/CLAUDE.md /path/to/your-project/
```

```powershell
# PowerShell 기준
Copy-Item C:\MINT-agent\AGENTS.md, C:\MINT-agent\CLAUDE.md -Destination C:\path\to\your-project\
```

적용 후 Claude Code를 해당 프로젝트에서 열면 MINT 페르소나가 활성화됩니다.

## 커스터마이징

- 프로젝트별 규칙·맥락은 대상 프로젝트의 `CLAUDE.md`에 **추가로** 적어 확장하세요
  (`@AGENTS.md` 줄은 유지). MINT 코어는 그대로 두고 프로젝트 레이어만 덧붙이는 방식을 권장합니다.
- 페르소나 자체를 바꾸려면 `AGENTS.md`를 이 repo에서 수정한 뒤, 각 프로젝트로 다시 복사합니다.

## 비고

이 repo는 **에이전트 정의만** 포함합니다. 개별 프로젝트의 코드·산출물·메모리(작업 기록)는
포함하지 않습니다.
