"""数据质控领域服务：管理质控流程，接受水文测量模块触发的质控任务。
退回重测时联动水文测量标记异常。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class DataQcService(BaseModuleService):
    MODULE = "data_qc"
    ENTITY = "质控记录"
    OPERATION_TERM = "开始质控"
    LIST_FIELDS = ["数据来源", "质控标准", "异常值个数", "质控结论", "修正措施", "质控人", "质控日期", "备注说明"]
    REQUIRED_FIELDS = ["数据来源", "质控标准", "异常值个数"]
    STATUS_ORDER = ["待质控", "质控中", "已签发", "需重测"]
    ACTION_RULES = {"开始质控": "质控中", "签发报告": "已签发", "退回重测": "需重测"}
    NEGATIVE_ACTIONS = ["退回重测"]

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
        entry["pending"] = target in ("待质控", "质控中")
        entry["abnormal"] = action in self.NEGATIVE_ACTIONS

        store.mark_idempotent(idempotent_key)

        # 退回重测时联动源数据模块
        if action == "退回重测" and entry.get("triggered_by") == "hydro_measure":
            source_id = entry.get("source_entry_id")
            if source_id:
                hydro_entry = store.find("hydro_measure", int(source_id))
                if hydro_entry and hydro_entry.get("status") != "数据异常":
                    hydro_entry["status"] = "数据异常"
                    hydro_entry["pending"] = False
                    hydro_entry["abnormal"] = True
                    hydro_entry["qc_result"] = "需重测"

        return entry, f"{self.ENTITY}已{action}"