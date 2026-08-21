import os

os.environ.setdefault("REDACTOR_API_KEY", "test-secret-key")
os.environ.setdefault("FLASK_SECRET_KEY", "test-flask-secret")

import pytest

from app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer test-secret-key"}
