# -*- coding: utf-8 -*-
"""航次生效回执与审阅轮次专项测试。

覆盖需求：
1. 已批准计划变更后可再次提交和批准（旧轮幂等去重不影响新轮）；
2. 同一审阅轮重复提交/批准只保留一份生效回执，新改线仍可走完整流程；
3. 变更后旧回执停用，旧一轮批准不能让变更稿生效；
4. 站位凭生效回执布设并冻结批准当时坐标；
5. 持旧回执确认 S06 与新改线批准冲突时，可追溯站位归属轮次与是否需重审；
6. 甲航次批准不能替乙航次确认站位；
7. 一个时点只对外公布一份可用于新布设的生效计划；
8. 重审批准与 S06 布设并发、回执查询旧轮坐标、跨航次借用等链路。
"""
from __future__ import annotations

import threading

import pytest

from app.services import plan_receipt
from app.services.cruise_plan import CruisePlanService
from app.services.station_deploy import StationDeployService
from app.store import store


@pytest.fixture(autouse=True)
def reset_store():
    store.reset()


def _plan(plan_id: int = 1, status: str = "编制中"):
    row = store.find("cruise_plan", plan_id)
    row["status"] = status
    row["review_round"] = 1
    row["active_receipt_no"] = None
    return row


def _station(station_id: int = 1, code: str = "S06", cruise_code: str = ""):
    row = store.find("station_deploy", station_id)
    row["status"] = "待布设"
    row["站位编号"] = code
    row["经纬度坐标"] = f"{code}-坐标V1"
    row["航次编号"] = cruise_code
    return row


def _approve_round(plan_id: int) -> dict:
    """提交并批准计划当前轮次，返回签发的生效回执。"""
    svc = CruisePlanService()
    plan = store.find("cruise_plan", plan_id)
    if plan["status"] not in ("编制中", "已变更"):
        plan["status"] = "编制中"
    entry, msg = svc.run_action(plan_id, "提交审阅")
    assert entry is not None, msg
    entry, msg = svc.run_action(plan_id, "批准计划")
    assert entry is not None, msg
    receipt_no = entry["active_receipt_no"]
    receipt = plan_receipt.get_receipt(receipt_no)
    assert receipt is not None and receipt["status"] == "生效中"
    return receipt


# ============================================================
# 1. 根因修复：变更后第二次提交/批准不再被旧轮去重拦截
# ============================================================
def test_change_then_resubmit_and_reapprove_full_cycle():
    _plan(1, "已批准")
    svc = CruisePlanService()

    # 第一轮已批准，发起变更并完成新一轮完整审阅
    entry, _ = svc.run_action(1, "变更计划")
    assert entry["status"] == "已变更" and entry["review_round"] == 2

    entry, msg = svc.run_action(1, "提交审阅")
    assert entry is not None, f"第二次提交被拒：{msg}"
    assert entry["status"] == "已提交" and entry["submitted_round"] == 2

    entry, msg = svc.run_action(1, "批准计划")
    assert entry is not None, f"第二次批准被拒：{msg}"
    assert entry["status"] == "已批准" and entry["approved_round"] == 2


def test_multiple_change_rounds_each_have_independent_dedup():
    _plan(1, "已批准")
    svc = CruisePlanService()
    for expected_round in range(2, 5):
        entry, msg = svc.run_action(1, "变更计划")
        assert entry is not None, msg
        assert entry["review_round"] == expected_round
        entry, msg = svc.run_action(1, "提交审阅")
        assert entry is not None, msg
        entry, msg = svc.run_action(1, "批准计划")
        assert entry is not None, msg
        assert entry["review_round"] == expected_round


# ============================================================
# 2. 同一审阅轮重复提交/重复批准：只保留一份生效回执
# ============================================================
def test_same_round_duplicate_submit_and_approve_rejected():
    _plan(1, "编制中")
    svc = CruisePlanService()

    entry, _ = svc.run_action(1, "提交审阅")
    assert entry is not None
    dup, msg = svc.run_action(1, "提交审阅")
    assert dup is None and "重复" in msg

    entry, _ = svc.run_action(1, "批准计划")
    assert entry is not None
    first_receipt_no = entry["active_receipt_no"]

    dup, msg = svc.run_action(1, "批准计划")
    assert dup is None and "重复" in msg

    receipts = plan_receipt.list_receipts(cruise_plan_id=1)
    active = [r for r in receipts if r["status"] == "生效中"]
    assert len(receipts) == 1, "同一轮重复批准只应保留一份回执"
    assert len(active) == 1 and active[0]["receipt_no"] == first_receipt_no


def test_new_round_after_duplicates_still_runs_full_flow():
    _plan(1, "编制中")
    svc = CruisePlanService()
    svc.run_action(1, "提交审阅")
    svc.run_action(1, "提交审阅")  # 同轮重复，被拒
    svc.run_action(1, "批准计划")
    svc.run_action(1, "批准计划")  # 同轮重复，被拒
    assert len(plan_receipt.list_receipts(cruise_plan_id=1)) == 1

    # 新改线仍可走完整流程
    entry, msg = svc.run_action(1, "变更计划")
    assert entry is not None, msg
    entry, _ = svc.run_action(1, "提交审阅")
    assert entry is not None
    entry, msg = svc.run_action(1, "批准计划")
    assert entry is not None, msg
    assert len(plan_receipt.list_receipts(cruise_plan_id=1)) == 2


# ============================================================
# 3. 变更即停用旧回执：旧轮批准不能让变更稿生效
# ============================================================
def test_change_deactivates_old_receipt_and_blocks_new_deploy():
    _plan(1, "已批准")
    first = _approve_round(1)
    assert plan_receipt.get_active_receipt()["receipt_no"] == first["receipt_no"]

    svc = CruisePlanService()
    entry, _ = svc.run_action(1, "变更计划")
    assert entry["active_receipt_no"] is None

    # 旧回执已停用，不再对外公布为可布设计划
    assert plan_receipt.get_receipt(first["receipt_no"])["status"] == "已停用"
    assert plan_receipt.get_active_receipt() is None

    # 新一轮尚未批准时，新布设被拒绝
    _station(1, "S06")
    station_svc = StationDeployService()
    entry, msg = station_svc.run_action(1, "确认站位")
    assert entry is None
    assert "变更" in msg or "批准" in msg

    # 持旧回执也无法布设
    entry, msg = station_svc.run_action(
        1, "确认站位", {"receipt_no": first["receipt_no"]}
    )
    assert entry is None and "已不能用于新布设" in msg


# ============================================================
# 4. 站位凭生效回执布设，冻结批准当时坐标
# ============================================================
def test_station_deploy_binds_receipt_and_freezes_coordinates():
    _plan(1, "已批准")
    receipt = _approve_round(1)
    station = _station(1, "S06")
    original_coord = station["经纬度坐标"]

    svc = StationDeployService()
    entry, msg = svc.run_action(1, "确认站位", {"receipt_no": receipt["receipt_no"]})
    assert entry is not None, msg
    assert entry["status"] == "已布设"
    assert entry["receipt_no"] == receipt["receipt_no"]
    assert entry["review_round"] == receipt["review_round"]
    assert entry["cruise_plan_id"] == 1
    assert entry["frozen_coordinates"] == original_coord
    assert entry["approved_coordinates"] == original_coord

    # 之后改线并修改当前坐标，已布设站位仍引用批准当时坐标
    plan_svc = CruisePlanService()
    plan_svc.run_action(1, "变更计划")
    station["经纬度坐标"] = "S06-坐标V2"
    assert store.find("station_deploy", 1)["frozen_coordinates"] == original_coord


def test_deploy_without_receipt_uses_active_receipt():
    _plan(1, "已批准")
    receipt = _approve_round(1)
    _station(1, "S06")
    entry, msg = StationDeployService().run_action(1, "确认站位")
    assert entry is not None, msg
    assert entry["receipt_no"] == receipt["receipt_no"]


def test_legacy_approved_plan_without_receipt_still_supported():
    # 历史数据：直接置已批准、没有任何回执时保持旧行为可用
    _plan(1, "已批准")
    _station(1, "S06")
    entry, msg = StationDeployService().run_action(1, "确认站位")
    assert entry is not None, msg
    assert entry["status"] == "已布设"


# ============================================================
# 5. S06 冲突场景：旧回执布设 vs 新改线批准，追溯归属与重审
# ============================================================
def test_s06_conflict_trace_round_and_re_review():
    # S06 在第一轮批准前就已登记，回执快照会保存批准当时的坐标
    _plan(1, "编制中")
    station = _station(1, "S06")
    first_coord = station["经纬度坐标"]
    first = _approve_round(1)

    # 船长持第一轮回执确认 S06
    _station(1, "S06")
    station_svc = StationDeployService()
    entry, msg = station_svc.run_action(
        1, "确认站位", {"receipt_no": first["receipt_no"]}
    )
    assert entry is not None, msg
    frozen = entry["frozen_coordinates"]

    # 新改线：变更、重新审阅批准，签发第二轮回执
    plan_svc = CruisePlanService()
    plan_svc.run_action(1, "变更计划")
    second = _approve_round(1)
    assert second["review_round"] == 2
    assert plan_receipt.get_receipt(first["receipt_no"])["status"] == "已替代"
    assert plan_receipt.get_active_receipt()["receipt_no"] == second["receipt_no"]

    # 持第一轮旧回执确认另一待布设站位被拒，并指出冲突轮次
    _station(2, "S07")
    entry, msg = station_svc.run_action(
        2, "确认站位", {"receipt_no": first["receipt_no"]}
    )
    assert entry is None and "已不能用于新布设" in msg and "第1轮" in msg

    # 追溯：站位归属第 1 轮计划，下一步需重审
    trace = station_svc.get_station_trace(1)
    assert trace["receipt_no"] == first["receipt_no"]
    assert trace["review_round"] == 1
    assert trace["receipt_status"] == "已替代"
    assert trace["frozen_coordinates"] == frozen
    assert trace["need_re_review"] is True
    assert trace["eligibility"] == plan_receipt.STATION_RE_REVIEW

    # 按旧回执可直接查回批准当时的 S06 坐标，不受后续改线影响
    old_receipt = plan_receipt.get_receipt(first["receipt_no"])
    assert old_receipt["review_round"] == 1 and old_receipt["status"] == "已替代"
    assert old_receipt["station_coordinates"]["S06"] == first_coord == frozen
    assert trace["approved_coordinates"] == frozen


def test_station_follow_when_receipt_still_active_and_coord_unchanged():
    _plan(1, "已批准")
    receipt = _approve_round(1)
    _station(1, "S06")
    svc = StationDeployService()
    svc.run_action(1, "确认站位", {"receipt_no": receipt["receipt_no"]})

    eligibility = svc.get_eligibility(1)
    assert eligibility["need_re_review"] is False
    assert eligibility["eligibility"] == plan_receipt.STATION_FOLLOW


def test_coord_drift_requires_re_review_even_with_active_receipt():
    _plan(1, "已批准")
    receipt = _approve_round(1)
    _station(1, "S06")
    svc = StationDeployService()
    svc.run_action(1, "确认站位", {"receipt_no": receipt["receipt_no"]})

    store.find("station_deploy", 1)["经纬度坐标"] = "S06-被挪动"
    eligibility = svc.get_eligibility(1)
    assert eligibility["need_re_review"] is True


# ============================================================
# 6. 甲航次批准不能替乙航次确认站位
# ============================================================
def test_cruise_a_receipt_cannot_confirm_cruise_b_station():
    # 甲航次（计划书1）完整批准
    plan_a = _plan(1, "已批准")
    plan_a["航次编号"] = "CRUI-A"
    receipt_a = _approve_round(1)

    # 乙航次（计划书2）单独编制，站位归属乙航次
    plan_b = _plan(2, "编制中")
    plan_b["航次编号"] = "CRUI-B"
    station = _station(1, "S06", cruise_code="CRUI-B")

    svc = StationDeployService()
    entry, msg = svc.run_action(
        1, "确认站位", {"receipt_no": receipt_a["receipt_no"]}
    )
    assert entry is None and "不一致" in msg and "CRUI-A" in msg and "CRUI-B" in msg

    # 不带回执时，当前生效回执属于甲，同样不能给乙确认
    entry, msg = svc.run_action(1, "确认站位")
    assert entry is None and "不一致" in msg

    # 乙航次自己走完审批后，凭乙的回执可以确认
    entry, _ = CruisePlanService().run_action(2, "提交审阅")
    entry, _ = CruisePlanService().run_action(2, "批准计划")
    receipt_b = plan_receipt.get_active_receipt()
    assert receipt_b["cruise_code"] == "CRUI-B"
    entry, msg = svc.run_action(
        1, "确认站位", {"receipt_no": receipt_b["receipt_no"]}
    )
    assert entry is not None, msg
    assert entry["cruise_code"] == "CRUI-B"

    # 两航次回执分别可核验
    assert len(plan_receipt.list_receipts(cruise_code="CRUI-A")) == 1
    assert len(plan_receipt.list_receipts(cruise_code="CRUI-B")) == 1


# ============================================================
# 7. 一个时点只对外公布一份可用于新布设的生效计划
# ============================================================
def test_only_one_active_receipt_globally():
    plan_a = _plan(1, "已批准")
    plan_a["航次编号"] = "CRUI-A"
    receipt_a = _approve_round(1)
    assert plan_receipt.get_active_receipt()["receipt_no"] == receipt_a["receipt_no"]

    plan_b = _plan(2, "编制中")
    plan_b["航次编号"] = "CRUI-B"
    CruisePlanService().run_action(2, "提交审阅")
    entry, _ = CruisePlanService().run_action(2, "批准计划")
    receipt_b_no = entry["active_receipt_no"]

    assert plan_receipt.get_active_receipt()["receipt_no"] == receipt_b_no
    assert plan_receipt.get_receipt(receipt_a["receipt_no"])["status"] == "已停用"
    active = [r for r in plan_receipt.list_receipts() if r["status"] == "生效中"]
    assert len(active) == 1


# ============================================================
# 8. 并发：重审批准与 S06 布设同时到达
# ============================================================
def test_concurrent_reapproval_and_s06_deploy_serialized():
    _plan(1, "已批准")
    first = _approve_round(1)
    _station(1, "S06")
    station_svc = StationDeployService()
    plan_svc = CruisePlanService()

    barrier = threading.Barrier(2)
    deploy_result = {}
    reapprove_result = {}

    def deploy_with_old_receipt():
        barrier.wait()
        deploy_result["entry"], deploy_result["msg"] = station_svc.run_action(
            1, "确认站位", {"receipt_no": first["receipt_no"]}
        )

    def change_and_reapprove():
        barrier.wait()
        entry, msg = plan_svc.run_action(1, "变更计划")
        reapprove_result["changed"] = entry is not None
        if entry is not None:
            entry, msg = plan_svc.run_action(1, "提交审阅")
            entry, msg = plan_svc.run_action(1, "批准计划")
            reapprove_result["approved"] = entry is not None
            reapprove_result["receipt_no"] = (
                entry["active_receipt_no"] if entry is not None else None
            )

    t1 = threading.Thread(target=deploy_with_old_receipt)
    t2 = threading.Thread(target=change_and_reapprove)
    t1.start(); t2.start(); t1.join(); t2.join()

    # 重审一定完成；旧回执布设与新批准互斥，不会出现两份生效回执
    assert reapprove_result["approved"] is True
    active = [r for r in plan_receipt.list_receipts() if r["status"] == "生效中"]
    assert len(active) == 1
    assert active[0]["receipt_no"] == reapprove_result["receipt_no"]

    station = store.find("station_deploy", 1)
    if deploy_result["entry"] is not None:
        # 布设先于变更落库：绑定的是第一轮回执，事后追溯判定需重审
        assert station["receipt_no"] == first["receipt_no"]
        trace = station_svc.get_station_trace(1)
        assert trace["need_re_review"] is True
    else:
        # 变更先落库：旧回执已停用，布设被拒，站位仍待布设
        assert station["status"] == "待布设"
        assert "已不能用于新布设" in deploy_result["msg"]


# ============================================================
# 9. 已作业站位的批准坐标不被后续改线改写
# ============================================================
def test_completed_operation_keeps_approved_coordinates():
    _plan(1, "已批准")
    receipt = _approve_round(1)
    station = _station(1, "S06")
    svc = StationDeployService()
    svc.run_action(1, "确认站位", {"receipt_no": receipt["receipt_no"]})
    for r in store.rows("weather_obs"):
        r["status"] = "已同步"
    entry, msg = svc.run_action(1, "开始作业")
    assert entry is not None, msg and entry["status"] == "已作业"
    frozen = entry["frozen_coordinates"]

    # 多轮改线后，已作业站位仍引用批准当时坐标
    plan_svc = CruisePlanService()
    for _ in range(2):
        plan_svc.run_action(1, "变更计划")
        _approve_round(1)
    station["经纬度坐标"] = "S06-坐标V3"

    trace = svc.get_station_trace(1)
    assert trace["frozen_coordinates"] == frozen
    assert trace["approved_coordinates"] == frozen
    assert trace["status"] == "已作业"


# ============================================================
# 10. 回执查询接口数据完整性
# ============================================================
def test_receipt_snapshot_contains_plan_and_station_coords():
    _plan(1, "已批准")
    receipt = _approve_round(1)
    assert receipt["plan_version"] == "R1"
    assert receipt["cruise_code"] == store.find("cruise_plan", 1)["航次编号"]
    seed_station = store.rows("station_deploy")[0]
    assert seed_station["站位编号"] in receipt["station_coordinates"]
    assert (
        receipt["station_coordinates"][seed_station["站位编号"]]
        == seed_station["经纬度坐标"]
    )
    assert receipt["issued_at"]
    assert receipt["plan_snapshot"]["调查海域"] is not None
