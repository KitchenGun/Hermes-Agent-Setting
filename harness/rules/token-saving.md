# token-saving.md - 토큰 절약 규칙

## 목적
Claude Code 세션당 소비되는 토큰을 줄이기 위한 규칙 모음.

## 규칙 1: CLAUDE.md 60줄 이하 (자동 검사)
- **검사 스크립트**: `compact-context.sh`
- **근거**: CLAUDE.md는 매 세션 자동 로드된다. 길수록 베이스 토큰 소비 증가.
- **조치**: 초과 시 세부사항을 `harness/rules/*.md`로 분리한다.

## 규칙 2: system_prompt.md 40줄 이하 (자동 검사)
- **검사 스크립트**: `compact-context.sh`
- **근거**: 에이전트 프롬프트는 모든 태스크 실행 시 로드된다.
  50줄짜리 프롬프트가 100회 실행되면 5000줄 추가 소비.
- **조치**: 초과 시 커밋 WARN. 핵심 지시만 남기고 예시는 삭제.

## 규칙 3: 권한 사전 승인 (.claude/settings.local.json)
- **근거**: 권한 확인 대화는 왕복 1~2회 추가 토큰 소비.
- **조치**: 자주 쓰는 명령(git, python, bash, powershell)은 settings.local.json에 사전 등록.

## 규칙 4: 출력 최소화 원칙
- 성공은 조용히 (출력 없음), 실패만 출력한다.
- 4000개 파일 처리 결과 전체를 출력하지 않는다. 실패한 항목만 표시.
- Claude 응답에서 "정리하자면..." "확인했습니다..." 류의 재요약 금지.

## 규칙 5: registry 변경 시 CLAUDE.md 동기화
- **근거**: registry 변경 후 CLAUDE.md가 낡으면 Claude가 registry를 재탐색한다.
- **조치**: agents/registry.json 또는 skills/registry.json 변경 시 CLAUDE.md 에이전트/스킬 표도 갱신.
