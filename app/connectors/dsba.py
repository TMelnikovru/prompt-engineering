"""Read the same public JSON endpoints as dsba.online's web client."""

import hashlib
import json
import re
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.config import Settings
from app.connectors.base import DocumentRecord


class DsbaError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class DsbaSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    program: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$")
    year: int = Field(ge=1, le=6)
    group: str = Field(default="", max_length=64)
    sub: str = Field(default="", max_length=8)
    subjects: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("subjects")
    @classmethod
    def unique_subjects(cls, value: list[str]) -> list[str]:
        if any(not re.fullmatch(r"[a-z0-9-]{1,100}", item) for item in value):
            raise ValueError("Некорректный идентификатор предмета")
        return sorted(set(value))

    def query(self) -> dict[str, str | int]:
        result: dict[str, str | int] = {"program": self.program, "year": self.year}
        if self.group:
            result["group"] = self.group
        if self.sub:
            result["sub"] = self.sub
        if self.subjects:
            result["mine"] = ",".join(self.subjects)
        return result

    @property
    def source_id(self) -> str:
        value = json.dumps(self.model_dump(), sort_keys=True, ensure_ascii=False).encode()
        return "dsba:" + hashlib.sha256(value).hexdigest()[:32]


class DsbaBatch(BaseModel):
    records: list[DocumentRecord] = Field(default_factory=list)
    errors: list[dict[str, str]] = Field(default_factory=list)
    complete_kinds: set[str] = Field(default_factory=set)
    has_more: bool = False


class DsbaYear(BaseModel):
    year: int = Field(ge=1, le=6)
    label: str
    groups: list[str]
    subgroups: list[str]
    subjects: list[str]
    names: dict[str, str]


class DsbaProgram(BaseModel):
    id: str
    name: str
    title: str
    years: list[DsbaYear] = Field(min_length=1)


class DsbaCatalog(BaseModel):
    academic_year: str
    programs: list[DsbaProgram] = Field(min_length=1, max_length=100)


def public_url(value: Any, base: str) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        result = urljoin(base + "/", value)
        parsed = urlsplit(result)
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    return result


def publication(value: Any) -> datetime | None:
    try:
        result = datetime.fromisoformat(value)
        return result if result.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def deadline(value: Any, all_day: Any) -> dict[str, str | None]:
    result: dict[str, str | None] = {"due_at": None, "due_date": None}
    if value in (None, ""):
        return result
    if not isinstance(value, str):
        raise DsbaError("dsba_invalid_deadline")
    if all_day not in (None, True, False, 0, 1):
        raise DsbaError("dsba_invalid_deadline")
    try:
        if all_day or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            result["due_date"] = date.fromisoformat(value).isoformat()
        else:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                raise DsbaError("dsba_deadline_timezone_unknown")
            result["due_at"] = parsed.isoformat()
    except ValueError:
        raise DsbaError("dsba_invalid_deadline") from None
    return result


def normalize_record(
    kind: str, raw: dict, subjects: dict[str, str], selection: DsbaSelection, base: str
) -> DocumentRecord:
    external_id = raw.get("id")
    if isinstance(external_id, bool) or not isinstance(external_id, (str, int)):
        raise DsbaError("dsba_record_without_id")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", str(external_id)):
        raise DsbaError("dsba_invalid_record_id")
    subject_id = str(raw.get("subject_id") or "")
    course = subjects.get(subject_id) or str(raw.get("course") or subject_id)
    if not course:
        course = "Предмет не указан"
    if kind == "deadline":
        fields = (
            "id",
            "subject_id",
            "type",
            "title",
            "number",
            "due_at",
            "all_day",
            "url",
            "details",
            "course",
            "source",
            "updated_at",
            "group",
            "conflict",
        )
        title = str(raw.get("title") or "Задание без названия")
        timing = deadline(raw.get("due_at"), raw.get("all_day"))
        if raw.get("conflict"):
            timing = {"due_at": None, "due_date": None}
        facts = {
            **timing,
            "course": course,
            "title": title,
            "due_raw": raw.get("due_at"),
            "conflict": raw.get("conflict"),
            "type": raw.get("type"),
        }
        url = base + "/#deadlines"
    else:
        fields = (
            "id",
            "kind",
            "subject_id",
            "number",
            "sheet",
            "date",
            "tag",
            "title",
            "ext",
            "url",
            "size",
            "source",
            "created_at",
            "updated_at",
            "has_file",
        )
        label = {"lecture": "Лекция", "seminar": "Семинар"}.get(raw.get("kind"), "Материал")
        title = str(raw.get("title") or f"{label} {raw.get('number') or ''}".strip())
        facts = {}
        url = base + "/#m/" + str(external_id)
    # Keep source evidence, without unrelated author/account fields.
    evidence = {key: raw.get(key) for key in fields}
    lines = [title, f"Предмет: {course}"]
    if kind == "deadline":
        lines.append(f"Срок в DSBA: {raw.get('due_at') or 'не указан'}")
        if facts.get("due_date"):
            lines.append("Источник указал дату без времени.")
        if raw.get("conflict"):
            lines.append(
                "Противоречащая запись: " + json.dumps(raw["conflict"], ensure_ascii=False)
            )
        if isinstance(raw.get("details"), str) and raw["details"]:
            lines.append(raw["details"])
    elif raw.get("date"):
        lines.append(f"Дата занятия в источнике: {raw['date']}")
    text = "\n".join(lines)
    content = json.dumps(
        {"record": evidence, "course": course, "normalizer": 1},
        ensure_ascii=False,
        sort_keys=True,
    )
    digest = hashlib.sha256(content.encode()).hexdigest()
    links = []
    if original := public_url(raw.get("url"), base):
        links.append({"url": original, "title": "Первоисточник"})
    if kind == "material" and raw.get("has_file"):
        links.append({"url": base + "/files/" + str(external_id), "title": "Файл материала"})
    return DocumentRecord(
        source_id=selection.source_id,
        external_id=f"{kind}:{external_id}",
        title=f"{course} · {title}",
        text=text,
        raw_content=json.dumps(evidence, ensure_ascii=False, sort_keys=True),
        url=url,
        published_at=publication(raw.get("created_at")),
        fetched_at=datetime.now(UTC),
        content_type="application/json",
        revision_key=digest,
        content_hash=digest,
        links=links,
        attributes={"kind": kind, "subject_id": subject_id, "course": course, "facts": facts},
    )


class DsbaConnector:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.base = settings.dsba_base_url
        self.client = httpx.AsyncClient(
            timeout=settings.dsba_request_timeout,
            follow_redirects=False,
            headers={"User-Agent": "HSEStudyAgent/0.1 (read-only DSBA integration)"},
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.client.aclose()

    async def get(self, path: str, params: dict | None = None) -> Any:
        try:
            async with self.client.stream("GET", self.base + path, params=params) as response:
                if response.status_code != 200:
                    raise DsbaError(f"dsba_http_{response.status_code}")
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > 8_000_000:
                        raise DsbaError("dsba_response_too_large")
                return json.loads(content)
        except httpx.TimeoutException:
            raise DsbaError("dsba_timeout") from None
        except httpx.HTTPError:
            raise DsbaError("dsba_network_error") from None
        except (ValueError, UnicodeDecodeError):
            raise DsbaError("dsba_invalid_response") from None

    async def catalog(self) -> dict:
        raw = await self.get("/api/config")
        if not isinstance(raw, dict) or raw.get("access") != "open":
            raise DsbaError("dsba_public_access_unavailable")
        programs = raw.get("catalog")
        if not isinstance(programs, list):
            raise DsbaError("dsba_invalid_catalog")
        try:
            result = {
                "academic_year": str(raw.get("academicYear") or ""),
                "programs": [
                    {
                        "id": p["id"],
                        "name": p["name"],
                        "title": p.get("title", p["name"]),
                        "years": [
                            {
                                "year": y["year"],
                                "label": y.get("label", str(y["year"])),
                                "groups": y.get("groups", []),
                                "subgroups": y.get("subgroups", []),
                                "subjects": y.get("subjects", []),
                                "names": y.get("names", {}),
                            }
                            for y in p["years"]
                        ],
                    }
                    for p in programs
                ],
            }
            return DsbaCatalog.model_validate(result).model_dump()
        except (TypeError, KeyError, AttributeError, ValidationError):
            raise DsbaError("dsba_invalid_catalog") from None

    @staticmethod
    def validate(selection: DsbaSelection, catalog: dict) -> None:
        year = next(
            (
                y
                for p in catalog["programs"]
                if p["id"] == selection.program
                for y in p["years"]
                if y["year"] == selection.year
            ),
            None,
        )
        if year is None or (year["groups"] and selection.group not in year["groups"]):
            raise DsbaError("dsba_invalid_selection")
        if not year["groups"] and selection.group:
            raise DsbaError("dsba_invalid_selection")
        if selection.sub and selection.sub not in year["subgroups"]:
            raise DsbaError("dsba_invalid_selection")
        if not set(selection.subjects) <= set(year["subjects"]):
            raise DsbaError("dsba_invalid_selection")

    async def collect(self, selection: DsbaSelection) -> DsbaBatch:
        result = DsbaBatch()
        try:
            raw_subjects = await self.get("/api/subjects", selection.query())
            if not isinstance(raw_subjects, list):
                raise DsbaError("dsba_invalid_subjects")
            if any(
                not isinstance(s, dict)
                or not isinstance(s.get("id"), str)
                or not isinstance(s.get("name"), str)
                for s in raw_subjects
            ):
                raise DsbaError("dsba_invalid_subjects")
            subjects = {str(s["id"]): str(s["name"]) for s in raw_subjects}
        except (DsbaError, KeyError, TypeError) as exc:
            result.errors.append(
                {"title": "subjects", "code": getattr(exc, "code", "dsba_invalid_subjects")}
            )
            return result
        for path, kind in (("/api/deadlines", "deadline"), ("/api/materials", "material")):
            try:
                values = await self.get(path, selection.query())
                if not isinstance(values, list):
                    raise DsbaError("dsba_invalid_response")
                complete = True
                if len(values) > self.settings.dsba_max_records:
                    result.has_more = True
                    complete = False
                    result.errors.append({"title": kind, "code": "dsba_record_limit"})
                    values = values[: self.settings.dsba_max_records]
                for raw in values:
                    try:
                        if not isinstance(raw, dict):
                            raise DsbaError("dsba_invalid_record")
                        result.records.append(
                            normalize_record(kind, raw, subjects, selection, self.base)
                        )
                    except DsbaError as exc:
                        complete = False
                        result.errors.append({"title": kind, "code": exc.code})
                if complete:
                    result.complete_kinds.add(kind)
            except DsbaError as exc:
                result.errors.append({"title": kind, "code": exc.code})
        return result
