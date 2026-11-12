"""Нехватка на выбранном ФФ при достаточном общем остатке."""


def test_shortage_at_ff_with_enough_total_returns_hint_and_changes_nothing(client, stocks, ff):
    # SKU-102: Север 5, Юг 0. Резервируем 3 на Юге.
    docs_before = client.get("/api/documents").json()
    r = client.post("/api/fbs", json={"sku": "SKU-102", "ff_id": ff["Юг"], "qty": 3, "order_ref": "A-1"})
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["code"] == "INSUFFICIENT_AT_FF"
    assert (d["requested"], d["available"], d["shortage"], d["total_available"]) == (3, 0, 3, 5)
    assert d["alternatives"] == [{"ff_id": ff["Север"], "name": "Север", "archived": False, "available": 5}]
    # ничего не списалось и не зарезервировалось, документ не создан
    assert stocks.at("SKU-102", ff["Юг"]) == {"on_hand": 0, "reserved": 0, "available": 0}
    assert stocks.at("SKU-102", ff["Север"]) == {"on_hand": 5, "reserved": 0, "available": 5}
    assert client.get("/api/documents").json() == docs_before
    stocks.audit_ok()


def test_shortage_hint_can_be_followed_with_a_transfer(client, stocks, ff):
    d = client.post("/api/fbs", json={"sku": "SKU-102", "ff_id": ff["Юг"], "qty": 3,
                                      "order_ref": "A-1"}).json()["detail"]
    alt = d["alternatives"][0]
    tr = client.post("/api/transfers", json={"sku": "SKU-102", "from_ff_id": alt["ff_id"],
                                             "to_ff_id": ff["Юг"], "qty": d["shortage"]})
    client.post(f"/api/transfers/{tr.json()['document']['id']}/receive")
    r = client.post("/api/fbs", json={"sku": "SKU-102", "ff_id": ff["Юг"], "qty": 3, "order_ref": "A-1"})
    assert r.status_code == 201
    assert stocks.at("SKU-102", ff["Юг"]) == {"on_hand": 3, "reserved": 3, "available": 0}
    stocks.audit_ok()


def test_total_shortage_is_reported_separately(client, ff):
    r = client.post("/api/fbs", json={"sku": "SKU-102", "ff_id": ff["Юг"], "qty": 6, "order_ref": "A-1"})
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["code"] == "INSUFFICIENT_TOTAL" and d["total_available"] == 5


def test_reserved_units_are_not_offered_as_alternatives(client, ff):
    client.post("/api/fbs", json={"sku": "SKU-102", "ff_id": ff["Север"], "qty": 4, "order_ref": "A-1"})
    d = client.post("/api/fbs", json={"sku": "SKU-102", "ff_id": ff["Юг"], "qty": 2,
                                      "order_ref": "A-2"}).json()["detail"]
    assert d["code"] == "INSUFFICIENT_TOTAL"  # на Севере свободна только 1 шт
    assert d["total_available"] == 1


def test_shortage_also_guards_transfer_and_fbo(client, stocks, ff):
    r1 = client.post("/api/transfers", json={"sku": "SKU-103", "from_ff_id": ff["Север"],
                                             "to_ff_id": ff["Юг"], "qty": 1})
    r2 = client.post("/api/fbo", json={"sku": "SKU-103", "from_ff_id": ff["Север"], "qty": 1,
                                       "marketplace_warehouse": "WB"})
    assert r1.status_code == r2.status_code == 409
    assert r1.json()["detail"]["alternatives"][0]["name"] == "Юг"
    stocks.audit_ok()
