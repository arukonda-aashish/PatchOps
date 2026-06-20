"""Knowledge base models — static infrastructure knowledge"""
from datetime import datetime, timezone
from sqlalchemy import String, Text, Boolean, DateTime, Integer, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column
from app.db.session import Base
import enum


class DependencyEdge(Base):
    """
    Directed dependency graph: server A depends_on server B
    Meaning: B must be rebooted BEFORE A (B is a prerequisite).
    Stored as edges; we do topological sort at runtime.
    """
    __tablename__ = "dependency_edges"

    id: Mapped[int] = mapped_column(primary_key=True)
    # server A depends on server B → B boots first
    dependent_server: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    dependency_server: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )


class ScheduledRebootWindow(Base):
    """
    Servers that must be rebooted at a specific time-of-day in a given timezone.
    We resolve WHICH servers fall into this category at runtime by matching
    the server's detected timezone against this table.
    
    No server-specific rows needed — timezone + time window is the key.
    """
    __tablename__ = "scheduled_reboot_windows"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    timezone: Mapped[str] = mapped_column(String(100), nullable=False)
    # HH:MM format, 24h
    preferred_start_time: Mapped[str] = mapped_column(String(5), nullable=False)
    preferred_end_time: Mapped[str] = mapped_column(String(5), nullable=False)
    # Days of week: comma-separated 0=Mon..6=Sun
    allowed_days: Mapped[str] = mapped_column(String(20), default="0,1,2,3,4")
    reason: Mapped[str] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class ServicePauseConfig(Base):
    """
    Servers where a specific Windows service must be paused before reboot
    and resumed after. Shell scripts are invoked via WinRM.
    """
    __tablename__ = "service_pause_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    server_hostname: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    service_name: Mapped[str] = mapped_column(String(255), nullable=False)
    pause_script: Mapped[str] = mapped_column(String(512), default="Pause-Service.ps1")
    resume_script: Mapped[str] = mapped_column(String(512), default="Resume-Service.ps1")
    reason: Mapped[str] = mapped_column(Text, nullable=True)
    pre_pause_wait_seconds: Mapped[int] = mapped_column(Integer, default=5)
    post_resume_wait_seconds: Mapped[int] = mapped_column(Integer, default=10)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
class ServerKBDocument(Base):
    """
    Per-server knowledge base document in plain English.
    Describes applications running on the server, criticality rules,
    and operational constraints. Gemini reads this to generate
    custom pre/post reboot PowerShell scripts at execution time.
    """
    __tablename__ = "server_kb_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    server_hostname: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True
    )
    # Plain English document describing apps, rules, constraints
    document_content: Mapped[str] = mapped_column(Text, nullable=False)
    # Last generated scripts (cached for audit/display)
    last_pre_reboot_script: Mapped[str] = mapped_column(Text, nullable=True)
    last_post_reboot_script: Mapped[str] = mapped_column(Text, nullable=True)
    last_script_generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )


