import uuid
from datetime import datetime, date
from sqlalchemy import (
    String, Integer, Enum, DateTime, Date, Text,
    ForeignKey, UniqueConstraint, func
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


def new_uuid():
    return str(uuid.uuid4())


class Workspace(Base):
    __tablename__ = "workspaces"

    id:            Mapped[str]      = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    name:          Mapped[str]      = mapped_column(String(100), nullable=False)
    owner_user_id: Mapped[str]      = mapped_column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=True)
    seat_limit:    Mapped[int]      = mapped_column(Integer, default=5)
    created_at:    Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    users: Mapped[list["User"]] = relationship("User", back_populates="workspace",
                                               foreign_keys="User.workspace_id")


class User(Base):
    __tablename__ = "users"

    id:                    Mapped[str]           = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    wp_user_id:            Mapped[int]           = mapped_column(Integer, unique=True, nullable=False)
    email:                 Mapped[str]           = mapped_column(String(255), unique=True, nullable=False)
    plan:                  Mapped[str]           = mapped_column(
                               Enum("free", "pro", "pro_plus", "team", name="plan_enum"),
                               default="free", nullable=False)
    plan_status:           Mapped[str]           = mapped_column(
                               Enum("active", "past_due", "canceled", "trialing", name="plan_status_enum"),
                               default="active", nullable=False)
    stripe_customer_id:    Mapped[str | None]    = mapped_column(String(100), nullable=True)
    paypal_subscription_id:Mapped[str | None]    = mapped_column(String(100), nullable=True)
    billing_provider:      Mapped[str | None]    = mapped_column(
                               Enum("stripe", "paypal", name="provider_enum"),
                               nullable=True)
    workspace_id:          Mapped[str | None]    = mapped_column(UUID(as_uuid=False),
                               ForeignKey("workspaces.id"), nullable=True)
    language_pref:         Mapped[str]           = mapped_column(String(10), default="auto")
    created_at:            Mapped[datetime]      = mapped_column(DateTime(timezone=True), server_default=func.now())

    workspace:      Mapped["Workspace | None"] = relationship("Workspace", back_populates="users",
                                                               foreign_keys=[workspace_id])
    usage_records:  Mapped[list["UsageDaily"]]  = relationship("UsageDaily", back_populates="user",
                                                               cascade="all, delete-orphan")
    conversations:  Mapped[list["Conversation"]] = relationship("Conversation", back_populates="user",
                                                               cascade="all, delete-orphan")


class UsageDaily(Base):
    __tablename__ = "usage_daily"
    __table_args__ = (UniqueConstraint("user_id", "date", name="uq_user_date"),)

    id:            Mapped[str]  = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    user_id:       Mapped[str]  = mapped_column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=False)
    date:          Mapped[date] = mapped_column(Date, nullable=False)
    message_count: Mapped[int]  = mapped_column(Integer, default=0)
    upload_count:  Mapped[int]  = mapped_column(Integer, default=0)

    user: Mapped["User"] = relationship("User", back_populates="usage_records")


class Conversation(Base):
    __tablename__ = "conversations"

    id:              Mapped[str]           = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    user_id:         Mapped[str]           = mapped_column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=False)
    title:           Mapped[str | None]    = mapped_column(String(200), nullable=True)
    created_at:      Mapped[datetime]      = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user:     Mapped["User"]           = relationship("User", back_populates="conversations")
    messages: Mapped[list["Message"]]  = relationship("Message", back_populates="conversation",
                                                       cascade="all, delete-orphan",
                                                       order_by="Message.created_at")


class Message(Base):
    __tablename__ = "messages"

    id:              Mapped[str]      = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    conversation_id: Mapped[str]      = mapped_column(UUID(as_uuid=False), ForeignKey("conversations.id"), nullable=False)
    role:            Mapped[str]      = mapped_column(Enum("user", "assistant", name="role_enum"), nullable=False)
    content:         Mapped[str]      = mapped_column(Text, nullable=False)
    tokens_used:     Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at:      Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="messages")
