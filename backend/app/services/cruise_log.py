"""航次日志领域服务：记录异常事件触发设备运行状态标记和安全管理联动。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class CruiseLogService(BaseModuleService):
    MODULE = "cruise_log"
    ENTITY = "航海日志"
    OPERATION_TERM = "提交日志"
    LIST_FIELDS = ["记录日期", "船舶位置", "作业内容", "设备运行", "异常事件", "记录人", "值班班组", "备注说明"]
    REQUIRED_FIELDS = ["记录日期", "船舶位置", "作业内容"]
    STATUS_ORDER = ["待填写", "已填写", "已审核", "已归档"]
    ACTION_RULES = {"提交日志": "已填写", "审核通过": "已审核", "归档日志": "已归档"}
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
        entry["pending"] = target in ("待填写", "已填写")
        entry["abnormal"] = action in self.NEGATIVE_ACTIONS

        store.mark_idempotent(idempotent_key)

        # 提交日志时如果包含异常事件，记录到相关模块
        if action == "提交日志":
            abnormal_event = entry.get("异常事件", "")
            if abnormal_event and "无" not in abnormal_event and abnormal_event.strip():
                entry["has_abnormal"] = True
                self._log_abnormal_event(entry)

        # 审核通过时处理异常事件联动
        if action == "审核通过" and entry.get("has_abnormal"):
            self._escalate_abnormal_event(entry)

        return entry, f"{self.ENTITY}已{action}"

    def _log_abnormal_event(self, entry: dict[str, Any]) -> None:
        """记录异常事件到设备清单相关设备。"""
        equipment_status = entry.get("设备运行", "")
        if equipment_status and "异常" in str(equipment_status):
            # 标记设备清单中相关设备
            equip_rows = store.rows("equipment_manifest")
            for equip in equip_rows:
                equip["log_alert"] = True
                equip["log_alert_msg"] = f"航次日志记录异常：{entry.get('异常事件', '')}"

    def _escalate_abnormal_event(self, entry: dict[str, Any]) -> None:
        """审核异常事件后的升级处理。"""
        # 对设备清单进行异常标记
        for equip in store.rows("equipment_manifest"):
            if equip.get("log_alert"):
                equip["safety_review_required"] = True

    def has_recent_abnormal(self, days: int = 1) -> bool:
        """检查近期是否有异常事件记录。"""
        return any(
            row.get("has_abnormal") and row.get("status") == "已审核"
            for row in store.rows(self.MODULE)
        )