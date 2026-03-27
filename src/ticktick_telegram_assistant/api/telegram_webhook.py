from fastapi import APIRouter, Request, status

from ticktick_telegram_assistant.services.conversation_service import TelegramUpdate


router = APIRouter()


@router.post("/webhook/telegram", status_code=status.HTTP_202_ACCEPTED)
async def telegram_webhook(payload: TelegramUpdate, request: Request) -> dict[str, str]:
    service = request.app.state.conversation_service
    telegram_client = request.app.state.telegram_client
    replies = await service.handle_update(payload)
    for reply in replies:
        await telegram_client.send_message(chat_id=reply.chat_id, text=reply.text)
    return {"status": "accepted"}
