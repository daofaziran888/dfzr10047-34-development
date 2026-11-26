"""字段解析领域服务：按命中模板逐字段抽取取值并记录解析质量，产出解析字段数据集。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class FieldParseService(BaseModuleService):
    MODULE = "field_parse"
    ENTITY = "解析字段"
    OPERATION_TERM = "解析"
    LIST_FIELDS = ["解析编号", "关联批次", "命中模板", "字段名称", "原始取值", "解析取值", "解析耗时", "解析行号"]
    REQUIRED_FIELDS = ["解析编号", "关联批次", "命中模板"]
    STATUS_ORDER = ["待解析", "解析中", "解析成功", "部分失败"]
    ACTION_RULES = {"启动解析": "解析中", "确认成功": "解析成功", "登记部分失败": "部分失败"}
    NEGATIVE_ACTIONS = ["登记部分失败"]

    def record_parsed_field(self, parse_no: str, intake_batch: str, template_no: str,
                            field_name: str, raw_value: str, parsed_value: str,
                            elapsed_ms: int, row_num: int,
                            success: bool) -> dict[str, Any]:
        """登记单字段解析结果，解析失败的字段连同行号可供坏行隔离消费。"""
        rows = store.rows(self.MODULE)
        entry_id = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        entry = {
            "id": entry_id,
            "status": "解析成功" if success else "部分失败",
            "pending": not success,
            "abnormal": not success,
            "解析编号": parse_no,
            "关联批次": intake_batch,
            "命中模板": template_no,
            "字段名称": field_name,
            "原始取值": raw_value,
            "解析取值": parsed_value,
            "解析耗时": str(elapsed_ms),
            "解析行号": str(row_num),
        }
        rows.append(entry)
        return entry

    def write_back_revision(self, intake_batch: str, row_num: str,
                            values: dict[str, Any], reviewer: str,
                            review_time: str) -> list[dict[str, Any]]:
        """把坏行复核确认的修订值写回解析批次。

        同一原行的字段解析明细只保留一条有效记录：已存在该字段就改写解析取值并置为
        解析成功，不存在才新增。这样重复回补不会在批次里造出第二条成功行。
        """
        rows = store.rows(self.MODULE)
        written: list[dict[str, Any]] = []
        for field_name, value in values.items():
            text = "" if value is None else str(value)
            target = next(
                (r for r in rows
                 if str(r.get("关联批次")) == str(intake_batch)
                 and str(r.get("解析行号")) == str(row_num)
                 and str(r.get("字段名称")) == str(field_name)),
                None)
            if target is not None:
                target["解析取值"] = text
                target["status"] = "解析成功"
                target["pending"] = False
                target["abnormal"] = False
                target["复核人"] = reviewer
                target["复核时间"] = review_time
                written.append(dict(target))
                continue
            template = next(
                (r.get("命中模板") for r in rows
                 if str(r.get("关联批次")) == str(intake_batch)
                 and str(r.get("解析行号")) == str(row_num) and r.get("命中模板")),
                "")
            entry = {
                "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
                "status": "解析成功",
                "pending": False,
                "abnormal": False,
                "解析编号": f"FP-{intake_batch}-{row_num}-{field_name}",
                "关联批次": intake_batch,
                "命中模板": template or "",
                "字段名称": str(field_name),
                "原始取值": "",
                "解析取值": text,
                "解析耗时": "0",
                "解析行号": str(row_num),
                "复核人": reviewer,
                "复核时间": review_time,
                "复核回补": "是",
            }
            rows.append(entry)
            written.append(dict(entry))
        # 同一行其余字段若有部分失败记录，行状态以聚合后的真实结果为准。
        self._settle_row_status(intake_batch, row_num)
        return written

    def _settle_row_status(self, intake_batch: str, row_num: str) -> None:
        row_fields = [
            r for r in store.rows(self.MODULE)
            if str(r.get("关联批次")) == str(intake_batch)
            and str(r.get("解析行号")) == str(row_num)
        ]
        if not row_fields:
            return
        still_failed = any(r.get("status") == "部分失败" for r in row_fields)
        for r in row_fields:
            if r.get("status") in ("解析成功", "部分失败"):
                r["status"] = "部分失败" if still_failed else "解析成功"
                r["pending"] = still_failed
                r["abnormal"] = still_failed

    def failed_fields_of_batch(self, intake_batch: str) -> list[dict[str, Any]]:
        """输出解析失败字段清单（含行号），坏行隔离据此建隔离行。"""
        return [
            {
                "解析编号": r.get("解析编号"),
                "字段名称": r.get("字段名称"),
                "原始取值": r.get("原始取值"),
                "解析行号": r.get("解析行号"),
            }
            for r in store.rows(self.MODULE)
            if r.get("关联批次") == intake_batch and r.get("status") == "部分失败"
        ]

    def parsed_rows_of_batch(self, intake_batch: str) -> dict[str, dict[str, Any]]:
        """按行号聚合解析成功的取值，供抽样复核与输出契约取数。"""
        grouped: dict[str, dict[str, Any]] = {}
        for r in store.rows(self.MODULE):
            if r.get("关联批次") != intake_batch:
                continue
            line = str(r.get("解析行号") or "")
            if not line:
                continue
            bucket = grouped.setdefault(line, {})
            bucket[str(r.get("字段名称"))] = r.get("解析取值")
            bucket["__status"] = (
                "部分失败" if r.get("status") == "部分失败"
                else bucket.get("__status", "解析成功")
            )
        return grouped

    def success_rate(self, intake_batch: str) -> float:
        """批次字段级解析成功率，供解析指标交叉核对。"""
        rows = [r for r in store.rows(self.MODULE) if r.get("关联批次") == intake_batch]
        if not rows:
            return 0.0
        return sum(1 for r in rows if r.get("status") == "解析成功") / len(rows)

    def template_hit_stats(self) -> dict[str, int]:
        """按命中模板统计解析字段数，供模板版本过渡期命中分布核对。"""
        stats: dict[str, int] = {}
        for r in store.rows(self.MODULE):
            key = str(r.get("命中模板") or "")
            if key:
                stats[key] = stats.get(key, 0) + 1
        return stats
