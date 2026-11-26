"""通用接口契约；模块字段由领域服务持有，不再为每个模块复制一份模型类。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20


class ActionResult(BaseModel):
    ok: bool
    message: str
    entry: dict[str, Any] | None = None


class EntryPayload(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    remark: str | None = None


class ActionPayload(BaseModel):
    action: str
    remark: str | None = None
    # 领办/回补时随动作提交的业务参数：领办人、可处理字段、修订值等。
    reviewer: str | None = None
    fields: list[str] = Field(default_factory=list)
    revised_values: dict[str, Any] = Field(default_factory=dict)


class ChecklistQuery(BaseModel):
    """坏行复核清单的查询条件：按批次必填，按失败字段可选。"""

    batch: str = ""
    fields: list[str] = Field(default_factory=list)


class ValidatePayload(BaseModel):
    """输出契约复验参数：不传行数时按回补后的实际行数复验。"""

    contract_no: str
    actual_columns: list[str] = Field(default_factory=list)
    actual_rows: int | None = None
    check_time: str = ""
