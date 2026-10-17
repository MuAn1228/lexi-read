"""Public visitors must never inherit user 1 or access another browser's records."""
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from backend.auth import token_digest
from backend.config import Settings
from backend.database import get_session
from backend.main import app
from backend.models import Base, Document, GuestCredential, User, Vocabulary, WordOccurrence
from backend.ocr.service import OCRResult, OCRWord


@pytest.fixture
def public_app(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'public.sqlite3'}",
                           connect_args={"check_same_thread": False, "timeout": 10})

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    image_path = tmp_path / "legacy.png"
    Image.new("RGB", (120, 60), "white").save(image_path)
    with factory() as session:
        session.add(User(id=1))
        session.flush()
        local_doc = Document(user_id=1, title="Local private material", image_path=str(image_path),
                             width=120, height=60, ocr_text="private")
        local_doc.words.append(WordOccurrence(word="private", lemma="private", sentence="private",
                                             bbox=[0, 0, 40, 20], line=0, confidence=1, bbox_source="demo"))
        session.add_all([local_doc, Vocabulary(user_id=1, word="private", lemma="private", status="KNOWN")])
        session.commit()
        legacy_id = local_doc.id

    settings = Settings(app_mode="public", data_dir=tmp_path, llm_provider="offline",
                        cors_origins="https://reader.github.io", _env_file=None)
    for module in ("backend.auth", "backend.api.routes", "backend.services.documents",
                   "backend.services.llm", "backend.public_limits"):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)
    monkeypatch.setattr("backend.main.settings", settings)
    monkeypatch.setattr("backend.main.initialize_database", lambda: None)

    def session_override():
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    try:
        with TestClient(app) as client:
            yield client, factory, settings, legacy_id
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def guest(client):
    response = client.post("/api/session")
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    return data, {"Authorization": f"Bearer {data['token']}"}


def demo(client, headers):
    response = client.post("/api/documents/demo", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def feedback_payload(word):
    return {"word": word, "action": "UNKNOWN", "request_id": str(uuid4())}


def test_guests_are_new_users_and_only_hashes_are_stored(public_app):
    client, factory, _, legacy_id = public_app
    assert client.get("/api/health").json()["auth_mode"] == "guest"
    first, first_headers = guest(client)
    second, second_headers = guest(client)
    assert first["user_id"] != second["user_id"]
    assert min(first["user_id"], second["user_id"]) > 1
    assert first["token"] != second["token"]
    with factory() as session:
        credentials = list(session.scalars(select(GuestCredential)))
        assert {row.token_hash for row in credentials} == {token_digest(first["token"]), token_digest(second["token"])}
        assert all(row.token_hash not in {first["token"], second["token"]} for row in credentials)
    for headers in (first_headers, second_headers):
        assert client.get("/api/documents", headers=headers).json() == []
        assert client.get("/api/vocabulary", headers=headers).json() == []
        assert client.get(f"/api/documents/{legacy_id}", headers=headers).status_code == 404
        assert client.get(f"/api/documents/{legacy_id}/image", headers=headers).status_code == 404


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer invalid"},
                                     {"Authorization": "Bearer " + "a" * 43}, {"X-User-Id": "1"}])
def test_all_private_endpoints_reject_missing_or_forged_credentials(public_app, headers):
    client, _, _, _ = public_app
    for path in ("/documents", "/documents/1", "/documents/1/image", "/vocabulary",
                 "/vocabulary/private/history", "/events", "/dashboard", "/relations"):
        response = client.get("/api" + path, headers=headers)
        assert response.status_code == 401, (path, response.text)
        assert response.headers["www-authenticate"] == "Bearer"
    for path, body in (("/documents/demo", None), ("/words/analyze", {"occurrence_id": 1}),
                       ("/words/recommend", {"occurrence_id": 1}),
                       ("/recommendations/1/feedback", feedback_payload("private"))):
        assert client.post("/api" + path, json=body, headers=headers).status_code == 401
    assert client.put("/api/vocabulary/private", json={"status": "UNKNOWN"}, headers=headers).status_code == 401
    for path in ("/api/documents", "/api/documents/upload"):
        assert client.post(path, files={"file": ("test.png", b"png", "image/png")}, headers=headers).status_code == 401


def test_two_guests_cannot_read_or_mutate_each_others_resources(public_app):
    client, _, _, _ = public_app
    first, one = guest(client)
    second, two = guest(client)
    doc_one, doc_two = demo(client, one), demo(client, two)
    assert doc_one["id"] != doc_two["id"]
    assert [doc["id"] for doc in client.get("/api/documents", headers=one).json()] == [doc_one["id"]]
    assert [doc["id"] for doc in client.get("/api/documents", headers=two).json()] == [doc_two["id"]]
    assert demo(client, one)["id"] == doc_one["id"]
    for own, other, headers in ((doc_one, doc_two, one), (doc_two, doc_one, two)):
        assert client.get(f"/api/documents/{own['id']}", headers=headers).status_code == 200
        image = client.get(own["image_url"], headers=headers)
        assert image.status_code == 200 and image.headers["content-type"] == "image/png"
        assert "no-store" in image.headers["cache-control"]
        assert client.get(f"/api/documents/{other['id']}", headers=headers).status_code == 404
        assert client.get(other["image_url"], headers=headers).status_code == 404
        for path in ("/api/words/analyze", "/api/words/recommend"):
            assert client.post(path, headers=headers,
                               json={"occurrence_id": other["words"][0]["id"]}).status_code == 404
        assert client.put("/api/vocabulary/model", headers=headers,
                          json={"status": "KNOWN", "document_id": other["id"]}).status_code == 404

    marked = client.put("/api/vocabulary/model", headers=one,
                        json={"status": "UNKNOWN", "document_id": doc_one["id"]})
    assert marked.status_code == 200
    assert marked.json()["user_id"] == first["user_id"]
    words_one = client.get(f"/api/documents/{doc_one['id']}", headers=one).json()["words"]
    words_two = client.get(f"/api/documents/{doc_two['id']}", headers=two).json()["words"]
    assert all(word["status"] == "UNKNOWN" for word in words_one if word["lemma"] == "model")
    assert all(word["status"] is None for word in words_two if word["lemma"] == "model")
    history_one = client.get("/api/vocabulary/model/history", headers=one).json()
    history_two = client.get("/api/vocabulary/model/history", headers=two).json()
    assert len(history_one["events"]) == 1 and history_two["events"] == []
    assert client.get("/api/events", headers=two).json() == []
    assert all(event["user_id"] == first["user_id"] for event in client.get("/api/events", headers=one).json())
    for headers, unknown in ((one, 1), (two, 0)):
        summary = client.get("/api/dashboard", headers=headers).json()["summary"]
        assert summary["document_count"] == 1 and summary["unknown"] == unknown

    occurrence = next(word for word in doc_one["words"] if word["word"] == "Generalization")
    analyzed = client.post("/api/words/analyze", headers=one, json={"occurrence_id": occurrence["id"]})
    assert analyzed.status_code == 200
    response = client.post("/api/words/recommend", headers=one, json={"occurrence_id": occurrence["id"]})
    assert response.status_code == 200, response.text
    batch = response.json()
    payload = feedback_payload(batch["items"][0]["word"])
    assert client.post(f"/api/recommendations/{batch['batch_id']}/feedback", headers=two,
                       json=payload).status_code == 404
    assert client.post(f"/api/recommendations/{batch['batch_id']}/feedback", headers=one,
                       json=payload).status_code == 200
    assert client.get("/api/events", headers=two).json() == []
    assert client.get("/api/relations", headers=two).json()["total"] == 0
    assert client.get("/api/relations", headers=one).json()["total"] == 1
    assert all(row["user_id"] == second["user_id"] for row in client.get("/api/vocabulary", headers=two).json())


def test_upload_uses_guest_identity_and_document_quota(public_app, monkeypatch):
    client, factory, settings, _ = public_app
    first, one = guest(client)
    _, two = guest(client)
    settings.public_max_documents_per_user = 1
    image = BytesIO()
    Image.new("RGB", (120, 60), "white").save(image, "PNG")

    class FakeOCR:
        def recognize(self, path):
            return OCRResult("model", 120, 60, [OCRWord("model", [0, 0, 40, 20], 0, 1, "ocr")])

    monkeypatch.setattr("backend.services.documents.get_ocr_service", lambda: FakeOCR())
    response = client.post("/api/documents/upload", headers=one,
                           files={"file": ("image.png", image.getvalue(), "image/png")})
    assert response.status_code == 201, response.text
    doc = response.json()
    with factory() as session:
        assert session.get(Document, doc["id"]).user_id == first["user_id"]
    assert client.get(doc["image_url"], headers=two).status_code == 404
    assert client.post("/api/documents/demo", headers=one).status_code == 409
    assert client.post("/api/documents", headers=one,
                       files={"file": ("image.png", image.getvalue(), "image/png")}).status_code == 409
    second_demo = demo(client, two)
    assert demo(client, two)["id"] == second_demo["id"]
    assert len(client.get("/api/documents", headers=one).json()) == 1


def test_local_mode_retains_user_one_without_creating_guest(public_app, monkeypatch):
    client, factory, settings, legacy_id = public_app
    settings.app_mode = "local"
    assert client.get("/api/health").json()["auth_mode"] == "local"
    assert [doc["id"] for doc in client.get("/api/documents").json()] == [legacy_id]
    assert client.get("/api/vocabulary").json()[0]["user_id"] == 1
    assert client.post("/api/session").status_code == 404
    with factory() as session:
        assert list(session.scalars(select(GuestCredential))) == []


@pytest.mark.parametrize("origins", ["", "  , ", "*", "https://reader.github.io,*", "https://*.example.com"])
def test_public_mode_rejects_empty_or_wildcard_cors(origins):
    with pytest.raises(ValidationError, match="explicit CORS_ORIGINS"):
        Settings(app_mode="public", cors_origins=origins, _env_file=None)


def test_cors_allows_bearer_header_and_exposes_retry_after():
    cors = next(middleware for middleware in app.user_middleware if middleware.cls.__name__ == "CORSMiddleware")
    assert "Authorization" in cors.kwargs["allow_headers"]
    assert "Retry-After" in cors.kwargs["expose_headers"]
