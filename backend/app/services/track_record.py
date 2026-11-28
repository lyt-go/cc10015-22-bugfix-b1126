"""航迹记录领域服务：管理船舶航迹记录，支持与站位布设位置交叉校验。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class TrackRecordService(BaseModuleService):
    MODULE = "track_record"
    ENTITY = "航迹点"
    OPERATION_TERM = "记录航迹"
    LIST_FIELDS = ["记录时间", "经纬度", "航速", "航向", "水深", "记录人", "记录设备", "备注说明"]
    REQUIRED_FIELDS = ["记录时间", "经纬度", "航速"]
    STATUS_ORDER = ["已记录", "已验证", "已发布", "已废弃"]
    ACTION_RULES = {"记录航迹": "已记录", "验证航迹": "已验证", "废弃航迹": "已废弃"}
    NEGATIVE_ACTIONS = ["废弃航迹"]

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
        entry["pending"] = target in ("已记录",)
        entry["abnormal"] = action in self.NEGATIVE_ACTIONS

        store.mark_idempotent(idempotent_key)

        return entry, f"{self.ENTITY}已{action}"

    def get_track_by_time_range(self, start: str, end: str) -> list[dict[str, Any]]:
        """按时间范围获取航迹记录（用于成果汇编数据溯源）。"""
        rows = store.rows(self.MODULE)
        return [
            r for r in rows
            if start <= str(r.get("记录时间", "")) <= end
        ]