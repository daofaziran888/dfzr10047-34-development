"""坏行隔离领域服务：归集解析失败行及其失败原因，支持复核回补流程，产出隔离行数据集。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


# 只有这些动作会真正改变复核状态；其余动作交给通用骨架判定。
REVIEW_ACTIONS = {"领办", "登记修订值", "回补批次", "废弃隔离行"}


class RowQuarantineService(BaseModuleService):
    MODULE = "row_quarantine"
    ENTITY = "隔离行"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["隔离编号", "关联批次", "原始行号", "失败字段", "失败原因", "原始行文本", "复核结论", "隔离时间"]
    REQUIRED_FIELDS = ["隔离编号", "关联批次", "原始行号"]
    STATUS_ORDER = ["已隔离", "复核中", "已回补", "已废弃"]
    ACTION_RULES = {"提交复核": "复核中", "回补批次": "已回补", "废弃隔离行": "已废弃"}
    NEGATIVE_ACTIONS = ["废弃隔离行"]

    # 只有这些失败原因允许人工回补，其余必须走废弃
    REINSTATABLE_REASONS = ("日期格式", "格式异常", "人工确认", "可修正")

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """登记入口：坏行隔离只走失败证据登记，同原行的新字段作为后续证据并入。

        路由层的通用登记不能直接造出重复隔离行，否则同一原行会出现两条隔离记录，
        批次隔离计数与复核清单都会失真。
        """
        missing = [
            field for field in self.REQUIRED_FIELDS
            if not str(values.get(field) or "").strip()
        ]
        if missing:
            return None, missing
        row_text = str(values.get("原始行号") or "").strip()
        if not row_text.isdigit():
            return None, [f"原始行号「{row_text}」必须是数字行号"]
        ok, message, entry = self.quarantine_failure(
            str(values.get("关联批次") or ""),
            int(row_text),
            str(values.get("原始行文本") or ""),
            values.get("失败字段") or "",
            str(values.get("失败原因") or ""),
            str(values.get("隔离时间") or ""),
        )
        if not ok:
            return None, [message]
        return entry, []

    def create_error_message(self, problems: list[str]) -> str:
        """登记失败提示：可能缺必填字段，也可能是重复证据被拒绝。"""
        if len(problems) == 1 and ("重复" in problems[0] or "已在隔离清单" in problems[0]):
            return problems[0]
        return super().create_error_message(problems)

    # 复核清单动作入口：领办判定由服务端完成，路由层不做业务判断。

    def run_action(self, entry_id: int, action: str,
                   payload: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str]:
        """隔离行动作入口：领办 / 登记修订值 / 回补批次 走复核流程。

        复核回补必须带修订值，不再沿用通用状态跳转，避免绕过证据范围校验。
        """
        payload = payload or {}
        row = store.find(self.MODULE, entry_id)
        if row is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        quarantine_no = str(row.get("隔离编号") or "")
        reviewer = str(payload.get("reviewer") or "").strip()
        fields = payload.get("fields") or []
        values = payload.get("revised_values") or {}
        remark = str(payload.get("remark") or "")

        if action == "领办":
            if not reviewer:
                return None, "缺少复核人标识，无法领办"
            ok, message, _row = self.claim_row(quarantine_no, reviewer, fields, remark)
            return (row if ok else None), message

        if action in ("登记修订值", "回补批次"):
            if not reviewer:
                return None, "缺少复核人标识，无法登记修订值"
            ok, message, updated = self.apply_revision(
                quarantine_no, reviewer, values, remark)
            return (updated if ok else None), message

        if action == "废弃隔离行":
            if not reviewer:
                return None, "缺少复核人标识，无法废弃"
            ok, message = self.discard_row(quarantine_no, reviewer, remark or "复核废弃")
            return (row if ok else None), message

        if action == "提交复核":
            ok, message = self.submit_review(quarantine_no, reviewer or "system")
            return (row if ok else None), message

        return super().run_action(entry_id, action, payload)

    # 证据表：同一原行可能先后出现多条失败证据（不同字段、不同时间补入）
    EVIDENCE_TABLE = "_quarantine_evidence"
    # 领办台账：记录每一份证据被谁领取、可修正的字段范围
    CLAIM_TABLE = "_quarantine_claims"

    # ------------------------------------------------------------------
    # 证据台账
    # ------------------------------------------------------------------
    def _evidence_rows(self) -> list[dict[str, Any]]:
        return store.rows(self.EVIDENCE_TABLE)

    def _claims(self) -> list[dict[str, Any]]:
        return store.rows(self.CLAIM_TABLE)

    @staticmethod
    def _norm_fields(failed_fields: Any) -> list[str]:
        """失败字段统一成有序去重的字段名列表，兼容字符串与列表。"""
        if isinstance(failed_fields, str):
            parts = failed_fields.replace("，", ",").split(",")
        elif isinstance(failed_fields, (list, tuple)):
            parts = [str(p) for p in failed_fields]
        else:
            parts = [str(failed_fields or "")]
        return [p.strip() for p in parts if p.strip()]

    @staticmethod
    def _field_key(fields: list[str]) -> str:
        return ",".join(sorted(fields))

    def _ensure_evidence(self) -> None:
        """把 seed 里的隔离行视作第一份历史证据；调用幂等，可重复执行。"""
        evidence = self._evidence_rows()
        known = {(e.get("隔离编号"), e.get("证据序号")) for e in evidence}
        seq_by_row: dict[tuple[str, str], int] = {}
        for e in evidence:
            key = (str(e.get("关联批次")), str(e.get("原始行号")))
            seq_by_row[key] = max(seq_by_row.get(key, 0), int(e.get("证据序号") or 0))
        for row in store.rows(self.MODULE):
            if (row.get("隔离编号"), 1) in known:
                continue
            key = (str(row.get("关联批次")), str(row.get("原始行号")))
            seq = seq_by_row.get(key, 0) + 1
            seq_by_row[key] = seq
            evidence.append({
                "id": len(evidence) + 1,
                "隔离编号": row.get("隔离编号"),
                "关联批次": row.get("关联批次"),
                "原始行号": str(row.get("原始行号")),
                "证据序号": seq,
                "失败字段": self._norm_fields(row.get("失败字段")),
                "失败原因": row.get("失败原因") or "",
                "原始行文本": row.get("原始行文本") or "",
                "证据来源": "解析隔离",
                "登记时间": row.get("隔离时间") or "",
            })

    def _evidence_of_row(self, intake_batch: str, row_num: Any) -> list[dict[str, Any]]:
        self._ensure_evidence()
        return [
            e for e in self._evidence_rows()
            if str(e.get("关联批次")) == str(intake_batch)
            and str(e.get("原始行号")) == str(row_num)
        ]

    def _evidence_of_quarantine(self, quarantine_no: str) -> list[dict[str, Any]]:
        self._ensure_evidence()
        return [e for e in self._evidence_rows() if e.get("隔离编号") == quarantine_no]

    def _find_row(self, quarantine_no: str) -> dict[str, Any] | None:
        for row in store.rows(self.MODULE):
            if row.get("隔离编号") == quarantine_no:
                return row
        return None

    # ------------------------------------------------------------------
    # 隔离登记
    # ------------------------------------------------------------------
    def quarantine_failure(self, intake_batch: str, row_num: int, raw_text: str,
                           failed_field: Any, reason: str,
                           quarantine_time: str) -> tuple[bool, str, dict[str, Any] | None]:
        """登记隔离失败证据。

        同一批次同一行号可以登记多份证据（不同字段的失败可以分别补入），
        但同一字段组合不得重复登记，避免同一失败证据把隔离计数虚高。
        """
        rows = store.rows(self.MODULE)
        fields = self._norm_fields(failed_field)
        field_key = self._field_key(fields)

        same_row = [
            r for r in rows
            if r.get("关联批次") == intake_batch and str(r.get("原始行号")) == str(row_num)
        ]
        for existing in same_row:
            existing_evidence = self._evidence_of_quarantine(str(existing.get("隔离编号")))
            for evidence in existing_evidence:
                if self._field_key(self._norm_fields(evidence.get("失败字段"))) == field_key:
                    return False, (
                        f"批次 {intake_batch} 行 {row_num} 的失败字段"
                        f"「{'、'.join(fields)}」已在隔离清单中，不得重复登记"
                    ), None

        if same_row:
            # 同一原行已有隔离行：把新字段作为后续证据补入，不新建隔离行。
            target = same_row[0]
            self._append_evidence(target, fields, reason, raw_text,
                                  "补入失败证据", quarantine_time)
            target["失败字段"] = ",".join(self._merged_fields(target))
            if target.get("status") in ("已回补", "已废弃"):
                # 新证据让终态结论失效，必须重新进入复核。
                target["status"] = "复核中"
                target["pending"] = True
                target["abnormal"] = False
                if target.get("已计入成功"):
                    # 已回补的原行被新证据推翻，批次成功行要退回。
                    self._settle_batch_counts(target)
            return True, (
                f"批次 {intake_batch} 行 {row_num} 新增失败证据"
                f"（{'、'.join(fields)}: {reason}），已并入隔离行 {target.get('隔离编号')}"
            ), dict(target)

        entry_id = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        next_seq = max(
            (int(e.get("证据序号") or 0) for e in self._evidence_rows()
             if str(e.get("关联批次")) == str(intake_batch)
             and str(e.get("原始行号")) == str(row_num)),
            default=0) + 1
        entry = {
            "id": entry_id,
            "status": "已隔离",
            "pending": True,
            "abnormal": False,
            "隔离编号": f"RQ-{intake_batch}-{row_num}",
            "关联批次": intake_batch,
            "原始行号": str(row_num),
            "失败字段": ",".join(fields),
            "失败原因": reason,
            "原始行文本": raw_text,
            "复核结论": "",
            "隔离时间": quarantine_time,
        }
        rows.append(entry)
        self._evidence_rows().append({
            "id": len(self._evidence_rows()) + 1,
            "隔离编号": entry["隔离编号"],
            "关联批次": intake_batch,
            "原始行号": str(row_num),
            "证据序号": next_seq,
            "失败字段": fields,
            "失败原因": reason,
            "原始行文本": raw_text,
            "证据来源": "解析隔离",
            "登记时间": quarantine_time,
        })
        return True, (
            f"批次 {intake_batch} 行 {row_num} 已隔离（{'、'.join(fields)}: {reason}）"
        ), dict(entry)

    def _append_evidence(self, row: dict[str, Any], fields: list[str], reason: str,
                         raw_text: str, source: str, time_text: str) -> dict[str, Any]:
        evidence = self._evidence_rows()
        same_row = [
            e for e in evidence
            if e.get("隔离编号") == row.get("隔离编号")
            or (str(e.get("关联批次")) == str(row.get("关联批次"))
                and str(e.get("原始行号")) == str(row.get("原始行号")))
        ]
        next_seq = max((int(e.get("证据序号") or 0) for e in same_row), default=0) + 1
        record = {
            "id": max((int(e.get("id", 0)) for e in evidence), default=0) + 1,
            "隔离编号": row.get("隔离编号"),
            "关联批次": row.get("关联批次"),
            "原始行号": str(row.get("原始行号")),
            "证据序号": next_seq,
            "失败字段": list(fields),
            "失败原因": reason,
            "原始行文本": raw_text,
            "证据来源": source,
            "登记时间": time_text,
        }
        evidence.append(record)
        return record

    def _merged_fields(self, row: dict[str, Any]) -> list[str]:
        merged: list[str] = []
        for evidence in self._evidence_of_quarantine(str(row.get("隔离编号"))):
            for field in self._norm_fields(evidence.get("失败字段")):
                if field not in merged:
                    merged.append(field)
        return merged or self._norm_fields(row.get("失败字段"))

    # ------------------------------------------------------------------
    # 复核清单与领办
    # ------------------------------------------------------------------
    def review_checklist(self, intake_batch: str) -> list[dict[str, Any]]:
        """按批次和字段输出坏行复核清单：同一原行的多份证据合并成一行，按原行排序。

        每行给出失败字段全集，以及每份证据的原文、原因、来源与登记时间，
        供复核员展开查看历史原文和后来补入的错误证据。
        """
        self._ensure_evidence()
        rows = [
            r for r in store.rows(self.MODULE)
            if str(r.get("关联批次")) == str(intake_batch)
        ]
        rows.sort(key=lambda r: _row_sort_key(r.get("原始行号")))
        checklist: list[dict[str, Any]] = []
        for row in rows:
            evidence = sorted(
                self._evidence_of_quarantine(str(row.get("隔离编号"))),
                key=lambda e: int(e.get("证据序号") or 0))
            fields = self._merged_fields(row)
            checklist.append({
                "隔离编号": row.get("隔离编号"),
                "关联批次": row.get("关联批次"),
                "原始行号": str(row.get("原始行号")),
                "失败字段": fields,
                "失败原因": row.get("失败原因"),
                "原始行文本": row.get("原始行文本"),
                "status": row.get("status"),
                "复核结论": row.get("复核结论"),
                "证据": [
                    {
                        "证据序号": e.get("证据序号"),
                        "失败字段": self._norm_fields(e.get("失败字段")),
                        "失败原因": e.get("失败原因"),
                        "原始行文本": e.get("原始行文本"),
                        "证据来源": e.get("证据来源"),
                        "登记时间": e.get("登记时间"),
                    }
                    for e in evidence
                ],
            })
        return checklist

    def filter_checklist(self, intake_batch: str,
                         fields: list[str] | None = None) -> list[dict[str, Any]]:
        """按失败字段筛选复核清单：命中任一字段即保留，便于按字段分派复核。"""
        wanted = {f.strip() for f in (fields or []) if f.strip()}
        checklist = self.review_checklist(intake_batch)
        if not wanted:
            return checklist
        return [item for item in checklist if wanted & set(item["失败字段"])]

    def _evidence_scope(self, row: dict[str, Any], fields: list[str]) -> tuple[bool, str]:
        """服务端判定可处理范围：字段必须在原行证据里真实出现过。"""
        known = self._merged_fields(row)
        unknown = [f for f in fields if f not in known]
        if not fields:
            return False, (
                f"隔离行 {row.get('隔离编号')} 未选定失败字段，无法判定可处理范围"
            )
        if unknown:
            return False, (
                f"隔离行 {row.get('隔离编号')} 的失败证据里没有字段"
                f"「{'、'.join(unknown)}」，可处理范围为 {known}"
            )
        return True, ""

    def claim_row(self, quarantine_no: str, reviewer: str, fields: Any,
                  claim_time: str) -> tuple[bool, str, dict[str, Any] | None]:
        """领办隔离行：服务端按证据判定可处理字段范围，其他人不得再领同一字段。

        同一原行存在多份失败证据时，甲领走邮编、乙可以领走金额，互不覆盖。
        """
        row = self._find_row(quarantine_no)
        if row is None:
            return False, f"隔离行 {quarantine_no} 不存在", None
        if row.get("status") in ("已回补", "已废弃"):
            return False, (
                f"隔离行 {quarantine_no} 已终态（{row.get('status')}），不能再领办"
            ), dict(row)

        selected = self._norm_fields(fields)
        ok, message = self._evidence_scope(row, selected)
        if not ok:
            return False, message, dict(row)

        claims = self._claims()
        held: dict[str, str] = {}
        for claim in claims:
            if claim.get("隔离编号") != quarantine_no:
                continue
            if claim.get("状态") == "进行中":
                for field in self._norm_fields(claim.get("可处理字段")):
                    held[field] = str(claim.get("领办人"))

        conflict = [f for f in selected if f in held]
        if conflict:
            return False, (
                f"隔离行 {quarantine_no} 的字段「{'、'.join(conflict)}」"
                f"已由 {sorted({held[f] for f in conflict})} 领办，请只领剩余字段"
            ), dict(row)

        record = {
            "id": max((int(c.get("id", 0)) for c in claims), default=0) + 1,
            "隔离编号": quarantine_no,
            "关联批次": row.get("关联批次"),
            "原始行号": str(row.get("原始行号")),
            "领办人": reviewer,
            "可处理字段": selected,
            "证据序号范围": sorted({
                int(e.get("证据序号") or 0)
                for e in self._evidence_of_quarantine(quarantine_no)
                if set(selected) & set(self._norm_fields(e.get("失败字段")))
            }),
            "状态": "进行中",
            "领办时间": claim_time,
        }
        claims.append(record)
        if row.get("status") != "复核中":
            row["status"] = "复核中"
            row["pending"] = True
        row.setdefault("复核人", reviewer)
        remaining = [f for f in self._merged_fields(row) if f not in held and f not in selected]
        return True, (
            f"隔离行 {quarantine_no} 已由 {reviewer} 领办字段"
            f"「{'、'.join(selected)}」；本行其余字段 {remaining or '无'} 仍可被他人领办"
        ), dict(row)

    def active_claims(self, quarantine_no: str) -> list[dict[str, Any]]:
        return [
            dict(c) for c in self._claims()
            if c.get("隔离编号") == quarantine_no and c.get("状态") == "进行中"
        ]

    # ------------------------------------------------------------------
    # 复核结论与回补
    # ------------------------------------------------------------------
    def submit_review(self, quarantine_no: str, reviewer: str) -> tuple[bool, str]:
        """提交复核：已回补/已废弃的隔离行不能再回到复核中。"""
        for row in store.rows(self.MODULE):
            if row.get("隔离编号") != quarantine_no:
                continue
            if row.get("status") in ("已回补", "已废弃"):
                return False, (
                    f"隔离行 {quarantine_no} 已终态（{row.get('status')}），不能回到复核中"
                )
            row["status"] = "复核中"
            row["pending"] = True
            row["复核人"] = reviewer
            return True, f"隔离行 {quarantine_no} 已进入复核，复核人 {reviewer}"
        return False, f"隔离行 {quarantine_no} 不存在"

    def _unresolved_fields(self, row: dict[str, Any],
                           resolved: list[str]) -> list[str]:
        """本行仍没有修订值的失败字段：这些字段还没核完，行不能离开隔离区。"""
        merged = self._merged_fields(row)
        covered: set[str] = set()
        for field in resolved:
            if field in merged:
                covered.add(field)
        raw = row.get("修订值")
        if isinstance(raw, dict):
            covered |= {k for k in raw if k in merged}
        return [f for f in merged if f not in covered]

    def apply_revision(self, quarantine_no: str, reviewer: str,
                       corrected_values: dict[str, Any],
                       review_time: str) -> tuple[bool, str, dict[str, Any] | None]:
        """登记修订值并回补批次。

        落库顺序：先写修订值与复核证据快照，再写字段解析明细，最后回写批次统计。
        同一批次同一原行的有效回补只产生一条成功行；重复调用不再增加成功行。
        """
        row = self._find_row(quarantine_no)
        if row is None:
            return False, f"隔离行 {quarantine_no} 不存在", None
        if row.get("status") == "已废弃":
            return False, f"隔离行 {quarantine_no} 已废弃，不能回补", None

        values = {str(k): v for k, v in (corrected_values or {}).items() if str(k).strip()}
        if not values:
            return False, f"隔离行 {quarantine_no} 未提交修订值，无法回补", dict(row)

        merged = self._merged_fields(row)
        unknown = [f for f in values if f not in merged]
        if unknown:
            return False, (
                f"隔离行 {quarantine_no} 的失败证据里没有字段"
                f"「{'、'.join(unknown)}」，可处理范围为 {merged}"
            ), dict(row)

        reason = str(row.get("失败原因") or "")
        if not any(k in reason for k in self.REINSTATABLE_REASONS):
            return False, (
                f"隔离行 {quarantine_no} 失败原因「{reason}」不属于可人工修正范围，"
                f"只能废弃不能回补"
            ), dict(row)

        existing = row.get("修订值") if isinstance(row.get("修订值"), dict) else {}
        merged_values = {**existing, **values}
        row["修订值"] = merged_values
        unresolved = self._unresolved_fields(row, list(merged_values))
        row["未解决字段"] = unresolved

        evidence = self._evidence_of_quarantine(quarantine_no)
        original_text = row.get("原始行文本")
        row["复核证据快照"] = {
            "原始行文本": original_text,
            "失败字段": merged,
            "证据": [
                {
                    "证据序号": e.get("证据序号"),
                    "失败字段": self._norm_fields(e.get("失败字段")),
                    "失败原因": e.get("失败原因"),
                    "原始行文本": e.get("原始行文本"),
                    "证据来源": e.get("证据来源"),
                    "登记时间": e.get("登记时间"),
                }
                for e in sorted(evidence, key=lambda e: int(e.get("证据序号") or 0))
            ],
        }
        row["复核结论"] = f"{reviewer}: 修订 {merged_values}"
        row["复核时间"] = review_time
        row["复核人"] = reviewer

        already_reinstated = row.get("status") == "已回补"
        if unresolved:
            # 还有失败字段没核完：留在隔离区，不占用批次的成功行。
            row["status"] = "复核中"
            row["pending"] = True
            row["abnormal"] = False
            self._close_claims(quarantine_no, list(values), "已修订")
            if already_reinstated:
                # 已回补后补证又回到复核：退回此前计入的那条成功行。
                self._settle_batch_counts(row)
            return True, (
                f"隔离行 {quarantine_no} 修订值已登记，但字段"
                f"「{'、'.join(unresolved)}」尚未核完，仍留在隔离区"
            ), dict(row)

        # 字段全部核完：写回解析批次。
        self._write_back_parsed_fields(row, merged_values, reviewer, review_time)
        row["status"] = "已回补"
        row["pending"] = False
        row["abnormal"] = False
        self._close_claims(quarantine_no, list(values), "已回补")
        self._settle_batch_counts(row)
        if already_reinstated:
            return True, (
                f"隔离行 {quarantine_no} 已回补，本次修订值已更新，"
                f"批次成功行数不重复增加"
            ), dict(row)
        return True, (
            f"隔离行 {quarantine_no} 已回补进批次 {row.get('关联批次')}，"
            f"批次成功行数按实际行数重算"
        ), dict(row)

    def _close_claims(self, quarantine_no: str, fields: list[str], state: str) -> None:
        for claim in self._claims():
            if claim.get("隔离编号") != quarantine_no or claim.get("状态") != "进行中":
                continue
            remaining = [
                f for f in self._norm_fields(claim.get("可处理字段"))
                if f not in fields
            ]
            if remaining:
                claim["可处理字段"] = remaining
            else:
                claim["状态"] = state

    def _write_back_parsed_fields(self, row: dict[str, Any], values: dict[str, Any],
                                  reviewer: str, review_time: str) -> None:
        """把确认过的修订值写成字段解析行的成功取值，供批次统计与契约取数。"""
        from app.services.field_parse import FieldParseService

        parse_svc = store_service("field_parse")
        assert isinstance(parse_svc, FieldParseService)
        parse_svc.write_back_revision(
            intake_batch=str(row.get("关联批次")),
            row_num=str(row.get("原始行号")),
            values=values,
            reviewer=reviewer,
            review_time=review_time,
        )

    def _settle_batch_counts(self, row: dict[str, Any]) -> None:
        """结果回写批次统计：有效回补让成功行 +1、隔离行 -1，且只计一次。

        同一批次同一原行重复回补不会再次增加成功行；原行因新增证据重回隔离时，
        已计入的成功行要退回，保证批次统计与实际行数一致。
        """
        batch = next(
            (b for b in store.rows("parse_batch")
             if b.get("关联投递批次") == row.get("关联批次")),
            None)
        if batch is None:
            return

        success = self._int_text(batch.get("成功行数"))
        quarantined = self._int_text(batch.get("隔离行数"))
        counted = bool(row.get("已计入成功"))
        reinstated = row.get("status") == "已回补"

        if reinstated and not counted:
            success += 1
            quarantined = max(0, quarantined - 1)
            row["已计入成功"] = True
        elif not reinstated and counted:
            success = max(0, success - 1)
            quarantined += 1
            row["已计入成功"] = False

        batch["成功行数"] = str(success)
        batch["隔离行数"] = str(quarantined)
        if quarantined == 0:
            batch["批次状态"] = "已闭合"
            batch["status"] = "已闭合"
            batch["pending"] = False
            batch["abnormal"] = False
        else:
            batch["批次状态"] = "存在隔离"
            batch["status"] = "存在隔离"
            batch["pending"] = True
            batch["abnormal"] = True

    @staticmethod
    def _int_text(value: Any) -> int:
        text = str(value or "").strip()
        return int(text) if text.isdigit() else 0

    def reinstate_to_batch(self, quarantine_no: str, reviewer: str,
                           conclusion: str, review_time: str,
                           corrected_values: dict[str, Any] | None = None
                           ) -> tuple[bool, str, dict[str, Any] | None]:
        """回补批次：校验失败原因是否可人工修正，回补后同步批次成功/隔离行数。

        既支持带修订值的复核回补，也兼容只给结论的旧调用（结论视为证据字段的修订摘要）。
        """
        row = self._find_row(quarantine_no)
        if row is None:
            return False, f"隔离行 {quarantine_no} 不存在", None
        if row.get("status") == "已废弃":
            return False, f"隔离行 {quarantine_no} 已废弃，不能回补", None
        if row.get("status") == "已回补" and not corrected_values:
            # 没有新的修订值就再次回补属于重复提交：成功行只算一次，直接拒绝。
            return False, f"隔离行 {quarantine_no} 已回补，不能重复回补", None

        values = dict(corrected_values or {})
        if not values:
            # 旧调用只给结论：把结论当作全部失败字段的修订说明，保证回补仍是一条成功行。
            values = {f: conclusion for f in self._merged_fields(row)}
        ok, message, updated = self.apply_revision(
            quarantine_no, reviewer, values, review_time)
        return ok, message, updated

    def discard_row(self, quarantine_no: str, reviewer: str, reason: str) -> tuple[bool, str]:
        """废弃隔离行：终态，不可再回补。"""
        for row in store.rows(self.MODULE):
            if row.get("隔离编号") != quarantine_no:
                continue
            if row.get("status") == "已废弃":
                return False, f"隔离行 {quarantine_no} 已废弃"
            row["status"] = "已废弃"
            row["pending"] = False
            row["abnormal"] = True
            row["复核结论"] = f"{reviewer}: 废弃，{reason}"
            for claim in self._claims():
                if claim.get("隔离编号") == quarantine_no:
                    claim["状态"] = "已废弃"
            return True, f"隔离行 {quarantine_no} 已废弃：{reason}"
        return False, f"隔离行 {quarantine_no} 不存在"

    # ------------------------------------------------------------------
    # 变更回执 / 契约阻断
    # ------------------------------------------------------------------
    def change_receipt(self, quarantine_no: str) -> dict[str, Any] | None:
        """变更回执：保留已填修订值，指明本申请还能提交什么、哪些要重新领取。

        别人已领办或已终态的字段不能再由本申请提交，但本申请已填的修订值不回滚。
        """
        row = self._find_row(quarantine_no)
        if row is None:
            return None
        merged = self._merged_fields(row)
        filled: dict[str, Any] = (
            dict(row.get("修订值")) if isinstance(row.get("修订值"), dict) else {}
        )
        held: dict[str, list[str]] = {}
        for claim in self._claims():
            if claim.get("隔离编号") != quarantine_no or claim.get("状态") != "进行中":
                continue
            for field in self._norm_fields(claim.get("可处理字段")):
                held.setdefault(field, []).append(str(claim.get("领办人")))

        submit_now = [f for f in merged if f not in held and f not in filled]
        pending_reasons = []
        for field in merged:
            if field in filled:
                continue
            if field in held:
                pending_reasons.append(
                    f"{field} 已由 {'、'.join(held[field])} 重新领办，需重新领取后再提交")
            else:
                pending_reasons.append(f"{field} 尚未登记修订值，可继续提交")
        return {
            "隔离编号": quarantine_no,
            "status": row.get("status"),
            "已填修订值": filled,
            "可提交字段": submit_now,
            "需重新领取": pending_reasons,
            "证据快照": row.get("复核证据快照") or {
                "原始行文本": row.get("原始行文本"),
                "失败字段": merged,
            },
            "已回补": row.get("status") == "已回补",
        }

    def row_evidence_trail(self, intake_batch: str, row_num: Any) -> dict[str, Any]:
        """原行回查：历史原文与后来补入的失败证据都能按时间顺序重放。"""
        evidence = sorted(
            self._evidence_of_row(intake_batch, row_num),
            key=lambda e: int(e.get("证据序号") or 0))
        row = next(
            (r for r in store.rows(self.MODULE)
             if str(r.get("关联批次")) == str(intake_batch)
             and str(r.get("原始行号")) == str(row_num)), None)
        return {
            "关联批次": intake_batch,
            "原始行号": str(row_num),
            "隔离编号": row.get("隔离编号") if row else "",
            "status": row.get("status") if row else "",
            "失败字段": self._merged_fields(row) if row else [],
            "证据": [
                {
                    "证据序号": e.get("证据序号"),
                    "失败字段": self._norm_fields(e.get("失败字段")),
                    "失败原因": e.get("失败原因"),
                    "原始行文本": e.get("原始行文本"),
                    "证据来源": e.get("证据来源"),
                    "登记时间": e.get("登记时间"),
                }
                for e in evidence
            ],
            "修订值": dict(row.get("修订值")) if row and isinstance(row.get("修订值"), dict) else {},
            "复核结论": row.get("复核结论") if row else "",
            "复核证据快照": row.get("复核证据快照") if row else None,
        }

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------
    def quarantine_stats(self, intake_batch: str) -> dict[str, int]:
        """按批次输出隔离真实计数，供解析指标结算隔离率。"""
        rows = [r for r in store.rows(self.MODULE) if str(r.get("关联批次")) == str(intake_batch)]
        return {
            "total": len(rows),
            "isolated": sum(1 for r in rows if r.get("status") == "已隔离"),
            "reviewing": sum(1 for r in rows if r.get("status") == "复核中"),
            "reinstated": sum(1 for r in rows if r.get("status") == "已回补"),
            "discarded": sum(1 for r in rows if r.get("status") == "已废弃"),
        }

    def unreviewed_rows(self, intake_batch: str) -> list[dict[str, Any]]:
        """未复核隔离行清单，输出契约据此阻断批次通过。"""
        return [
            {"隔离编号": r.get("隔离编号"), "原始行号": r.get("原始行号"),
             "失败字段": r.get("失败字段"), "失败原因": r.get("失败原因"),
             "未解决字段": self._unresolved_fields(
                 r, list(r.get("修订值") or {}) if isinstance(r.get("修订值"), dict) else [])}
            for r in store.rows(self.MODULE)
            if str(r.get("关联批次")) == str(intake_batch)
            and r.get("status") in ("已隔离", "复核中")
        ]

    def blocks_contract(self, intake_batch: str) -> tuple[bool, str]:
        """存在未复核隔离行时批次不得进入输出契约。"""
        pending = self.unreviewed_rows(intake_batch)
        if pending:
            return True, (
                f"批次 {intake_batch} 有 {len(pending)} 行隔离未复核"
                f"（行号 {[p['原始行号'] for p in pending][:5]}），输出契约不得通过"
            )
        return False, f"批次 {intake_batch} 无未复核隔离行"


def _row_sort_key(row_num: Any) -> tuple[int, int | str]:
    """原行排序：数字行号按数值排，非数字行号排在数字之后并按文本排。"""
    text = str(row_num or "").strip()
    if text.isdigit():
        return (0, int(text))
    return (1, text)


def store_service(module: str):
    """延迟导入服务注册表，避免与各模块服务形成导入环。"""
    from app.services import service_for

    return service_for(module)
