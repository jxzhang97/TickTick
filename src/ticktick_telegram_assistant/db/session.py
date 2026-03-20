from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ticktick_telegram_assistant.config import Settings


def create_engine_from_settings(settings: Settings):
    return create_engine(settings.database_url)


def create_session_factory(settings: Settings) -> sessionmaker[Session]:
    engine = create_engine_from_settings(settings)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)

