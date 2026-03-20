from sqlalchemy.orm import Session

from ticktick_telegram_assistant.db.models.user import User


class UserRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_telegram_user_id(self, telegram_user_id: str) -> User | None:
        if self._session is None:
            return None
        return (
            self._session.query(User)
            .filter(User.telegram_user_id == telegram_user_id)
            .one_or_none()
        )

