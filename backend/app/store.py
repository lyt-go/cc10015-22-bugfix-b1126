"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。
增加幂等检查、并发锁、跨模块状态同步、重试恢复和回滚能力。
"""
from __future__ import annotations

import threading
import time
from typing import Any

from app.seed import SEED_ROWS


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {}
        self._idempotent_keys: set[str] = set()
        self._locks: dict[str, threading.Lock] = {}
        self._global_lock = threading.Lock()
        # 回滚日志：{transaction_id: {module, entry_id, before_snapshot}}
        self._rollback_log: dict[str, dict] = {}
        # 航次生效回执：批准每一轮计划时签发，全局同一时点只有一份“生效中”
        self._approval_receipts: list[dict[str, Any]] = []
        self._receipt_seq: int = 0
        # 重试配置
        self._retry_max: int = 3
        self._retry_delay: float = 0.01
        self._seed()

    def _seed(self) -> None:
        self._tables = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        self._idempotent_keys.clear()
        self._rollback_log.clear()
        self._approval_receipts.clear()
        self._receipt_seq = 0

    def reset(self) -> None:
        """重置所有数据回到初始种子状态。"""
        self._seed()
        self._locks.clear()

    def _get_lock(self, key: str) -> threading.Lock:
        with self._global_lock:
            if key not in self._locks:
                self._locks[key] = threading.Lock()
            return self._locks[key]

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def atomic_update(self, module: str, entry_id: int, updates: dict[str, Any]) -> dict[str, Any] | None:
        """原子更新一条记录，返回更新后的记录或None。"""
        lock = self._get_lock(f"{module}:{entry_id}")
        with lock:
            row = self.find(module, entry_id)
            if row is None:
                return None
            row.update(updates)
            return dict(row)

    def atomic_append(self, module: str, entry: dict[str, Any]) -> dict[str, Any]:
        """原子追加一条记录。"""
        lock = self._get_lock(f"{module}:append")
        with lock:
            rows = self.rows(module)
            entry_copy = dict(entry)
            rows.append(entry_copy)
            return entry_copy

    # ------------------------------------------------------------
    # 航次生效回执：审阅轮次的批准凭证，供站位布设核验与回溯
    # ------------------------------------------------------------
    def next_receipt_no(self) -> str:
        """生成全局递增的航次生效回执编号。"""
        with self._global_lock:
            self._receipt_seq += 1
            return f"ECR-{self._receipt_seq:04d}"

    def issue_approval_receipt(self, receipt: dict[str, Any]) -> dict[str, Any]:
        """签发生效回执：同一时点只保留一份“生效中”的回执。

        - 同一航次计划书更早轮次的回执置为“已替代”；
        - 其他航次计划书处于“生效中”的回执置为“已停用”，
          保证一个时点只对外公布一份可用于新布设的生效计划。
        全程在同一把回执锁内完成，避免批准与布设并发时读到中间态。
        """
        lock = self._get_lock("approval_receipt:issue")
        with lock:
            plan_id = receipt.get("cruise_plan_id")
            for existing in self._approval_receipts:
                if existing.get("cruise_plan_id") != plan_id:
                    # 其他航次的生效回执：停用，保证全局只有一份生效计划
                    if existing.get("status") == "生效中":
                        existing["status"] = "已停用"
                        existing["supersede_reason"] = "其他航次计划批准生效，本回执停止用于新布设"
                    continue
                # 同一航次计划书的历史回执（含变更时已停用的）：由新一轮替代
                if existing.get("status") in ("生效中", "已停用"):
                    existing["status"] = "已替代"
                    existing["supersede_reason"] = (
                        f"第{receipt.get('review_round')}轮计划已批准，本回执被新一轮替代"
                    )
            stored = dict(receipt)
            self._approval_receipts.append(stored)
            return dict(stored)

    def deactivate_approval_receipts(
        self, cruise_plan_id: int, reason: str
    ) -> list[dict[str, Any]]:
        """停用某航次计划书当前生效中的回执（计划发起变更时调用）。

        旧一轮批准不得直接让变更稿生效；停用后该航次无生效回执，
        新一轮计划必须重新审阅和批准。已布设/已作业站位仍保留回执引用，
        其坐标按批准当时的快照继续可查。
        """
        lock = self._get_lock("approval_receipt:issue")
        deactivated: list[dict[str, Any]] = []
        with lock:
            for receipt in self._approval_receipts:
                if (
                    receipt.get("cruise_plan_id") == cruise_plan_id
                    and receipt.get("status") == "生效中"
                ):
                    receipt["status"] = "已停用"
                    receipt["supersede_reason"] = reason
                    deactivated.append(dict(receipt))
        return deactivated

    def get_approval_receipt(self, receipt_no: str) -> dict[str, Any] | None:
        """按回执编号查询航次生效回执。"""
        with self._global_lock:
            for receipt in self._approval_receipts:
                if receipt.get("receipt_no") == receipt_no:
                    return dict(receipt)
        return None

    def get_active_approval_receipt(self) -> dict[str, Any] | None:
        """获取当前唯一“生效中”、可用于新布设的航次生效回执。"""
        with self._global_lock:
            for receipt in self._approval_receipts:
                if receipt.get("status") == "生效中":
                    return dict(receipt)
        return None

    def list_approval_receipts(
        self, cruise_plan_id: int | None = None, cruise_code: str | None = None
    ) -> list[dict[str, Any]]:
        """列出生效回执，可按航次计划书或航次编号过滤。"""
        with self._global_lock:
            rows = [dict(r) for r in self._approval_receipts]
        if cruise_plan_id is not None:
            rows = [r for r in rows if r.get("cruise_plan_id") == cruise_plan_id]
        if cruise_code is not None:
            rows = [r for r in rows if r.get("cruise_code") == cruise_code]
        return rows

    def approval_receipt_lock(self):
        """回执签发/布设核验共用的串行锁。"""
        return self._get_lock("approval_receipt:issue")

    def is_idempotent(self, key: str) -> bool:
        """检查幂等键是否已存在（不写入）。"""
        with self._global_lock:
            return key in self._idempotent_keys

    def mark_idempotent(self, key: str) -> None:
        """标记幂等键为已执行。"""
        with self._global_lock:
            self._idempotent_keys.add(key)

    def clear_idempotent_key(self, key: str) -> None:
        """清除幂等键（用于失败回滚）。"""
        with self._global_lock:
            self._idempotent_keys.discard(key)

    def find_by(self, module: str, field: str, value: Any) -> list[dict[str, Any]]:
        """按字段值查找记录。"""
        rows = self.rows(module)
        if value is None:
            return [row for row in rows if row.get(field) is None]
        return [row for row in rows if str(row.get(field, "")) == str(value)]

    def find_one_by(self, module: str, field: str, value: Any) -> dict[str, Any] | None:
        """按字段值查找第一条记录。"""
        results = self.find_by(module, field, value)
        return results[0] if results else None

    def snapshot(self, module: str) -> list[dict[str, Any]]:
        """返回模块的当前快照（浅拷贝）。"""
        return [dict(row) for row in self.rows(module)]

    def save_snapshot(self, transaction_id: str, module: str, entry_id: int) -> str:
        """保存操作前的快照，支持失败后回滚。"""
        row = self.find(module, entry_id)
        key = f"{module}:{entry_id}"
        if row is None:
            return transaction_id
        with self._global_lock:
            self._rollback_log[transaction_id] = {
                "module": module,
                "entry_id": entry_id,
                "before": dict(row),
            }
        return transaction_id

    def rollback(self, transaction_id: str) -> bool:
        """回滚到指定事务前的状态。"""
        with self._global_lock:
            log = self._rollback_log.pop(transaction_id, None)
            if log is None:
                return False
            row = self.find(log["module"], log["entry_id"])
            if row is not None:
                row.clear()
                row.update(log["before"])
        return True

    def retry(self, func, *args, max_retries: int | None = None, **kwargs) -> tuple[Any, bool]:
        """带重试机制执行操作。返回(结果, 是否成功)。"""
        retries = max_retries if max_retries is not None else self._retry_max
        last_error = None
        for attempt in range(retries):
            try:
                result = func(*args, **kwargs)
                return result, True
            except Exception as e:
                last_error = e
                if attempt < retries - 1:
                    time.sleep(self._retry_delay * (attempt + 1))
        return None, False

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()