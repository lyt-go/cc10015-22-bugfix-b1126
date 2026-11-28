"""仪器校准领域服务：校准不合格时自动停用设备清单中对应用设备。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class InstrumentCalibService(BaseModuleService):
    MODULE = "instrument_calib"
    ENTITY = "校准记录"
    OPERATION_TERM = "执行校准"
    LIST_FIELDS = ["仪器名称", "校准项目", "标准参数", "实测参数", "偏差值", "校准人", "校准日期", "备注说明"]
    REQUIRED_FIELDS = ["仪器名称", "校准项目", "标准参数"]
    STATUS_ORDER = ["待校准", "已校准", "已签署", "不合格"]
    ACTION_RULES = {"执行校准": "已校准", "签署报告": "已签署", "停用仪器": "不合格"}
    NEGATIVE_ACTIONS = ["停用仪器"]

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
        entry["pending"] = target in ("待校准", "已校准")
        entry["abnormal"] = action in self.NEGATIVE_ACTIONS

        store.mark_idempotent(idempotent_key)

        # 停用仪器（不合格）时自动联动设备清单停用对应设备
        if action == "停用仪器":
            instrument_name = entry.get("仪器名称", "")
            self._disable_equipment(instrument_name)

        return entry, f"{self.ENTITY}已{action}"

    def _disable_equipment(self, instrument_name: str) -> None:
        """仪器校准不合格时自动在设备清单中停用对应设备。"""
        equip_rows = store.rows("equipment_manifest")
        for equip in equip_rows:
            if str(equip.get("设备名称", "")).casefold() == instrument_name.casefold():
                if equip.get("status") != "已归还":
                    equip["status"] = "已归还"
                    equip["pending"] = False
                    equip["abnormal"] = True
                    equip["calib_failed"] = True
                    equip["calib_reason"] = f"校准不合格，仪器「{instrument_name}」已自动停用"

    def is_instrument_calibrated(self, instrument_name: str) -> bool:
        """检查仪器是否已校准且合格。"""
        rows = store.find_by(self.MODULE, "仪器名称", instrument_name)
        return any(
            r.get("status") in ("已校准", "已签署") and not r.get("abnormal")
            for r in rows
        )