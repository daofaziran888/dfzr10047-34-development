"""输出契约领域服务：校验解析结果是否满足列集约定与行数下限，产出契约条目数据集。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class OutputContractService(BaseModuleService):
    MODULE = "output_contract"
    ENTITY = "契约条目"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["契约编号", "关联批次", "输出列集合", "列顺序约定", "必填列", "行数下限", "契约校验结论", "校验时间"]
    REQUIRED_FIELDS = ["契约编号", "关联批次", "输出列集合"]
    STATUS_ORDER = ["待校验", "校验中", "已通过", "未通过"]
    ACTION_RULES = {"启动校验": "校验中", "确认通过": "已通过", "登记未通过": "未通过"}
    NEGATIVE_ACTIONS = ["登记未通过"]

    @staticmethod
    def _split(text: Any) -> list[str]:
        return [p.strip() for p in str(text or "").split(",") if p.strip()]

    def _find(self, contract_no: str) -> dict[str, Any] | None:
        for row in store.rows(self.MODULE):
            if row.get("契约编号") == contract_no:
                return row
        return None

    def validate_batch(self, contract_no: str, actual_columns: list[str],
                       actual_rows: int, check_time: str) -> tuple[bool, list[str], dict[str, Any] | None]:
        """执行契约校验：列集合、列顺序、必填列、行数下限逐项比对。

        校验前必须先确认坏行隔离与抽样复核都没有阻断该批次；
        任一前置阻断成立时契约直接判未通过并记录原因。
        """
        contract = self._find(contract_no)
        if contract is None:
            return False, [f"契约 {contract_no} 不存在"], None

        intake_batch = self._intake_batch_of(contract)
        failures: list[str] = []

        quarantine_blocked, quarantine_msg = self._quarantine_block(intake_batch)
        if quarantine_blocked:
            failures.append(quarantine_msg)

        review_blocked, review_msg = self._review_block(intake_batch)
        if review_blocked:
            failures.append(review_msg)

        expected = self._split(contract.get("输出列集合"))
        missing = [c for c in expected if c not in actual_columns]
        extra = [c for c in actual_columns if c not in expected]
        if missing:
            failures.append(f"缺少约定列 {missing}")
        if extra:
            failures.append(f"多出未约定列 {extra}")

        order = self._split(contract.get("列顺序约定"))
        if order and not missing and not extra:
            if actual_columns != order:
                failures.append(
                    f"列顺序与约定不一致，期望 {order} 实际 {actual_columns}"
                )

        mandatory = self._split(contract.get("必填列"))
        absent = [c for c in mandatory if c not in actual_columns]
        if absent:
            failures.append(f"必填列缺失 {absent}")

        min_rows_text = str(contract.get("行数下限") or "").strip()
        min_rows = int(min_rows_text) if min_rows_text.isdigit() else 0
        if actual_rows < min_rows:
            failures.append(f"实际行数 {actual_rows} 低于下限 {min_rows}")

        passed = not failures
        contract["契约校验结论"] = "通过" if passed else ";".join(failures)
        contract["校验时间"] = check_time
        contract["实际行数"] = str(actual_rows)
        contract["status"] = "已通过" if passed else "未通过"
        contract["pending"] = not passed
        contract["abnormal"] = not passed
        return passed, failures, dict(contract)

    def _intake_batch_of(self, contract: dict[str, Any]) -> str:
        """契约的关联批次可能是批次记录编号，换成投递批次编号统一比对。"""
        linked = str(contract.get("关联批次") or "")
        for batch in store.rows("parse_batch"):
            if batch.get("批次记录编号") == linked:
                return str(batch.get("关联投递批次") or linked)
        return linked

    def actual_rows_of_batch(self, contract_no: str) -> int:
        """回补后的实际行数：按字段解析明细中全成功的原行去重计数。

        同一原行只算一条成功行，回补多少次都不会让实际行数虚增；仍留在隔离区的
        原行即使有部分修订值也不计入，保证契约用真实结果行数复验。
        """
        contract = self._find(contract_no)
        if contract is None:
            return 0
        intake_batch = self._intake_batch_of(contract)
        still_quarantined = {
            str(r.get("原始行号"))
            for r in store.rows("row_quarantine")
            if str(r.get("关联批次")) == intake_batch
            and r.get("status") in ("已隔离", "复核中")
        }
        rows_by_line: dict[str, bool] = {}
        for r in store.rows("field_parse"):
            if str(r.get("关联批次")) != intake_batch:
                continue
            line = str(r.get("解析行号") or "")
            if not line:
                continue
            ok = r.get("status") == "解析成功"
            rows_by_line[line] = rows_by_line.get(line, True) and ok
        return sum(
            1 for line, ok in rows_by_line.items()
            if ok and line not in still_quarantined
        )

    def revalidate_after_reinstate(self, contract_no: str, actual_columns: list[str],
                                   actual_rows: int | None, check_time: str
                                   ) -> tuple[bool, list[str], dict[str, Any] | None]:
        """隔离行回补后重新校验：已通过的契约不得被重复校验改写结论。

        actual_rows 不传时按回补后的实际行数复验，避免沿用回补前的旧行数。
        """
        contract = self._find(contract_no)
        if contract is None:
            return False, [f"契约 {contract_no} 不存在"], None
        if contract.get("status") == "已通过":
            return True, ["契约已通过，保留原校验结论不重复校验"], dict(contract)
        rows = self.actual_rows_of_batch(contract_no) if actual_rows is None else actual_rows
        return self.validate_batch(contract_no, actual_columns, rows, check_time)

    def _quarantine_block(self, intake_batch: str) -> tuple[bool, str]:
        """未复核隔离行阻断契约。

        只有仍留在隔离区（已隔离/复核中）的原行算阻断；已回补、已废弃的行不再拦截。
        同一原行有多份失败证据时，只要还有一份没处理完，这行就继续阻断。
        """
        rows = store.rows("row_quarantine")
        pending = [
            r for r in rows
            if str(r.get("关联批次")) == str(intake_batch)
            and r.get("status") in ("已隔离", "复核中")
        ]
        if pending:
            return True, (
                f"存在 {len(pending)} 行隔离未复核"
                f"（行号 {[p.get('原始行号') for p in pending][:5]}），契约不得通过"
            )
        return False, ""

    def _review_block(self, intake_batch: str) -> tuple[bool, str]:
        """抽样复核驳回阻断契约。"""
        candidates = {intake_batch}
        for batch in store.rows("parse_batch"):
            if batch.get("批次记录编号") == intake_batch:
                candidates.add(str(batch.get("关联投递批次") or ""))
        for sample in store.rows("sample_review"):
            if sample.get("关联批次") in candidates and sample.get("status") == "已驳回":
                return True, (
                    f"抽样复核已驳回（{sample.get('复核结论')}），契约不得通过"
                )
        return False, ""

    def contract_result_for_deliver(self, contract_no: str) -> tuple[bool, str]:
        """结果投递前置：只有已通过的契约才允许创建投递记录。"""
        contract = self._find(contract_no)
        if contract is None:
            return False, f"契约 {contract_no} 不存在，不得投递"
        if contract.get("status") != "已通过":
            return False, (
                f"契约 {contract_no} 状态为 {contract.get('status')}，"
                f"未通过的契约不得进入结果投递"
            )
        return True, f"契约 {contract_no} 已通过，允许投递"

    def pass_ratio(self) -> float:
        """契约通过率：已通过 / 全部契约条目。"""
        rows = store.rows(self.MODULE)
        if not rows:
            return 0.0
        return sum(1 for r in rows if r.get("status") == "已通过") / len(rows)
