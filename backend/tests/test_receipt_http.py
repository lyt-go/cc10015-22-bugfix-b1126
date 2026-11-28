# -*- coding: utf-8 -*-
"""HTTP 端到端验证：回执查询、动作接口 receipt_no 透传、路由不与动态路由冲突。"""
from fastapi.testclient import TestClient

from app.main import app
from app.store import store

client = TestClient(app)
store.reset()


def test_http_full_flow():
    store.reset()
    # 初始无生效回执
    r = client.get("/api/plan-receipts/active")
    assert r.status_code == 200 and r.json()["exists"] is False

    # 列表接口正常（验证固定路由未被 /{module} 抢占）
    r = client.get("/api/plan-receipts")
    assert r.status_code == 200 and r.json()["total"] == 0

    # 第一轮：编制中 -> 提交 -> 批准
    plan = store.find("cruise_plan", 1)
    plan["status"] = "编制中"
    plan["review_round"] = 1
    r = client.post("/api/cruise_plan/1/actions", json={"action": "提交审阅"})
    assert r.json()["ok"] and r.json()["entry"]["status"] == "已提交"
    r = client.post("/api/cruise_plan/1/actions", json={"action": "批准计划"})
    body = r.json()
    assert body["ok"]
    receipt_no = body["entry"]["active_receipt_no"]

    r = client.get(f"/api/plan-receipts/{receipt_no}")
    assert r.status_code == 200 and r.json()["receipt_no"] == receipt_no
    r = client.get("/api/plan-receipts/active")
    assert r.json()["receipt"]["receipt_no"] == receipt_no

    # 同轮重复批准被拒
    r = client.post("/api/cruise_plan/1/actions", json={"action": "批准计划"})
    assert r.json()["ok"] is False and "重复" in r.json()["message"]

    # 持回执确认站位
    station = store.find("station_deploy", 1)
    station["status"] = "待布设"
    r = client.post(
        "/api/station_deploy/1/actions",
        json={"action": "确认站位", "receipt_no": receipt_no},
    )
    assert r.json()["ok"], r.json()["message"]
    assert r.json()["entry"]["receipt_no"] == receipt_no

    # 站位追溯接口
    r = client.get("/api/station_deploy/1/trace")
    assert r.status_code == 200
    trace = r.json()
    assert trace["receipt_no"] == receipt_no
    assert trace["need_re_review"] is False

    # 不存在的回执 404；不存在的站位 404
    assert client.get("/api/plan-receipts/NOPE").status_code == 404
    assert client.get("/api/station_deploy/999/trace").status_code == 404

    # 变更 -> 旧回执停用、active 为空
    r = client.post("/api/cruise_plan/1/actions", json={"action": "变更计划"})
    assert r.json()["ok"] and r.json()["entry"]["review_round"] == 2
    assert client.get("/api/plan-receipts/active").json()["exists"] is False

    # 第二轮完整流程，旧回执变“已替代”，新回执生效
    client.post("/api/cruise_plan/1/actions", json={"action": "提交审阅"})
    r = client.post("/api/cruise_plan/1/actions", json={"action": "批准计划"})
    new_no = r.json()["entry"]["active_receipt_no"]
    assert new_no != receipt_no
    assert client.get(f"/api/plan-receipts/{receipt_no}").json()["status"] == "已替代"
    assert client.get("/api/plan-receipts/active").json()["receipt"]["receipt_no"] == new_no

    # 快照接口带出生效回执信息
    r = client.get("/api/snapshot/station_deploy")
    assert r.json()["cross_reference"]["active_receipt_no"] == new_no

    # 普通资源列表仍可用
    r = client.get("/api/cruise_plan")
    assert r.status_code == 200 and r.json()["total"] >= 1
    print("HTTP 端到端流程全部通过")


if __name__ == "__main__":
    test_http_full_flow()
