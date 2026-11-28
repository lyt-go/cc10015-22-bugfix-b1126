"""成果汇编领域服务：汇总航次全部成果，接受数据传输完成后的数据完整度更新联动。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class ResultsCompileService(BaseModuleService):
    MODULE = "results_compile"
    ENTITY = "汇编报告"
    OPERATION_TERM = "提交汇编"
    LIST_FIELDS = ["航次编号", "成果类别", "主要发现", "参与人员", "数据完整度", "汇编人", "完成日期", "备注说明"]
    REQUIRED_FIELDS = ["航次编号", "成果类别", "主要发现"]
    STATUS_ORDER = ["汇编中", "已提交", "已审核", "需补充"]
    ACTION_RULES = {"提交汇编": "已提交", "审核通过": "已审核", "退回补充": "需补充"}
    NEGATIVE_ACTIONS = ["退回补充"]

    def run_action(self, entry_id: int, action: str, context: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"

        target = self.ACTION_RULES.get(action)
        if target is None:
            return None, f"动作「{action}」不属于{self.MODULE}可执行范围"

        # 幂等检查
        idempotent_key = f"{self.MODULE}:{entry_id}:{action}"
        if store.is_idempotent(idempotent_key):
            return None, f"动作「{action}」已执行，请勿重复操作"

        old_status = entry.get("status")
        entry["status"] = target
        entry["pending"] = target in ("汇编中", "需补充")
        entry["abnormal"] = action in self.NEGATIVE_ACTIONS

        store.mark_idempotent(idempotent_key)

        return entry, f"{self.ENTITY}已{action}"

    def update_completeness(self, entry_id: int, data_source: str, data_desc: str) -> tuple[dict[str, Any] | None, str]:
        """由数据传输模块联动更新数据完整度。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在"
        current = entry.get("数据完整度", "")
        entry["数据完整度"] = f"{current} | [{data_source}] {data_desc}"
        entry["pending"] = True
        return entry, "数据完整度已更新"

    def get_completeness_summary(self) -> dict[str, Any]:
        """获取整体数据完整度摘要。"""
        rows = store.rows(self.MODULE)
        total = len(rows)
        complete = sum(1 for r in rows if r.get("transfer_complete"))
        return {
            "total_reports": total,
            "complete_transfers": complete,
            "completeness_pct": round(complete / total * 100, 1) if total > 0 else 0,
        }