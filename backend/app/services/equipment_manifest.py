"""设备清单领域服务：管理科考设备全生命周期，接受仪器校准模块的停用联动。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class EquipmentManifestService(BaseModuleService):
    MODULE = "equipment_manifest"
    ENTITY = "设备记录"
    OPERATION_TERM = "校验设备"
    LIST_FIELDS = ["设备名称", "设备类型", "出厂编号", "校准日期", "当前状态", "保管人", "所在位置", "备注说明"]
    REQUIRED_FIELDS = ["设备名称", "设备类型", "出厂编号"]
    STATUS_ORDER = ["待校验", "已校验", "已启用", "已归还"]
    ACTION_RULES = {"校验设备": "已校验", "启用设备": "已启用", "归还设备": "已归还"}
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

        # 启用设备前需确认已校验
        if action == "启用设备" and entry.get("status") != "已校验":
            return None, "只能启用已校验的设备"

        entry["status"] = target
        entry["pending"] = target in ("待校验", "已校验")
        entry["abnormal"] = action in self.NEGATIVE_ACTIONS

        store.mark_idempotent(idempotent_key)

        return entry, f"{self.ENTITY}已{action}"

    def find_by_name(self, name: str) -> dict[str, Any] | None:
        """按设备名称查找。"""
        return store.find_one_by(self.MODULE, "设备名称", name)

    def disable_equipment(self, equipment_name: str) -> tuple[dict[str, Any] | None, str]:
        """仪器校准不合格时自动停用设备。"""
        equipment = self.find_by_name(equipment_name)
        if equipment is None:
            return None, f"设备「{equipment_name}」不在清单中"
        if equipment.get("status") == "已归还":
            return equipment, "设备已归还，无需停用"
        equipment["status"] = "已归还"
        equipment["pending"] = False
        equipment["abnormal"] = True
        equipment["disable_reason"] = "仪器校准不合格"
        return equipment, f"设备「{equipment_name}」因校准不合格已自动停用"