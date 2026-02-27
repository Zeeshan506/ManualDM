# create_tables.py
from sqlalchemy import inspect
from sqlalchemy.engine import create_engine
from app.core.database import Base
import os

# Import all models so they are registered in Base.metadata
from app.db.models import (
    WebhookEvent,
    Contact,
    Message,
    User,
    Lead,
    Invoice,
    PaymentEvent,
    MetaConversionEvent,
)

# Now Base.metadata knows about all tables
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("Please set DATABASE_URL environment variable")

engine = create_engine(DATABASE_URL)
inspector = inspect(engine)
existing_tables = inspector.get_table_names()
print("Existing tables in the database:", existing_tables)

tables_to_create = [
    table for table in Base.metadata.tables.values()
    if table.name != "alembic_version"
]

print("Tables to create (excluding alembic_version):", [t.name for t in tables_to_create])

for table in tables_to_create:
    if table.name not in existing_tables:
        print(f"Creating table: {table.name}")
        table.create(bind=engine, checkfirst=True)
    else:
        print(f"Skipping existing table: {table.name}")

print("All tables from models.py are now created (alembic_version skipped).")