"""统一资源接口：查询、导出、登记和动作按模块 key 分派到领域服务。"""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import (
    ActionPayload,
    ActionResult,
    EntryPayload,
    PageResult,
    ValidatePayload,
)
from app.services import service_for

router = APIRouter(prefix="/api", tags=["业务资源"])


def require_service(module: str):
    service = service_for(module)
    if service is None:
        raise HTTPException(status_code=404, detail=f"未知业务模块：{module}")
    return service


@router.get("/{module}", response_model=PageResult[dict])
def list_entries(
    module: str,
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    service = require_service(module)
    filters = {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in service.LIST_FIELDS and value.strip()
    }
    items, total = service.list_entries(
        filters=filters, keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{module}/export")
def export_entries(
    module: str,
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> StreamingResponse:
    service = require_service(module)
    filters = {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in service.LIST_FIELDS and value.strip()
    }
    items, _ = service.list_entries(
        filters=filters, keyword=keyword, status=status, page=1, size=1_000_000)
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["id", *service.LIST_FIELDS, "status"])
    for item in items:
        values = [item.get("id"), *(item.get(field, "") for field in service.LIST_FIELDS),
                  item.get("status", "")]
        writer.writerow([
            "'" + str(value) if str(value or "").startswith(("=", "+", "-", "@")) else value
            for value in values
        ])
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{module}.csv"'},
    )


@router.get("/row_quarantine/checklist")
def quarantine_checklist(
    batch: str = Query(default="", description="关联批次，必填"),
    fields: str = Query(default="", description="按失败字段筛选，逗号分隔"),
    field: str = Query(default="", description="单个失败字段筛选（fields 的单值写法）"),
) -> dict[str, Any]:
    """坏行复核清单：按批次(必填)和失败字段(可选)查询，按原行排列并列出证据。"""
    service = require_service("row_quarantine")
    if not batch.strip():
        raise HTTPException(status_code=400, detail="必须指定批次")
    raw = fields or field
    selected = [f.strip() for f in raw.replace("，", ",").split(",") if f.strip()]
    items = service.filter_checklist(batch.strip(), selected)
    return {"items": items, "total": len(items), "batch": batch.strip(),
            "fields": selected}


@router.get("/row_quarantine/evidence")
def quarantine_evidence(batch: str = Query(default=""), row: str = Query(default="")) -> dict[str, Any]:
    """原行回查：历史原文与后来补入的失败证据按时间顺序返回。"""
    service = require_service("row_quarantine")
    if not batch.strip() or not row.strip():
        raise HTTPException(status_code=400, detail="必须指定批次和原始行号")
    return service.row_evidence_trail(batch.strip(), row.strip())


@router.get("/row_quarantine/{quarantine_no}/receipt")
def quarantine_receipt(quarantine_no: str) -> dict[str, Any]:
    """变更回执：保留已填修订值，指明还能提交什么、哪些需要重新领取。"""
    service = require_service("row_quarantine")
    receipt = service.change_receipt(quarantine_no)
    if receipt is None:
        raise HTTPException(status_code=404, detail=f"隔离行 {quarantine_no} 不存在")
    return receipt


@router.post("/row_quarantine/{quarantine_no}/revise", response_model=ActionResult)
def quarantine_revise(quarantine_no: str, payload: ActionPayload) -> ActionResult:
    """提交复核修订值并回补批次；未核完的字段会让原行继续留在隔离区。"""
    service = require_service("row_quarantine")
    reviewer = (payload.reviewer or "").strip()
    if not reviewer:
        return ActionResult(ok=False, message="缺少复核人标识，无法登记修订值")
    row, message = service.apply_revision(
        quarantine_no, reviewer, payload.revised_values,
        payload.remark or "")
    return ActionResult(ok=row is not None, message=message, entry=row)


@router.post("/output_contract/validate", response_model=ActionResult)
def output_contract_validate(payload: ValidatePayload) -> ActionResult:
    """按回补后的实际行数复验输出契约，行数不传时取真实结果行数。"""
    service = require_service("output_contract")
    passed, failures, contract = service.revalidate_after_reinstate(
        payload.contract_no, payload.actual_columns, payload.actual_rows,
        payload.check_time)
    return ActionResult(
        ok=passed,
        message=("契约校验通过" if passed else ";".join(failures) or "契约校验未通过"),
        entry=contract,
    )


@router.get("/{module}/{entry_id}", response_model=dict)
def get_entry(module: str, entry_id: int) -> dict[str, Any]:
    service = require_service(module)
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"{service.ENTITY} {entry_id} 不存在或已归档")
    return entry

@router.post("/{module}", response_model=ActionResult)
def create_entry(module: str, payload: EntryPayload) -> ActionResult:
    service = require_service(module)
    entry, problems = service.create_entry(payload.values)
    if problems:
        return ActionResult(ok=False, message=service.create_error_message(problems))
    return ActionResult(ok=True, message=f"{service.ENTITY}已登记", entry=entry)


@router.post("/{module}/{entry_id}/actions", response_model=ActionResult)
def run_action(module: str, entry_id: int, payload: ActionPayload) -> ActionResult:
    service = require_service(module)
    entry, message = service.run_action(
        entry_id, payload.action.strip(),
        payload.model_dump(exclude_none=True),
    )
    return ActionResult(ok=entry is not None, message=message, entry=entry)
