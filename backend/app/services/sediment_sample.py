"""沉积物采样领域服务：分割样品后自动更新样品登记模块的存储信息。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class SedimentSampleService(BaseModuleService):
    MODULE = "sediment_sample"
    ENTITY = "沉积物样品"
    OPERATION_TERM = "执行采样"
    LIST_FIELDS = ["采样站位", "采样深度", "柱状长度", "岩性描述", "样品编号", "采样人", "采样时间", "备注说明"]
    REQUIRED_FIELDS = ["采样站位", "采样深度", "柱状长度"]
    STATUS_ORDER = ["待采样", "已采样", "已登记", "已分割"]
    ACTION_RULES = {"执行采样": "已采样", "登记样品": "已登记", "分割样品": "已分割"}
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
        entry["pending"] = target in ("待采样", "已采样")
        entry["abnormal"] = action in self.NEGATIVE_ACTIONS

        store.mark_idempotent(idempotent_key)

        # 分割样品后更新样品登记中对应样品的存储位置和保存方式
        if action == "分割样品":
            self._update_registry_on_split(entry)

        return entry, f"{self.ENTITY}已{action}"

    def _update_registry_on_split(self, entry: dict[str, Any]) -> None:
        """分割后更新样品登记的存放信息。"""
        sample_no = entry.get("样品编号", "")
        existing = store.find_one_by("sample_registry", "样品编号", sample_no)
        if existing:
            existing["保存方式"] = "分层保存（已分割）"
            existing["存放位置"] = f"沉积物库-{entry.get('采样站位', '')}"
            existing["pending"] = True
            existing["备注说明"] = f"{existing.get('备注说明', '')} | 已分割为{entry.get('柱状长度', '')}段"