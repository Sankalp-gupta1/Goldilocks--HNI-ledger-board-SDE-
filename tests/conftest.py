import io
import pytest
from goldilocks.app import create_app

HEADERS = {"X-Goldilocks": "1"}
PASSWORD = "Fictional-example-pass-123"


@pytest.fixture
def app(tmp_path):
    return create_app(str(tmp_path / "test.db"), testing=True)


@pytest.fixture
def client(app):
    c = app.test_client()
    r = c.post(
        "/api/register",
        json={
            "name": "Fictional Tester",
            "email": "tester@example.com",
            "password": PASSWORD,
            "confirm_password": PASSWORD,
        },
        headers=HEADERS,
    )
    assert r.status_code == 201, r.json
    return c


def register(app, email="other@example.com"):
    c = app.test_client()
    r = c.post(
        "/api/register",
        json={
            "name": "Another Fictional Tester",
            "email": email,
            "password": PASSWORD,
            "confirm_password": PASSWORD,
        },
        headers=HEADERS,
    )
    assert r.status_code == 201, r.json
    return c, r.json


def upload(
    client, rows, account="Test account", name="test.csv", opening=None, closing=None
):
    data = {"account_id": account, "file": (io.BytesIO(rows.encode()), name)}
    if opening is not None:
        data["opening_balance"] = str(opening)
    if closing is not None:
        data["closing_balance"] = str(closing)
    return client.post("/api/import", data=data, headers=HEADERS)


def post(client, url, data):
    return client.post(url, json=data, headers=HEADERS)


CSV = "Date,Narration,Debit,Credit,Balance\n2026-01-01,SALARY CEDAR DESIGN STUDIO,,10000.00,11000.00\n2026-01-02,SWIGGY FOOD ORDER,125.25,,10874.75\n"
