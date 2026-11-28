"""潜水作业领域服务：中止潜水后影响队员排班中对应人员可用性。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class DivingOpsService(BaseModuleService):
    MODULE = "diving_ops"
    ENTITY = "潜水记录"
    OPERATION_TERM = "确认下水"
    LIST_FIELDS = ["潜水地点", "潜水深度", "潜水时长", "作业内容", "潜水员", "安全员", "下水时间", "备注说明"]
    REQUIRED_FIELDS = ["潜水地点", "潜水深度", "潜水时长"]
    STATUS_ORDER = ["待准备", "已下水", "已完成", "已中止"]
    ACTION_RULES = {"确认下水": "已下水", "完成作业": "已完成", "中止潜水": "已中止"}
    NEGATIVE_ACTIONS = ["中止潜水"]

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

        # 中止潜水前只能是已下水或已完成状态
        if action == "中止潜水" and entry.get("status") not in ("已下水", "已完成"):
            return None, "只能中止已下水或已完成的潜水作业"

        old_status = entry.get("status")
        entry["status"] = target
        entry["pending"] = target in ("待准备", "已下水")
        entry["abnormal"] = action in self.NEGATIVE_ACTIONS

        store.mark_idempotent(idempotent_key)

        # 中止潜水后联动队员排班：标记潜水员和安全员不可用
        if action == "中止潜水":
            diver_name = entry.get("潜水员", "")
            safety_name = entry.get("安全员", "")
            self._update_crew_availability(diver_name, safety_name)

        return entry, f"{self.ENTITY}已{action}"

    def _update_crew_availability(self, diver_name: str, safety_name: str) -> None:
        """潜水中止后更新队员排班的人员可用性。"""
        crew_rows = store.rows("crew_schedule")
        for row in crew_rows:
            member = row.get("队员姓名", "")
            if member in (diver_name, safety_name):
                row["unavailable"] = True
                row["unavailable_reason"] = f"潜水作业中止，{member}需重新评估状态"
                row["diving_aborted"] = True

    def is_diver_available(self, diver_name: str) -> bool:
        """检查潜水员是否可用。"""
        crew_rows = store.find_by("crew_schedule", "队员姓名", diver_name)
        if not crew_rows:
            return True
        return all(not r.get("unavailable", False) for r in crew_rows)