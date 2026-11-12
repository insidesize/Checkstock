"""Добавление, переименование и удаление ФФ, в том числе с остатками."""


def test_add_and_rename_ff(client):
    r = client.post("/api/ffs", json={"name": "  Восток "})
    assert r.status_code == 201 and r.json()["name"] == "Восток"
    ff_id = r.json()["id"]
    assert client.patch(f"/api/ffs/{ff_id}", json={"name": "Дальний Восток"}).json()["name"] == "Дальний Восток"


def test_active_ff_names_must_be_unique_case_insensitive(client):
    assert client.post("/api/ffs", json={"name": "север"}).status_code == 409
    assert client.post("/api/ffs", json={"name": "   "}).status_code == 422


def test_delete_ff_keeps_stock_and_history(client, stocks, ff):
    movements_before = client.get("/api/movements", params={"ff_id": ff["Север"]}).json()
    assert client.delete(f"/api/ffs/{ff['Север']}").json()["archived"] is True

    ffs = {f["name"]: f for f in client.get("/api/ffs").json()}
    assert ffs["Север"]["archived"] is True
    # товар по-прежнему числится на удалённом ФФ, итоги не изменились
    assert stocks.at("SKU-101", ff["Север"])["on_hand"] == 10
    assert stocks.row("SKU-101")["totals"]["on_hand"] == 16
    assert client.get("/api/movements", params={"ff_id": ff["Север"]}).json() == movements_before
    stocks.audit_ok()


def test_deleted_ff_cannot_receive_or_reserve_but_goods_can_be_moved_out(client, stocks, ff):
    client.delete(f"/api/ffs/{ff['Север']}")
    n, s = ff["Север"], ff["Юг"]

    assert client.post("/api/receipts", json={"sku": "SKU-101", "ff_id": n, "qty": 1}).status_code == 409
    assert client.post("/api/fbs", json={"sku": "SKU-101", "ff_id": n, "qty": 1,
                                         "order_ref": "A"}).status_code == 409
    assert client.post("/api/transfers", json={"sku": "SKU-101", "from_ff_id": s, "to_ff_id": n,
                                               "qty": 1}).status_code == 409

    # вывоз остатков с удалённого ФФ разрешён
    out = client.post("/api/transfers", json={"sku": "SKU-101", "from_ff_id": n, "to_ff_id": s, "qty": 10})
    assert out.status_code == 201
    client.post(f"/api/transfers/{out.json()['document']['id']}/receive")
    assert stocks.at("SKU-101", n)["on_hand"] == 0
    assert stocks.at("SKU-101", s)["on_hand"] == 16
    stocks.audit_ok()


def test_documents_in_flight_survive_ff_deletion(client, stocks, ff):
    n, s = ff["Север"], ff["Юг"]
    fbs = client.post("/api/fbs", json={"sku": "SKU-101", "ff_id": n, "qty": 4,
                                        "order_ref": "A"}).json()["document"]["id"]
    tr = client.post("/api/transfers", json={"sku": "SKU-101", "from_ff_id": s, "to_ff_id": n,
                                             "qty": 2}).json()["document"]["id"]
    client.delete(f"/api/ffs/{n}")

    assert client.post(f"/api/fbs/{fbs}/ship").status_code == 200       # резерв можно отгрузить
    assert client.post(f"/api/transfers/{tr}/receive").status_code == 200  # едущий товар можно принять
    assert stocks.at("SKU-101", n)["on_hand"] == 10 - 4 + 2  # числится на удалённом ФФ
    stocks.audit_ok()


def test_restore_and_name_reuse(client, ff):
    client.delete(f"/api/ffs/{ff['Север']}")
    assert client.post("/api/ffs", json={"name": "Север"}).status_code == 201  # имя освободилось
    assert client.post(f"/api/ffs/{ff['Север']}/restore").status_code == 409   # теперь занято
