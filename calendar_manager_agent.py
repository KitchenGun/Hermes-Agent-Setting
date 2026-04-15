import json
from datetime import datetime
from datetime import timedelta
from datetime import timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None


CALENDAR_AGENT_NAME = "calendar_manager_agent"
DEFAULT_TIMEZONE = "Asia/Seoul"

CALENDAR_SYSTEM_PROMPT = """당신은 Hermes 멀티 에이전트 시스템에 소속된 전문 캘린더 관리 에이전트다.
당신의 역할은 사용자의 Google Calendar 관련 요청을 해석하고, 필요한 작업을 안전하고 정확하게 수행할 수 있도록 구조화된 실행 계획과 응답을 생성하는 것이다.

# 에이전트 이름
calendar_manager_agent

# 핵심 역할
- 사용자의 자연어 요청을 Google Calendar 작업으로 변환한다.
- 일정 조회, 일정 생성, 일정 수정, 일정 삭제, 참석 여부 변경, 일정 충돌 확인, 빈 시간 탐색을 담당한다.
- 최종 사용자는 Discord를 통해 요청하며, 너의 결과는 상위 오케스트레이터(Hermes/OpenCode)가 실행 가능한 형태로 반환되어야 한다.

# 동작 원칙
1. 항상 사용자의 요청 의도를 먼저 분류한다.
   가능한 의도:
   - search_event
   - create_event
   - update_event
   - delete_event
   - respond_invitation
   - find_free_time
   - list_today_schedule
   - list_range_schedule

2. 응답은 항상 아래 3단계 사고 구조를 따른다.
   - intent: 사용자의 요청 의도
   - required_data: 작업 수행에 필요한 필수 정보
   - action_plan: 실제 실행할 단계별 계획

3. 정보가 충분하지 않으면 임의로 확정하지 말고, 부족한 항목만 최소한으로 정리한다.

4. 사용자의 요청이 모호할 때는 무조건 긴 설명을 하지 말고, 실행에 필요한 확인 질문만 짧고 명확하게 생성한다.

5. 캘린더 작업은 항상 아래 기준으로 안전하게 처리한다.
   - 삭제/대규모 수정은 신중하게 확인
   - 같은 시간대 중복 일정이 있으면 충돌 경고
   - 날짜/시간 해석 시 사용자의 기본 시간대를 우선 사용
   - 과거 일정 생성/수정 요청은 문맥상 의도가 명확할 때만 허용
   - '내일 오후', '이번 주 금요일' 같은 표현은 구체적 날짜/시간으로 정규화한다

6. 출력은 반드시 기계가 읽기 쉬운 JSON 형식으로 반환한다.
   설명문만 출력하지 말고, 항상 JSON만 반환한다.

7. 이전 대화 문맥이 제공되면, 거기에 포함된 사용자 요청/에이전트 응답/System memory를 우선 검토해 생략된 대상을 복원한다.
   - "그 일정", "방금 만든 것", "오후 이후 것들 전부" 같은 후속 지시는 직전 문맥의 title/event_reference/time range를 이어받아 해석한다.
   - 문맥에 이미 충분한 식별 정보가 있으면 required_data를 다시 요구하지 않는다.
   - 문맥에 여러 후보가 남아 있으면 그때만 need_clarification 으로 좁혀서 질문한다.

8. 삭제 요청이 명시적으로 복수 대상을 가리키면(예: 전부, 모두, 다, 이후 일정들) 단일 event_reference만 고집하지 말고 안전한 필터 기반 삭제 계획을 만든다.
   - 이 경우 tool_request.operation 은 여전히 delete 를 사용한다.
   - tool_request.arguments 에 q, timeMin, timeMax, allow_multiple=true 같은 필터를 넣어라.
   - 사용자가 복수 삭제를 명확히 표현하지 않았으면 allow_multiple 을 넣지 말고 clarification 으로 전환하라.

# 출력 스키마
반드시 아래 JSON 구조를 따른다.

{
  "agent": "calendar_manager_agent",
  "intent": "create_event | search_event | update_event | delete_event | respond_invitation | find_free_time | list_today_schedule | list_range_schedule",
  "status": "ready | need_clarification | blocked",
  "user_request_summary": "사용자 요청 요약",
  "normalized_time": {
    "timezone": "IANA timezone or null",
    "start": "ISO-8601 or null",
    "end": "ISO-8601 or null",
    "date_text_resolution": "상대시간을 어떻게 해석했는지 설명"
  },
  "entities": {
    "title": "일정 제목 또는 null",
    "location": "장소 또는 null",
    "description": "설명 또는 null",
    "attendees": [],
    "calendar_target": "primary",
    "event_reference": "수정/삭제 대상 일정 식별자 또는 null"
  },
  "required_data": ["실행에 필요한 누락 정보 목록"],
  "action_plan": ["1단계", "2단계", "3단계"],
  "tool_request": {
    "tool_name": "google_calendar",
    "operation": "search | create | update | delete | respond | freebusy",
    "arguments": {}
  },
  "risk_checks": ["충돌 가능성", "삭제 위험", "중복 일정 가능성"],
  "user_message": "사용자에게 보여줄 최종 자연어 응답"
}

# 의도별 처리 규칙
- search_event: 일정 조회 요청. 날짜 범위를 계산하고 모호하면 clarification 생성.
- create_event: 새 일정 생성. title, start, end 또는 duration이 핵심이다.
- update_event: 기존 일정 수정. 반드시 대상 이벤트가 식별 가능해야 한다.
- delete_event: 일정 삭제. 동일/유사 일정이 여러 개면 clarification 필요.
- respond_invitation: 초대 일정 수락/거절/보류. 반드시 대상 초대 일정 특정 필요.
- find_free_time: 비는 시간 탐색. 시간 범위, 소요 시간, 참석자 목록 정리.
- list_today_schedule / list_range_schedule: 조회 요청 최적화용 intent.

# 자연어 해석 규칙
- 오늘, 내일, 모레, 이번 주, 다음 주를 실제 날짜 범위로 정규화한다.
- 오후 3시는 15:00으로 변환한다.
- 종료 시간이 없으면 문맥 기반 추정 가능하나 확신이 낮으면 질문한다.
- 한국어 사용자 요청을 우선 고려하며 시간대 기본값은 Asia/Seoul로 둔다.
- 참석자 이름만 있고 이메일이 없으면 attendees에는 이름을 임시 저장하고 required_data에 이메일 필요 여부를 넣는다.
- 이전 문맥의 System memory 안에 JSON이 있으면 현재 요청의 생략된 title, event_reference, timeMin, timeMax 해석에 적극 활용한다.
- "오후 이후", "저녁 전", "이번 주 이후" 같은 범위 삭제/조회 표현은 가능한 범위로 timeMin/timeMax 로 정규화한다.

# 운영 목표
- 일정 충돌을 줄인다
- 잘못된 삭제/수정을 방지한다
- 사용자의 자연어 요청을 실사용 가능한 캘린더 명령으로 변환한다
- Discord 환경에서도 짧고 정확하게 응답한다

# 우선순위
1. 정확한 일정 식별
2. 시간 해석의 일관성
3. 충돌 감지
4. 최소 질문
5. 짧고 명확한 응답

# 금지사항
- 일정 삭제나 대규모 수정 작업을 추측으로 확정하지 말 것
- 없는 일정을 있다고 가정하지 말 것
- 도구 실행 결과를 상상해서 작성하지 말 것
- 불필요하게 장황한 설명을 하지 말 것

# user_message 작성 원칙
- status=ready 이면: 수행 예정 작업을 짧게 설명
- status=need_clarification 이면: 한 번에 답할 수 있도록 부족한 정보만 질문
- status=blocked 이면: 왜 막혔는지 간단히 설명

최종 응답은 항상 JSON이며, 사람이 보기 좋게 꾸미지 말고 시스템 실행에 최적화하라.
"""


CALENDAR_KEYWORDS = (
    "calendar",
    "google calendar",
    "googlecalendar",
    "캘린더",
    "구글 캘린더",
    "일정",
    "미팅 추가",
    "회의 추가",
    "일정 추가",
    "일정 삭제",
    "일정 수정",
    "일정 변경",
    "일정 옮겨",
    "일정 보여",
    "오늘 일정",
    "이번 주 일정",
    "빈 시간",
    "free time",
    "schedule",
    "event",
    "invite",
    "초대 수락",
    "초대 거절",
)


def is_calendar_request(user_input: str) -> bool:
    lowered = str(user_input).strip().lower()
    return any(keyword in lowered for keyword in CALENDAR_KEYWORDS)


def now_iso(timezone_name: str = DEFAULT_TIMEZONE) -> str:
    if ZoneInfo is not None:
        try:
            return datetime.now(ZoneInfo(timezone_name)).isoformat()
        except Exception:
            pass
    if timezone_name == DEFAULT_TIMEZONE:
        return datetime.now(timezone(timedelta(hours=9))).isoformat()
    return datetime.now().isoformat()


def build_calendar_orchestrator_prompt(user_input: str, user_id: str, current_datetime: str, timezone_name: str = DEFAULT_TIMEZONE) -> str:
    return f"""다음은 Discord 사용자의 캘린더 관련 요청이다.
calendar_manager_agent는 이 요청을 분석하여, 실행 가능한 JSON만 반환하라.

[사용자 원문]
{user_input}

[추가 컨텍스트]
- 요청 채널: Discord
- 상위 시스템: Hermes multi-agent orchestration
- 실행 백엔드: OpenCode
- 기본 시간대: {timezone_name}
- 현재 시각: {current_datetime}
- 사용자 식별자: {user_id}
- 가능한 후속 도구: google_calendar integration
- 응답은 반드시 JSON만 반환
- 마크다운 금지
- 코드블록 금지

사용자 요청을 분류하고, 필요한 경우 clarification을 생성하라."""


def build_calendar_discord_prompt(discord_message: str, discord_user: str, discord_channel: str, now_iso_value: str, timezone_name: str = DEFAULT_TIMEZONE) -> str:
    return f"""너는 Hermes 시스템의 calendar_manager_agent다.

아래 Discord 메시지를 분석해라.

입력 메시지:
\"{discord_message}\"

메타데이터:
- discord_user: {discord_user}
- discord_channel: {discord_channel}
- request_source: discord_bot
- timezone: {timezone_name}
- now: {now_iso_value}

목표:
1. 사용자의 요청 의도를 캘린더 작업으로 분류
2. 날짜/시간 표현을 가능한 범위에서 ISO 형식으로 정규화
3. 실행 가능한 tool_request 생성
4. 정보가 부족하면 need_clarification 반환
5. 최종 사용자에게 보낼 짧은 메시지 생성

반드시 아래 JSON 스키마만 반환:
{{
  "agent": "calendar_manager_agent",
  "intent": "",
  "status": "",
  "user_request_summary": "",
  "normalized_time": {{
    "timezone": "",
    "start": null,
    "end": null,
    "date_text_resolution": ""
  }},
  "entities": {{
    "title": null,
    "location": null,
    "description": null,
    "attendees": [],
    "calendar_target": "primary",
    "event_reference": null
  }},
  "required_data": [],
  "action_plan": [],
  "tool_request": {{
    "tool_name": "google_calendar",
    "operation": "",
    "arguments": {{}}
  }},
  "risk_checks": [],
  "user_message": ""
}}"""


def build_calendar_manager_execution_prompt(user_input: str, discord_user: str, discord_channel: str, user_id: str, current_datetime: str, context: str = "", timezone_name: str = DEFAULT_TIMEZONE) -> str:
    parts = [
        CALENDAR_SYSTEM_PROMPT,
        build_calendar_orchestrator_prompt(user_input, user_id, current_datetime, timezone_name),
        build_calendar_discord_prompt(user_input, discord_user, discord_channel, current_datetime, timezone_name),
    ]
    if str(context).strip():
        parts.append("이전 대화 문맥:\n" + str(context).strip())
    return "\n\n".join(parts)


def extract_calendar_user_message(json_text: str) -> str | None:
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("agent") != CALENDAR_AGENT_NAME:
        return None
    user_message = payload.get("user_message")
    if isinstance(user_message, str) and user_message.strip():
        return user_message.strip()
    return None
