from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse


router = APIRouter()


@router.get("/auth/ticktick/callback")
async def ticktick_oauth_callback(code: str, state: str, request: Request) -> HTMLResponse:
    service = request.app.state.ticktick_oauth_service
    result = await service.connect_with_code(code=code, state=state)
    status_code = status.HTTP_200_OK if result.connected else status.HTTP_400_BAD_REQUEST
    return HTMLResponse(
        content=f"<html><body><p>{result.message}</p></body></html>",
        status_code=status_code,
    )
