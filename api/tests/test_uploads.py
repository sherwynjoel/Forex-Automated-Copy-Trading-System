# api/tests/test_uploads.py
"""Receipts and proofs: multipart upload with type sniffing and a size cap,
owner-only and admin reads with safe headers, the on-disk layout and the
hourly limit. Every test points the store at its own temp directory."""
import hashlib

import psycopg
import pytest
from psycopg.types.json import Jsonb

from portal_helpers import DEST_CRYPTO, csrf, member

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 64
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 64
PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n" + b"\x00" * 64
# org_client's admin, as a login_as() dict (it has no id; none is needed).
ADMIN = {"email": "admin@example.com", "password": "a-solid-password", "mpin": "123456"}


@pytest.fixture
def portal(org_client, make_user, login_as, db, tmp_path):
    """org_client's org with an investor member logged in, and the app's
    upload store swapped for a per-test directory."""
    from api.uploads import UploadStore
    client, org_id, seed = org_client
    root = tmp_path / "uploads"
    client.app.state.uploads = UploadStore(root)
    investor = make_user(email="inv@example.com", display_name="Inv One")
    member(db, org_id, investor["id"], "investor")
    login_as(client, investor)
    return client, org_id, investor, root


def upload(client, org_id, data=PNG, *, purpose="deposit_receipt", name="receipt.png",
           claimed="image/png"):
    return client.post(f"/api/orgs/{org_id}/investor/files",
                       data={"purpose": purpose},
                       files={"file": (name, data, claimed)},
                       headers=csrf(client))


# ---------- upload ----------

def test_a_png_receipt_is_stored_under_the_org(portal, db):
    client, org_id, investor, root = portal
    r = upload(client, org_id)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["purpose"] == "deposit_receipt" and body["content_type"] == "image/png"
    assert body["size_bytes"] == len(PNG) and body["created_at"]
    file_id = body["id"]
    with psycopg.connect(db, autocommit=True) as conn:
        key, sha, size, owner = conn.execute(
            "SELECT storage_key, sha256, size_bytes, user_id FROM files WHERE id = %s",
            (file_id,)).fetchone()
    assert key == f"{org_id}/{file_id}.png" and owner == investor["id"] and size == len(PNG)
    assert sha == hashlib.sha256(PNG).hexdigest()
    assert (root / str(org_id) / f"{file_id}.png").read_bytes() == PNG
    assert not list(root.rglob("*.part"))


@pytest.mark.parametrize("data, claimed, expected", [
    (JPEG, "text/plain", "image/jpeg"),
    (WEBP, "application/octet-stream", "image/webp"),
    (PDF, "image/png", "application/pdf"),
])
def test_the_type_comes_from_the_bytes_not_the_header(portal, data, claimed, expected):
    client, org_id, _, _ = portal
    r = upload(client, org_id, data, claimed=claimed, name="whatever.bin")
    assert r.status_code == 201 and r.json()["content_type"] == expected


def test_bytes_that_are_not_an_image_or_a_pdf_are_refused(portal, db):
    client, org_id, _, root = portal
    r = upload(client, org_id, b"<html>not a receipt</html>" * 4, claimed="image/png")
    assert r.status_code == 400 and r.json()["detail"] == "unsupported file type"
    assert not list(root.rglob("*"))
    with psycopg.connect(db, autocommit=True) as conn:
        (n,) = conn.execute("SELECT count(*) FROM files").fetchone()
    assert n == 0


def test_the_size_cap_is_five_megabytes(portal):
    from api.uploads import MAX_UPLOAD_BYTES
    client, org_id, _, _ = portal
    exact = PNG + b"\x00" * (MAX_UPLOAD_BYTES - len(PNG))
    assert upload(client, org_id, exact).status_code == 201
    r = upload(client, org_id, exact + b"\x00")
    assert r.status_code == 400 and r.json()["detail"] == "file too large (5 MB max)"


def _files_rows(db):
    with psycopg.connect(db, autocommit=True) as conn:
        (n,) = conn.execute("SELECT count(*) FROM files").fetchone()
    return n


def test_a_seven_mebibyte_upload_is_413_before_the_route_runs(portal, db):
    """The api caps the request body at 6 MiB for this route (body_limit.py)
    before FastAPI parses the multipart form: the route's own 5 MB check
    never runs (its 400 wording is absent), no row, no bytes on disk."""
    client, org_id, _, root = portal
    big = PNG + b"\x00" * (7 * 1024 * 1024)
    r = upload(client, org_id, big)
    assert r.status_code == 413 and r.json() == {"detail": "request body too large"}
    assert _files_rows(db) == 0 and not list(root.rglob("*"))


def test_a_chunked_seven_mebibyte_upload_is_counted_and_refused(portal, db):
    """Without a Content-Length the body is counted as it streams."""
    client, org_id, _, root = portal
    boundary = "mirrorfleet-test-boundary"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"purpose\"\r\n\r\n"
            f"deposit_receipt\r\n--{boundary}\r\nContent-Disposition: form-data; "
            f"name=\"file\"; filename=\"r.png\"\r\nContent-Type: image/png\r\n\r\n").encode()
    body += PNG + b"\x00" * (7 * 1024 * 1024) + f"\r\n--{boundary}--\r\n".encode()
    r = client.post(f"/api/orgs/{org_id}/investor/files", content=iter([body]),
                    headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                             **csrf(client)})
    assert r.status_code == 413 and r.json() == {"detail": "request body too large"}
    assert _files_rows(db) == 0 and not list(root.rglob("*"))


def test_a_five_mebibyte_upload_still_fits_under_the_body_cap(portal, db):
    from api.uploads import MAX_UPLOAD_BYTES
    client, org_id, _, _ = portal
    r = upload(client, org_id, PNG + b"\x00" * (MAX_UPLOAD_BYTES - len(PNG)))
    assert r.status_code == 201 and r.json()["size_bytes"] == 5 * 1024 * 1024
    assert _files_rows(db) == 1


def test_an_empty_file_is_refused(portal):
    client, org_id, _, _ = portal
    r = upload(client, org_id, b"")
    assert r.status_code == 400 and r.json()["detail"] == "file is empty"


@pytest.mark.parametrize("purpose", ["kyc_document", "kyc_photo"])
def test_the_kyc_purposes_are_accepted(portal, purpose):
    client, org_id, _, _ = portal
    r = upload(client, org_id, purpose=purpose)
    assert r.status_code == 201, r.text
    assert r.json()["purpose"] == purpose


@pytest.mark.parametrize("purpose", ["ticket_attachment", "avatar", "selfie", ""])
def test_only_the_accepted_purposes_are_stored(portal, purpose):
    client, org_id, _, _ = portal
    r = upload(client, org_id, purpose=purpose)
    assert r.status_code == 400 and r.json()["detail"] == "purpose is not accepted yet"


def test_thirty_uploads_an_hour_per_investor(portal):
    client, org_id, _, _ = portal
    for _ in range(30):
        assert upload(client, org_id).status_code == 201
    r = upload(client, org_id)
    assert r.status_code == 429 and r.json()["detail"] == "too many uploads; try again later"


# ---------- read ----------

def test_the_owner_reads_it_back_with_safe_headers(portal):
    client, org_id, _, _ = portal
    file_id = upload(client, org_id).json()["id"]
    r = client.get(f"/api/orgs/{org_id}/investor/files/{file_id}")
    assert r.status_code == 200 and r.content == PNG
    assert r.headers["content-type"] == "image/png"
    assert r.headers["content-disposition"] == f'inline; filename="file-{file_id}.png"'
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "private, max-age=0"


def test_a_pdf_is_served_as_an_attachment(portal):
    client, org_id, _, _ = portal
    file_id = upload(client, org_id, PDF, name="r.pdf", claimed="application/pdf").json()["id"]
    r = client.get(f"/api/orgs/{org_id}/investor/files/{file_id}")
    assert r.status_code == 200 and r.content == PDF
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"] == f'attachment; filename="file-{file_id}.pdf"'


def test_another_investor_cannot_read_it(portal, make_user, login_as, db):
    client, org_id, _, _ = portal
    file_id = upload(client, org_id).json()["id"]
    other = make_user(email="other@example.com")
    member(db, org_id, other["id"], "investor")
    login_as(client, other)
    r = client.get(f"/api/orgs/{org_id}/investor/files/{file_id}")
    assert r.status_code == 404 and r.json()["detail"] == "File not found"
    assert client.get(f"/api/orgs/{org_id}/investor/files/999").status_code == 404


def test_an_admin_reads_any_file_in_the_org(portal, make_user, login_as, db):
    client, org_id, _, _ = portal
    file_id = upload(client, org_id).json()["id"]
    # The investor may not use the admin route.
    assert client.get(f"/api/orgs/{org_id}/files/{file_id}").status_code == 403
    login_as(client, ADMIN)
    r = client.get(f"/api/orgs/{org_id}/files/{file_id}")
    assert r.status_code == 200 and r.content == PNG
    assert r.headers["content-disposition"] == f'inline; filename="file-{file_id}.png"'
    assert r.headers["x-content-type-options"] == "nosniff"
    assert client.get(f"/api/orgs/{org_id}/files/999").status_code == 404
    # A viewer is below admin.
    viewer = make_user(email="viewer@example.com")
    member(db, org_id, viewer["id"], "viewer")
    login_as(client, viewer)
    assert client.get(f"/api/orgs/{org_id}/files/{file_id}").status_code == 403


def test_a_file_from_another_org_is_not_found(portal, make_user, make_org, login_as):
    client, org_id, investor, _ = portal
    file_id = upload(client, org_id).json()["id"]
    boss = make_user(email="boss@example.com")
    other_org = make_org(name="Other", members=[(boss, "admin"), (investor, "investor")])
    assert client.get(f"/api/orgs/{other_org}/investor/files/{file_id}").status_code == 404
    login_as(client, boss)
    assert client.get(f"/api/orgs/{other_org}/files/{file_id}").status_code == 404


def test_a_row_whose_bytes_are_gone_is_not_found(portal):
    client, org_id, _, root = portal
    file_id = upload(client, org_id).json()["id"]
    (root / str(org_id) / f"{file_id}.png").unlink()
    r = client.get(f"/api/orgs/{org_id}/investor/files/{file_id}")
    assert r.status_code == 404 and r.json()["detail"] == "File not found"


def test_file_belongs_checks_org_owner_and_purpose(portal, make_user, db):
    from api.routes.portal_files import file_belongs
    client, org_id, investor, _ = portal
    file_id = upload(client, org_id).json()["id"]
    proof_id = upload(client, org_id, purpose="payout_proof").json()["id"]
    other = make_user(email="other@example.com")
    with psycopg.connect(db, autocommit=True) as conn:
        assert file_belongs(conn, org_id, investor["id"], file_id, "deposit_receipt")
        assert file_belongs(conn, org_id, investor["id"], proof_id, "payout_proof")
        assert file_belongs(conn, org_id, investor["id"], None, "deposit_receipt")
        assert not file_belongs(conn, org_id, investor["id"], file_id, "payout_proof")
        assert not file_belongs(conn, org_id, other["id"], file_id, "deposit_receipt")
        assert not file_belongs(conn, org_id + 1, investor["id"], file_id, "deposit_receipt")
        assert not file_belongs(conn, org_id, investor["id"], 999, "deposit_receipt")
        # Spec section 9: a file is referenced by at most one request row.
        # Once a deposit carries the receipt, or a payout destination the
        # proof, the same file cannot be attached to a second request.
        conn.execute(
            "INSERT INTO deposits (org_id, user_id, method_kind, method_label, amount, reference, "
            "receipt_file_id) VALUES (%s, %s, 'crypto', 'x', 1, 'r1', %s)",
            (org_id, investor["id"], file_id))
        assert not file_belongs(conn, org_id, investor["id"], file_id, "deposit_receipt")
        conn.execute(
            "INSERT INTO payout_destinations (org_id, user_id, kind, nickname, details, "
            "proof_file_id) VALUES (%s, %s, 'crypto', 'Main', %s, %s)",
            (org_id, investor["id"], Jsonb(DEST_CRYPTO), proof_id))
        assert not file_belongs(conn, org_id, investor["id"], proof_id, "payout_proof")


# ---------- config ----------

def test_upload_dir_defaults_when_unset_or_empty(portal, monkeypatch):
    """`portal` (via app_client) sets the rest of the env from_env needs.
    An `UPLOAD_DIR=` line in .env must mean "default", never the cwd."""
    from api.config import ApiConfig
    monkeypatch.delenv("UPLOAD_DIR", raising=False)
    assert ApiConfig.from_env().upload_dir == "./data/uploads"
    monkeypatch.setenv("UPLOAD_DIR", "")
    assert ApiConfig.from_env().upload_dir == "./data/uploads"
    monkeypatch.setenv("UPLOAD_DIR", "/data/uploads")
    assert ApiConfig.from_env().upload_dir == "/data/uploads"


# ---------- the sniffer and the store, without the app ----------

@pytest.mark.parametrize("head, expected", [
    (PNG[:16], ("image/png", "png")), (JPEG[:16], ("image/jpeg", "jpg")),
    (WEBP[:16], ("image/webp", "webp")), (PDF[:16], ("application/pdf", "pdf")),
    (b"GIF89a" + b"\x00" * 10, None), (b"RIFF\x00\x00\x00\x00WAVEfmt ", None),
    (b"", None), (b"\x89PN", None),
])
def test_detect_type_reads_magic_bytes(head, expected):
    from api.uploads import detect_type
    assert detect_type(head) == expected


def test_the_store_lays_files_out_by_org_and_refuses_escaping_keys(tmp_path):
    from api.uploads import UploadStore
    store = UploadStore(tmp_path / "up")
    assert (tmp_path / "up").is_dir()
    assert store.key(7, 12, "png") == "7/12.png"
    store.write("7/12.png", b"abc")
    assert store.read("7/12.png") == b"abc"
    assert store.path("7/12.png") == tmp_path / "up" / "7" / "12.png"
    assert not list((tmp_path / "up").rglob("*.part"))
    for bad in ["../x.png", "7/../../x.png", "/etc/passwd", "C:/Windows/x", "", "7\\..\\x.png"]:
        with pytest.raises(ValueError):
            store.path(bad)


# ---------- the preview's CSP ----------

def test_the_csp_lets_the_upload_preview_show_its_object_url(app_client):
    """FileInput previews a chosen image through URL.createObjectURL, a
    blob: URL; img-src must allow it or the thumbnail is blank in
    production. Nothing else about the policy loosens."""
    r = app_client.get("/api/me")
    directives = [d.strip() for d in r.headers["content-security-policy"].split(";")]
    assert "img-src 'self' data: blob:" in directives
    assert "default-src 'self'" in directives and "object-src 'none'" in directives


def test_file_belongs_refuses_a_file_already_in_a_kyc_slot(portal, db):
    from api.routes.portal_files import file_belongs
    from portal_helpers import kyc_profile, seed_file
    client, org_id, investor, _ = portal
    written = kyc_profile(db, org_id, investor["id"], status="draft")
    loose = seed_file(db, org_id, investor["id"], purpose="kyc_document")
    with psycopg.connect(db, autocommit=True) as conn:
        assert file_belongs(conn, org_id, investor["id"], loose, "kyc_document")
        for slot in ("id_front_file_id", "id_back_file_id", "address_proof_file_id"):
            assert not file_belongs(conn, org_id, investor["id"], written[slot], "kyc_document")
        assert not file_belongs(conn, org_id, investor["id"], written["photo_file_id"], "kyc_photo")
