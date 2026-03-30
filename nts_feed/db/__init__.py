from sqlalchemy.orm import sessionmaker


def init_sessionmaker(engine):
    # Use expire_on_commit=False so objects remain usable after commit in long-running tasks
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)


