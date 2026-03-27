from fastapi import APIRouter


router = APIRouter()


@router.get("/auth/ticktick/callback")
async def ticktick_oauth_callback(code: str, state: str) -> dict[str, str]:
    return {"status": "received", "code": code, "state": state}
