from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, model_validator


class PreviewScenario(StrEnum):
    DEFAULT = "default"
    EMPTY = "empty"
    ERROR = "error"


SourceStatus = Literal["not_connected", "connected", "syncing", "partial", "error", "stale"]


class Source(BaseModel):
    id: str
    name: str
    description: str
    status: SourceStatus
    last_synced_at: AwareDatetime | None = None
    error_message: str | None = Field(
        default=None, description="Сообщение для интерфейса; технический код здесь не показывается."
    )


class Evidence(BaseModel):
    source_id: str
    title: str
    quote: str
    url: str | None = None
    published_at: AwareDatetime | None = None
    fetched_at: AwareDatetime | None = None
    external_id: str | None = None


class Task(BaseModel):
    id: str
    title: str
    course: str
    status: Literal["todo", "done"] = "todo"
    priority: Literal["high", "medium", "low"]
    due_at: AwareDatetime | None = Field(
        default=None, description="Точный срок с часовым поясом, только если он известен."
    )
    due_date: date | None = Field(
        default=None, description="Срок без известного времени. Не заменяется на 23:59."
    )
    estimated_minutes: int | None = Field(default=None, ge=1)
    materials: list[str] = Field(default_factory=list)
    evidence: list[Evidence]
    due_requires_confirmation: bool = False
    deadline_changed: str | None = None
    previous_due_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def one_deadline_representation(self) -> "Task":
        if self.due_at is not None and self.due_date is not None:
            raise ValueError("Укажите due_at или due_date, но не оба сразу")
        return self


class PlanBlock(BaseModel):
    task_id: str
    starts_at: AwareDatetime
    minutes: int = Field(ge=1)
    reason: str


class DayPlan(BaseModel):
    date: date
    available_minutes: int = Field(ge=0)
    blocks: list[PlanBlock] = Field(default_factory=list)
    unscheduled_task_ids: list[str] = Field(default_factory=list)
    note: str


class DashboardMeta(BaseModel):
    mode: Literal["demo", "unconfigured", "live"]
    generated_at: AwareDatetime
    timezone: str
    notice: str


class Material(BaseModel):
    id: str
    title: str
    course: str
    source_id: str
    url: str
    fetched_at: AwareDatetime


class Dashboard(BaseModel):
    meta: DashboardMeta
    tasks: list[Task]
    sources: list[Source]
    plan: DayPlan
    materials: list[Material] = Field(default_factory=list)


class ApiErrorDetail(BaseModel):
    code: str
    message: str
    retryable: bool = False


class ApiError(BaseModel):
    error: ApiErrorDetail


class Readiness(BaseModel):
    status: Literal["ready"] = "ready"
    database: Literal["connected"] = "connected"
