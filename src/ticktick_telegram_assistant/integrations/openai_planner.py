from __future__ import annotations

import json
from textwrap import dedent

from openai import AsyncOpenAI
from pydantic import ValidationError

from ticktick_telegram_assistant.domain.schemas import ConversationContext, PlannedConversation


class OpenAIPlanner:
    def __init__(self, client: AsyncOpenAI | None = None, model: str = "gpt-5-mini") -> None:
        self._client = client
        self._model = model

    async def plan(self, context: ConversationContext) -> PlannedConversation:
        if self._client is None:
            return PlannedConversation()

        response = await self._client.responses.create(
            model=self._model,
            input=self._build_prompt(context),
        )
        try:
            return PlannedConversation.model_validate_json(self._extract_json(response.output_text))
        except ValidationError:
            return PlannedConversation()

    def _build_prompt(self, context: ConversationContext) -> str:
        return dedent(
            f"""
            你是 TickTick Telegram 助手的规划层。你的任务不是闲聊，而是先理解用户意图，再返回严格 JSON。

            只返回 JSON，不要 Markdown，不要解释，不要代码块。

            输出 schema:
            {{
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
                    "window_start": "粗略时间窗口开始 ISO 8601",
                    "window_end": "粗略时间窗口结束 ISO 8601",
                    "raw_nl_time": "原始自然语言时间片段",
                    "list_name": "用户明确说了 list 才填"
                  }}
                }}
              ],
              "requires_confirmation": false,
              "assistant_reply": null
            }}

            当前只允许这些策略：
            1. 用户明确要新增/记录/提醒一条任务时，用 create_task。
            2. 用户明确说某条任务“做完了/完成了/勾掉”，并且文本里带了可定位的标题时，用 complete_task，payload 至少填 title。
            3. 用户明确说要改已有任务的时间、说明、标题或 list，并且文本里带了可定位的标题时，用 update_task；原任务标题放进 match_title，新的标题才放进 title。
            4. 用户说的是今天安排、日程查询，不要输出 create_task、complete_task 或 update_task。
            5. 用户在改已有任务、完成已有任务但指代不清、或其他高风险写操作时，actions 置空，requires_confirmation 设为 true，并给一句简短中文确认。

            时间规则：
            - 明确日期+时刻 => semantic_type=explicit_time，并填写 due_at。
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
