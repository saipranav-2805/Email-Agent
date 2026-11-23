import json
from datetime import datetime
from typing import List, Optional
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), unique=True, nullable=False, index=True)
    aliases = Column(Text, default="[]")  # JSON encoded list of strings
    status = Column(String(50), default="interested", index=True)
    role = Column(String(255), nullable=True)
    ctc = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    emails = relationship("EmailRecord", back_populates="company", cascade="all, delete-orphan")

    def get_aliases_list(self) -> List[str]:
        try:
            return json.loads(self.aliases or "[]")
        except Exception:
            return []

    def set_aliases_list(self, aliases_list: List[str]):
        self.aliases = json.dumps(aliases_list)


class EmailRecord(Base):
    __tablename__ = "emails"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_inbox = Column(String(20), nullable=False, index=True)  # COLLEGE or PERSONAL
    message_id = Column(String(255), nullable=False, index=True)
    thread_id = Column(String(255), nullable=False, index=True)
    sender = Column(String(255), nullable=False)
    subject = Column(Text, nullable=False)
    body_text = Column(Text, nullable=False)
    received_at = Column(DateTime, nullable=False)
    processed_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Classification fields
    tier = Column(String(50), nullable=False, index=True)
    classification_json = Column(Text, nullable=True)
    attachment_scan_json = Column(Text, nullable=True)

    # Links & actions
    calendar_event_id = Column(String(255), nullable=True)
    alarm_sent = Column(Boolean, default=False)
    telegram_message_id = Column(Integer, nullable=True)

    # Relationships
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="SET NULL"), nullable=True, index=True)
    company = relationship("Company", back_populates="emails")

    reminders = relationship("ScheduledReminder", back_populates="email", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("source_inbox", "message_id", name="uq_source_message_id"),
    )


class UserPreference(Base):
    __tablename__ = "user_preferences"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_text = Column(Text, nullable=False)
    category = Column(String(50), default="general")
    is_active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ScheduledReminder(Base):
    __tablename__ = "scheduled_reminders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email_id = Column(Integer, ForeignKey("emails.id", ondelete="CASCADE"), nullable=True, index=True)
    company_name = Column(String(255), nullable=False)
    event_title = Column(String(255), nullable=False)
    event_time = Column(DateTime, nullable=False)
    calendar_event_id = Column(String(255), nullable=True)
    status = Column(String(50), default="scheduled")  # scheduled, synced, cancelled
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    email = relationship("EmailRecord", back_populates="reminders")
