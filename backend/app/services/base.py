"""模块服务基类：共享查询、登记、动作和统计骨架，支持跨模块联动。"""
from __future__ import annotations

from typing import Any

from app.store import store


class BaseModuleService:
    MODULE = ""
    ENTITY = ""
    LIST_FIELDS: list[str] = []
    REQUIRED_FIELDS: list[str] = []
    STATUS_ORDER: list[str] = []
    ACTION_RULES: dict[str, str] = {}
    NEGATIVE_ACTIONS: list[str] = []

    # 跨模块前置条件：{action: {module: (status_required, error_msg)}}
    CROSS_PRECHECKS: dict[str, dict[str, tuple[str, str]]] = {}

    # 跨模块后置联动：{action: callable(self, entry_id, entry)}
    CROSS_SIDE_EFFECTS: dict[str, list] = {}

    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = list(store.rows(self.MODULE))
        for field, expected in (filters or {}).items():
            if field in self.LIST_FIELDS:
                rows = [
                    row for row in rows
                    if expected.casefold() in str(row.get(field, "")).casefold()
                ]
        if keyword and self.LIST_FIELDS:
            field = self.LIST_FIELDS[0]
            rows = [row for row in rows if keyword.casefold() in str(row.get(field, "")).casefold()]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(self.MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [
            field for field in self.REQUIRED_FIELDS
            if not str(values.get(field) or "").strip()
        ]
        if missing:
            return None, missing
        rows = store.rows(self.MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in self.LIST_FIELDS})
        entry["status"] = self.STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str, context: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str]:
        """执行动作，包含幂等检查和跨模块前置/后置处理。"""
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

        # 执行状态变更
        entry["status"] = target
        entry["pending"] = target != self.STATUS_ORDER[-1] and target not in self.NEGATIVE_FOR_STATUS
        entry["abnormal"] = action in self.NEGATIVE_ACTIONS

        # 标记幂等
        store.mark_idempotent(idempotent_key)

        # 跨模块后置联动
        self._run_side_effects(action, entry_id, entry)

        return entry, f"{self.ENTITY}已{action}"

    @property
    def NEGATIVE_FOR_STATUS(self) -> list[str]:
        """被标记为负向状态的状态列表，这些状态的pending应为False。"""
        return []

    def _run_prechecks(self, action: str, entry: dict[str, Any]) -> str | None:
        """执行跨模块前置条件检查。"""
        prechecks = self.CROSS_PRECHECKS.get(action, {})
        for module, (required_status, error_msg) in prechecks.items():
            snapshot = self._get_module_snapshot(module)
            if snapshot is None or snapshot.get("status") != required_status:
                return error_msg
        return None

    def _run_side_effects(self, action: str, entry_id: int, entry: dict[str, Any]) -> None:
        """执行跨模块后置联动。"""
        effects = self.CROSS_SIDE_EFFECTS.get(action, [])
        for effect_func in effects:
            try:
                effect_func(self, entry_id, entry)
            except Exception:
                # 后置联动失败不影响主操作
                pass

    def _get_module_snapshot(self, module: str, record_id: int | None = None) -> dict[str, Any] | None:
        """获取目标模块的快照数据。"""
        if record_id is not None:
            return store.find(module, record_id)
        # 返回第一个记录作为模块参考状态
        rows = store.rows(module)
        return rows[0] if rows else None

    def domain_snapshot(self) -> dict[str, Any]:
        """返回本模块的状态统计与业务对象，供其他模块读取。"""
        rows = store.rows(self.MODULE)
        by_status = {
            status: sum(1 for row in rows if row.get("status") == status)
            for status in self.STATUS_ORDER
        }
        return {
            "module": self.MODULE,
            "entity": self.ENTITY,
            "artifact": f"{self.ENTITY}状态记录",
            "count": len(rows),
            "by_status": by_status,
            "abnormal": sum(1 for row in rows if row.get("abnormal")),
            "rows": [dict(r) for r in rows],
        }