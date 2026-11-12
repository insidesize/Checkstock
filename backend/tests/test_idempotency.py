"""Повторное подтверждение не списывает товар дважды; гонки не уводят остаток в минус."""
from concurrent.futures import ThreadPoolExecutor

from app import service
from app.db import SessionLocal


def test_repeated_receive_transfer_credits_destination_once(client, stocks, ff):
    doc = client.post("/api/transfers", json={"sku": "SKU-101", "from_ff_id": ff["Север"],
                                              "to_ff_id": ff["Юг"], "qty": 3}).json()["document"]
    first = client.post(f"/api/transfers/{doc['id']}/receive").json()
    second = client.post(f"/api/transfers/{doc['id']}/receive")
    assert first["already_applied"] is False
    assert second.status_code == 200 and second.json()["already_applied"] is True
    assert stocks.at("SKU-101", ff["Юг"])["on_hand"] == 9  # а не 12
    stocks.audit_ok()


def test_repeated_fbs_ship_decrements_once(client, stocks, ff):
    doc = client.post("/api/fbs", json={"sku": "SKU-101", "ff_id": ff["Север"], "qty": 4,
                                        "order_ref": "A"}).json()["document"]
    for _ in range(3):
        assert client.post(f"/api/fbs/{doc['id']}/ship").status_code == 200
    assert stocks.at("SKU-101", ff["Север"]) == {"on_hand": 6, "reserved": 0, "available": 6}
    assert stocks.row("SKU-101")["in_transit"]["to_customer"] == 4
    stocks.audit_ok()


def test_repeated_fbo_accept_and_cancel_are_harmless(client, stocks, ff):
    fbo = client.post("/api/fbo", json={"sku": "SKU-101", "from_ff_id": ff["Север"], "qty": 2,
                                        "marketplace_warehouse": "WB"}).json()["document"]["id"]
    fbs = client.post("/api/fbs", json={"sku": "SKU-101", "ff_id": ff["Юг"], "qty": 2,
                                        "order_ref": "A"}).json()["document"]["id"]
    for _ in range(2):
        client.post(f"/api/fbo/{fbo}/accept")
        client.post(f"/api/fbs/{fbs}/cancel")
    assert stocks.row("SKU-101")["in_transit"]["at_marketplace"] == 2
    assert stocks.at("SKU-101", ff["Юг"])["reserved"] == 0
    stocks.audit_ok()


def test_conflicting_transitions_are_rejected(client, ff):
    fbs = client.post("/api/fbs", json={"sku": "SKU-101", "ff_id": ff["Север"], "qty": 1,
                                        "order_ref": "A"}).json()["document"]["id"]
    client.post(f"/api/fbs/{fbs}/ship")
    assert client.post(f"/api/fbs/{fbs}/cancel").status_code == 409  # уже отправлен
    other = client.post("/api/fbs", json={"sku": "SKU-101", "ff_id": ff["Север"], "qty": 1,
                                          "order_ref": "B"}).json()["document"]["id"]
    client.post(f"/api/fbs/{other}/cancel")
    assert client.post(f"/api/fbs/{other}/ship").status_code == 409   # уже отменён
    assert client.post(f"/api/transfers/{fbs}/receive").status_code == 404  # чужой тип документа


def test_same_request_id_creates_one_document(client, stocks, ff):
    body = {"sku": "SKU-101", "from_ff_id": ff["Север"], "to_ff_id": ff["Юг"], "qty": 3,
            "client_request_id": "form-1"}
    a = client.post("/api/transfers", json=body).json()
    b = client.post("/api/transfers", json=body).json()
    assert a["document"]["id"] == b["document"]["id"] and b["already_applied"] is True
    assert stocks.at("SKU-101", ff["Север"])["on_hand"] == 7  # списано один раз
    assert len(client.get("/api/documents", params={"kind": "TRANSFER"}).json()) == 1
    stocks.audit_ok()


def test_request_id_cannot_be_reused_for_another_operation(client, ff):
    client.post("/api/receipts", json={"sku": "SKU-101", "ff_id": ff["Север"], "qty": 1,
                                       "client_request_id": "k"})
    r = client.post("/api/fbs", json={"sku": "SKU-101", "ff_id": ff["Север"], "qty": 1,
                                      "order_ref": "A", "client_request_id": "k"})
    assert r.status_code == 409


def test_validation(client, ff):
    ok = {"sku": "SKU-101", "ff_id": ff["Север"]}
    assert client.post("/api/receipts", json={**ok, "qty": 0}).status_code == 422
    assert client.post("/api/receipts", json={**ok, "qty": -5}).status_code == 422
    assert client.post("/api/receipts", json={"sku": "NOPE", "ff_id": ff["Север"], "qty": 1}).status_code == 404
    assert client.post("/api/transfers", json={"sku": "SKU-101", "from_ff_id": ff["Север"],
                                               "to_ff_id": ff["Север"], "qty": 1}).status_code == 422


# ───────── параллельные запросы (настоящие транзакции, не моки) ─────────

def _run_parallel(fn, n):
    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(lambda i: fn(i), range(n)))


def _call(fn, *args):
    with SessionLocal() as s:
        try:
            return fn(s, *args)
        except service.DomainError as e:
            return e


def test_parallel_reserves_never_oversell(client, stocks, ff):
    # на Севере 10 шт, 8 потоков по 3 шт: поместиться могут ровно 3 резерва (9 шт)
    results = _run_parallel(
        lambda i: _call(service.reserve_fbs, "SKU-101", ff["Север"], 3, f"P-{i}", f"par-{i}"), 8
    )
    ok = [r for r in results if isinstance(r, dict)]
    failed = [r for r in results if isinstance(r, service.DomainError)]
    assert len(ok) == 3 and len(failed) == 5
    assert stocks.at("SKU-101", ff["Север"]) == {"on_hand": 10, "reserved": 9, "available": 1}
    stocks.audit_ok()


def test_parallel_duplicate_create_makes_one_document(client, stocks, ff):
    results = _run_parallel(
        lambda i: _call(service.create_transfer, "SKU-101", ff["Север"], ff["Юг"], 3, "same-key"), 6
    )
    assert all(isinstance(r, dict) for r in results)
    assert len({r["document"]["id"] for r in results}) == 1
    assert sum(1 for r in results if not r["already_applied"]) == 1
    assert stocks.at("SKU-101", ff["Север"])["on_hand"] == 7
    stocks.audit_ok()


def test_parallel_confirmations_apply_once(client, stocks, ff):
    doc_id = client.post("/api/transfers", json={"sku": "SKU-101", "from_ff_id": ff["Север"],
                                                 "to_ff_id": ff["Юг"], "qty": 3}).json()["document"]["id"]
    results = _run_parallel(lambda i: _call(service.receive_transfer, doc_id), 6)
    assert sum(1 for r in results if not r["already_applied"]) == 1
    assert stocks.at("SKU-101", ff["Юг"])["on_hand"] == 9
    stocks.audit_ok()
