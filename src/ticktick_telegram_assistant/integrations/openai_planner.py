from __future__ import annotations

import json
from textwrap import dedent
from typing import Any, Optional

from pydantic import ValidationError

from ticktick_telegram_assistant.domain.schemas import ConversationContext, PlannedConversation

try:
    from openai import AsyncOpenAI
except ImportError:  # pragma: no cover - exercised implicitly in test environments without openai
    AsyncOpenAI = Any  # type: ignore[assignment]


class OpenAIPlanner:
    def __init__(self, client: Optional[AsyncOpenAI] = None, model: str = "gpt-5-mini") -> None:
        self._client = client
        self._model = model

    async def plan(self, context: ConversationContext) -> PlannedConversation:
        if self._client is None:
            return PlannedConversation()

        try:
            response = await self._client.responses.create(
                model=self._model,
                input=self._build_prompt(context),
            )
        except Exception:
            return PlannedConversation()
        try:
            return self._parse_planned_conversation(self._extract_json(response.output_text))
        except ValidationError:
            return PlannedConversation()

    def _build_prompt(self, context: ConversationContext) -> str:
        return dedent(
            f"""
            你是 TickTick Telegram 助手的规划层。你的任务不是闲聊，而是先理解用户意图，再返回严格 JSON。

            只返回 JSON，不要 Markdown，不要解释，不要代码块。

            输出 schema:
            {{
              "intent_type": "query|reminder_control|clarification|task_write",
              "query": {{
                "query_kind": "today_brief|task_lookup|schedule_query|other",
                "query_text": "用户原始查询",
                "time_scope": "today|tomorrow|this_week|custom"
              }},
              "reminder_control": {{
                "control_type": "snooze|stop|resume|reschedule",
                "delay_minutes": 60,
                "scheduled_at": "可选 ISO 8601",
                "target_title": "可选",
                "scope": "telegram_reminder"
              }},
              "clarification": {{
                "question": "需要用户确认的问题",
                "options": ["选项1", "选项2"],
                "reason": "为什么要确认"
              }},
              "task_write": {{
                "write_type": "create|update|complete",
                "target_title": "目标任务标题",
                "target_task_id": "可选任务 ID",
                "summary": "一句话总结"
              }},
              "actions": [
                {{
                  "action_type": "create_task|complete_task|update_task",
                  "target_task_id": null,
                  "payload": {{
                    "title": "任务标题",
                    "match_title": "要修改或完成的已有任务标题",
                    "description": "可选描述",
                    "description_mode": "append|replace",
                    "semantic_type": "explicit_time|windowed|memo",
                    "due_at": "有明确时间时填 ISO 8601",
                    "start_at": "可选 ISO 8601",
                    "end_at": "有明确结束时间时填 ISO 8601",
                    "duration_minutes": 90,
                    "window_start": "粗略时间窗口开始 ISO 8601",
                    "window_end": "粗略时间窗口结束 ISO 8601",
                    "raw_nl_time": "原始自然语言时间片段",
                    "list_name": "用户明确说了 list 才填",
                    "repeat_rule": "重复规则，原样放入 TickTick repeatFlag",
                    "priority": 1,
                    "tags": ["标签1", "标签2"],
                    "checklist": ["子任务1", "子任务2"]
                  }}
                }}
              ],
              "requires_confirmation": false,
              "assistant_reply": null
            }}

            当前只允许这些策略：
            1. 用户明确要新增/记录/提醒一条任务时，用 task_write=create，并保持 create_task action 作为兼容输出。
            2. 用户明确说某条任务“做完了/完成了/勾掉”，并且文本里带了可定位的标题时，用 task_write=complete，并保持 complete_task action。
            3. 用户明确说要改已有任务的时间、说明、标题或 list，并且文本里带了可定位的标题时，用 task_write=update，并保持 update_task action；原任务标题放进 match_title，新的标题才放进 title。
            4. 用户说的是今天安排、日程查询、today brief 之类查询时，用 intent_type=query，填 query，actions 置空。
            5. 用户说的是只调整 Telegram 侧提醒、稍后再提醒、暂停提醒、恢复提醒时，用 intent_type=reminder_control，填 reminder_control，actions 置空。
            6. 用户需要先确认怎么做时，用 intent_type=clarification，填 clarification，requires_confirmation 设为 true，actions 置空。
            7. 用户在改已有任务、完成已有任务但指代不清、或其他高风险写操作时，actions 置空，requires_confirmation 设为 true，并给一句简短中文确认。
            8. 当用户提到重复、优先级、标签、清单/子任务时，把这些信息放进 payload；不要丢字段。

            时间规则：
            - 明确日期+时刻 => semantic_type=explicit_time，并填写 due_at。
            - 如果用户给了开始和结束时间，填写 start_at/end_at。
            - 如果用户给了开始时间和时长，填写 start_at/duration_minutes。
            - 粗略窗口，如“下周”“这两周”“月底前” => semantic_type=windowed，并填写 window_start/window_end/raw_nl_time。
            - 没有明确时间 => semantic_type=memo。
            - 保留用户原本的英文专有名词。
            - 标题要简洁，不要把“提醒我”“帮我记一下”这种口头前缀带进标题。

            当前本地时间: {context.current_local_time}
            当前时区: {context.current_timezone}
            相关记忆: {json.dumps(context.memory_items, ensure_ascii=False)}
            候选任务: {json.dumps(context.candidate_tasks, ensure_ascii=False)}
            用户原话: {context.user_text}
            """
        ).strip()

    def _extract_json(self, output_text: str) -> str:
        cleaned = output_text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
            cleaned = cleaned.strip()
        return cleaned

    def _parse_planned_conversation(self, raw_json: str) -> PlannedConversation:
        validator = getattr(PlannedConversation, "model_validate_json", None)
        if callable(validator):
            return validator(raw_json)
        return PlannedConversation.parse_raw(raw_json)
