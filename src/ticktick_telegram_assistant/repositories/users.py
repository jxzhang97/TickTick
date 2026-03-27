from sqlalchemy.orm import Session
from sqlalchemy import select

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

    def get_or_create(
        self,
        *,
        telegram_user_id: str,
        display_name: str | None = None,
    ) -> User:
        user = self.get_by_telegram_user_id(telegram_user_id)
        if user is not None:
            if display_name and not user.display_name:
                user.display_name = display_name
            return user

        user = User(telegram_user_id=telegram_user_id, display_name=display_name)
        if self._session is not None:
            self._session.add(user)
            self._session.flush()
        return user

    def list_connected_users(self) -> list[User]:
        if self._session is None:
            return []
        return list(
            self._session.scalars(
                select(User).where(User.ticktick_access_token.is_not(None))
            )
        )
