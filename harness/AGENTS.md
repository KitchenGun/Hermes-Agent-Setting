# AGENTS.md - Hermes Token 하네스

## 원칙
- 처음부터 완벽하지 않아도 된다. 실패할 때마다 여기에 한 줄 추가한다.
- 성공은 조용히, 실패만 시끄럽게.
- 토큰 절약 = 컨텍스트를 작게 + 허가를 미리 + 출력을 최소로.

## 토큰 절약 규칙
- CLAUDE.md 60줄 이하 유지. 초과 시 커밋 차단.
- skills/*/system_prompt.md 40줄 이하 유지. 초과 시 경고.
- 응답은 핵심만. 재확인/재요약 반복 금지.
- 코드 탐색 전 CLAUDE.md 먼저 확인한다.
- 파일 전체 읽기 전에 필요한 섹션만 읽는다.

## 코드 규칙
- 커밋 전 `bash harness/scripts/lint.sh --fix --quiet` 통과할 것.
- 사용하지 않는 코드(dead code)는 발견 즉시 삭제한다.
- agents/registry.json, skills/registry.json 수정 후 CLAUDE.md 동기화.

## 가비지 컬렉션 체크리스트
- [ ] CLAUDE.md 60줄 이하인가?
- [ ] system_prompt.md 파일 40줄 이하인가?
- [ ] 미사용 import 없는가?
- [ ] 죽은 코드 없는가?
- [ ] registry 변경 시 CLAUDE.md 동기화됐는가?

## 실패 로그
<!-- 예시: 2026-04-16 | agent pool 재탐색으로 토큰 낭비 | CLAUDE.md 핵심 파일 목록 보강 -->
