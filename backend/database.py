from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# SQLite 数据库文件路径（固定在 backend 目录下生成）
BASE_DIR = Path(__file__).resolve().parent
DB_FILE = BASE_DIR / "mindalpha.db"
SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_FILE.as_posix()}"

# 创建数据库引擎
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)

# 创建本地会话工厂
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 声明基类，我们后面的数据表都要继承它
Base = declarative_base()
