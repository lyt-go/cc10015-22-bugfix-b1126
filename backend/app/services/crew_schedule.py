"""队员排班领域服务：排班调整影响站位布设和潜水作业的人员可用性。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class CrewScheduleService(BaseModuleService):
    MODULE = "crew_schedule"
    ENTITY = "排班表"
    OPERATION_TERM = "发布排班"
    LIST_FIELDS = ["队员姓名", "岗位职责", "作业时段", "所属班组", "休息安排", "排班人", "开始日期", "备注说明"]
    REQUIRED_FIELDS = ["队员姓名", "岗位职责", "作业时段"]
    STATUS_ORDER = ["待排班", "已发布", "已确认", "已调整"]
    ACTION_RULES = {"发布排班": "已发布", "确认排班": "已确认", "调整排班": "已调整"}
    NEGATIVE_ACTIONS = []

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
        entry["pending"] = target in ("待排班", "已发布")
        entry["abnormal"] = action in self.NEGATIVE_ACTIONS
        entry["adjustment_time"] = str(Any)

        store.mark_idempotent(idempotent_key)

        # 排班调整后影响站位布设和潜水作业的可执行性
        if action == "调整排班":
            member_name = entry.get("队员姓名", "")
            self._mark_affected_operations(member_name)

        return entry, f"{self.ENTITY}已{action}"

    def _mark_affected_operations(self, member_name: str) -> None:
        """排班调整后标记受影响的操作。"""
        # 标记站位布设中该人员的相关记录
        station_rows = store.find_by("station_deploy", "布设人", member_name)
        for row in station_rows:
            row["schedule_changed"] = True
            row["schedule_note"] = f"排班已调整，请确认{member_name}可用"

        # 标记潜水作业中该人员的相关记录
        diving_rows = store.find_by("diving_ops", "潜水员", member_name)
        for row in diving_rows:
            row["schedule_changed"] = True
            row["schedule_note"] = f"排班已调整，请确认{member_name}可用"

    def is_member_available(self, member_name: str) -> bool:
        """检查队员是否可用（未在确认排班中则为可用，已调整但待确认则不可用）。"""
        member_rows = store.find_by(self.MODULE, "队员姓名", member_name)
        if not member_rows:
            return True  # 不在排班表中默认可用
        return all(r.get("status") != "已调整" for r in member_rows)

    def mark_unavailable(self, member_name: str) -> None:
        """标记队员不可用（潜水中止等场景）。"""
        member_rows = store.find_by(self.MODULE, "队员姓名", member_name)
        for row in member_rows:
            row["unavailable"] = True
            row["unavailable_reason"] = "潜水作业中止"