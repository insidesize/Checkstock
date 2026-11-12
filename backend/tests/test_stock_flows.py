"""Учёт после прихода, резерва, перемещения, отправки и приёмки."""


def test_seed_matches_assignment(stocks, ff):
    assert stocks.at("SKU-101", ff["Север"])["on_hand"] == 10
    assert stocks.at("SKU-101", ff["Юг"])["on_hand"] == 6
    assert stocks.at("SKU-102", ff["Север"])["on_hand"] == 5
    assert stocks.at("SKU-102", ff["Юг"])["on_hand"] == 0
    assert stocks.at("SKU-103", ff["Север"])["on_hand"] == 0
    assert stocks.at("SKU-103", ff["Юг"])["on_hand"] == 4
    stocks.audit_ok()


def test_receipt_adds_to_on_hand_and_available(client, stocks, ff):
    r = client.post("/api/receipts", json={"sku": "SKU-103", "ff_id": ff["Север"], "qty": 7})
    assert r.status_code == 201
    assert stocks.at("SKU-103", ff["Север"]) == {"on_hand": 7, "reserved": 0, "available": 7}
    stocks.audit_ok()


def test_fbs_reserve_blocks_available_but_not_on_hand(client, stocks, ff):
    client.post("/api/fbs", json={"sku": "SKU-101", "ff_id": ff["Север"], "qty": 4, "order_ref": "A-1"})
    assert stocks.at("SKU-101", ff["Север"]) == {"on_hand": 10, "reserved": 4, "available": 6}
    stocks.audit_ok()


def test_fbs_ship_removes_goods_and_shows_them_going_to_customer(client, stocks, ff):
    doc = client.post("/api/fbs", json={"sku": "SKU-101", "ff_id": ff["Север"], "qty": 4,
                                        "order_ref": "A-1"}).json()["document"]
    client.post(f"/api/fbs/{doc['id']}/ship")
    assert stocks.at("SKU-101", ff["Север"]) == {"on_hand": 6, "reserved": 0, "available": 6}
    assert stocks.row("SKU-101")["in_transit"]["to_customer"] == 4
    assert stocks.row("SKU-101")["total_tracked"] == 16  # ничего не потеряли: 6 + 6 + 4
    stocks.audit_ok()


def test_fbs_cancel_releases_reserve(client, stocks, ff):
    doc = client.post("/api/fbs", json={"sku": "SKU-101", "ff_id": ff["Север"], "qty": 4,
                                        "order_ref": "A-1"}).json()["document"]
    client.post(f"/api/fbs/{doc['id']}/cancel")
    assert stocks.at("SKU-101", ff["Север"]) == {"on_hand": 10, "reserved": 0, "available": 10}
    stocks.audit_ok()


def test_transfer_goods_are_in_transit_until_received(client, stocks, ff):
    doc = client.post("/api/transfers", json={"sku": "SKU-101", "from_ff_id": ff["Север"],
                                              "to_ff_id": ff["Юг"], "qty": 3}).json()["document"]
    # отправлено: на Севере убыло, на Юге ещё не прибыло, но товар учтён «в пути»
    assert stocks.at("SKU-101", ff["Север"])["on_hand"] == 7
    assert stocks.at("SKU-101", ff["Юг"])["on_hand"] == 6
    assert stocks.row("SKU-101")["in_transit"]["between_ff"] == 3
    assert stocks.row("SKU-101")["total_tracked"] == 16
    stocks.audit_ok()

    client.post(f"/api/transfers/{doc['id']}/receive")
    assert stocks.at("SKU-101", ff["Юг"])["on_hand"] == 9
    assert stocks.row("SKU-101")["in_transit"]["between_ff"] == 0
    assert stocks.row("SKU-101")["total_tracked"] == 16
    stocks.audit_ok()


def test_fbo_leaves_ff_and_is_in_transit_until_accepted(client, stocks, ff):
    doc = client.post("/api/fbo", json={"sku": "SKU-101", "from_ff_id": ff["Юг"], "qty": 5,
                                        "marketplace_warehouse": "WB Коледино"}).json()["document"]
    assert stocks.at("SKU-101", ff["Юг"])["on_hand"] == 1
    assert stocks.row("SKU-101")["in_transit"]["to_marketplace"] == 5
    stocks.audit_ok()

    client.post(f"/api/fbo/{doc['id']}/accept")
    row = stocks.row("SKU-101")
    assert row["in_transit"]["to_marketplace"] == 0 and row["in_transit"]["at_marketplace"] == 5
    assert row["total_tracked"] == 16
    stocks.audit_ok()


def test_reserved_goods_cannot_be_sent_elsewhere(client, ff):
    client.post("/api/fbs", json={"sku": "SKU-101", "ff_id": ff["Север"], "qty": 8, "order_ref": "A-1"})
    r = client.post("/api/transfers", json={"sku": "SKU-101", "from_ff_id": ff["Север"],
                                            "to_ff_id": ff["Юг"], "qty": 3})
    assert r.status_code == 409 and r.json()["detail"]["available"] == 2


def test_history_shows_what_where_and_how_much(client, ff):
    client.post("/api/transfers", json={"sku": "SKU-101", "from_ff_id": ff["Север"],
                                        "to_ff_id": ff["Юг"], "qty": 3})
    last = client.get("/api/movements", params={"sku": "SKU-101"}).json()[0]
    assert (last["op"], last["qty"], last["from_bucket"], last["from_ff_id"], last["to_bucket"]) == (
        "TRANSFER_SHIP", 3, "FF_AVAILABLE", ff["Север"], "TRANSFER_TRANSIT")


def test_audit_stays_green_through_a_long_mixed_scenario(client, stocks, ff):
    n, s = ff["Север"], ff["Юг"]

    def do(url, body=None):
        r = client.post(url, json=body)
        assert r.status_code in (200, 201), r.text
        stocks.audit_ok()
        return r.json()["document"]["id"]

    do("/api/receipts", {"sku": "SKU-102", "ff_id": s, "qty": 9})
    fbs = do("/api/fbs", {"sku": "SKU-102", "ff_id": n, "qty": 2, "order_ref": "X-1"})
    tr = do("/api/transfers", {"sku": "SKU-102", "from_ff_id": s, "to_ff_id": n, "qty": 4})
    fbo = do("/api/fbo", {"sku": "SKU-103", "from_ff_id": s, "qty": 3, "marketplace_warehouse": "Ozon"})
    do(f"/api/fbs/{fbs}/ship")
    do(f"/api/transfers/{tr}/receive")
    do(f"/api/fbo/{fbo}/accept")
    assert stocks.row("SKU-102")["total_tracked"] == 14  # 5 из сида + 9 приход
    assert stocks.row("SKU-103")["total_tracked"] == 4
