from openai import AsyncOpenAI

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
            input=context.to_prompt(),
        )
        return PlannedConversation.model_validate_json(response.output_text)

