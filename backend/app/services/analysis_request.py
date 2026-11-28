"""分析申请领域服务：批准分析前需校验样品登记模块中对应样品是否已入库。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class AnalysisRequestService(BaseModuleService):
    MODULE = "analysis_request"
    ENTITY = "分析申请单"
    OPERATION_TERM = "提交申请"
    LIST_FIELDS = ["申请样品", "分析项目", "分析方法", "预期周期", "申请人", "审批人", "申请日期", "备注说明"]
    REQUIRED_FIELDS = ["申请样品", "分析项目", "分析方法"]
    STATUS_ORDER = ["草稿", "已提交", "已批准", "已驳回"]
    ACTION_RULES = {"提交申请": "已提交", "批准分析": "已批准", "驳回申请": "已驳回"}
    NEGATIVE_ACTIONS = ["驳回申请"]

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

        # 批准分析前校验样品是否已入库
        if action == "批准分析":
            sample_name = entry.get("申请样品", "")
            registry_rows = store.find_by("sample_registry", "样品编号", sample_name)
            if registry_rows:
                sample = registry_rows[0]
                if sample.get("status") not in ("已入库", "已移交"):
                    return None, f"样品「{sample_name}」尚未入库（当前状态：{sample.get('status', '未知')}），不能批准分析申请"
            # 如果样品不在登记簿中，也允许批准（可能是其他来源的样品）

        old_status = entry.get("status")
        entry["status"] = target
        entry["pending"] = target in ("草稿", "已提交")
        entry["abnormal"] = action in self.NEGATIVE_ACTIONS

        store.mark_idempotent(idempotent_key)

        return entry, f"{self.ENTITY}已{action}"