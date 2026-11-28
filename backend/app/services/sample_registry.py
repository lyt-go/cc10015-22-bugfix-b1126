"""样品登记领域服务：样品全生命周期管理，接受采样、拖网、沉积物等模块自动登记。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class SampleRegistryService(BaseModuleService):
    MODULE = "sample_registry"
    ENTITY = "样品登记簿"
    OPERATION_TERM = "确认登记"
    LIST_FIELDS = ["样品编号", "样品类型", "采集站位", "保存方式", "存放位置", "登记人", "登记日期", "备注说明"]
    REQUIRED_FIELDS = ["样品编号", "样品类型", "采集站位"]
    STATUS_ORDER = ["待登记", "已登记", "已入库", "已移交"]
    ACTION_RULES = {"确认登记": "已登记", "执行入库": "已入库", "移交样品": "已移交"}
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
        entry["pending"] = target in ("待登记", "已登记")
        entry["abnormal"] = action in self.NEGATIVE_ACTIONS

        store.mark_idempotent(idempotent_key)

        return entry, f"{self.ENTITY}已{action}"

    def is_sample_checked_in(self, sample_no: str) -> bool:
        """检查样品是否已入库。"""
        existing = store.find_one_by(self.MODULE, "样品编号", sample_no)
        if existing and existing.get("status") == "已入库":
            return True
        return False

    def get_samples_by_station(self, station: str) -> list[dict[str, Any]]:
        """获取某站位下的所有样品。"""
        return store.find_by(self.MODULE, "采集站位", station)