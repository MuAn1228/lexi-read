from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from backend.config import get_settings
from backend.models import Base, User

settings = get_settings()
engine = create_engine(settings.db_url, connect_args={"check_same_thread": False, "timeout": 30})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


@event.listens_for(engine, "connect")
def sqlite_configuration(dbapi_connection, _):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


def initialize_database() -> None:
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)
    with SessionLocal() as session:
        if not session.get(User, 1):
            session.add(User(id=1))
            session.commit()


def get_session():
    with SessionLocal() as session:
        yield session
