import os
from pathlib import Path
import re

TEST_DB_PATH = Path(__file__).resolve().parents[1] / "test-mailbox-api.db"

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH.as_posix()}"
os.environ["ADMIN_API_KEY"] = "admin-test-token"
os.environ["APP_SECRET"] = "test-secret-with-enough-length"
os.environ["MAILU_SYNC_ENABLED"] = "false"
os.environ["BOX_REQUIRE_MAILU_ADMIN_SESSION"] = "false"
os.environ["CLOUDFLARE_SYNC_ENABLED"] = "false"
os.environ["MAIL_READ_REFRESH_ENABLED"] = "false"

from fastapi.testclient import TestClient
import pytest

from mailbox_api.db import Base, engine
from mailbox_api.extractors import extract_code
from mailbox_api.main import app


@pytest.fixture(autouse=True)
def reset_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    engine.dispose()
    TEST_DB_PATH.unlink(missing_ok=True)


def test_mailbox_token_isolation_and_admin_export():
    client = TestClient(app)
    admin_headers = {"X-Admin-Token": "admin-test-token"}

    assert client.get("/admin/domains").status_code == 403

    domain_response = client.post(
        "/admin/domains",
        headers=admin_headers,
        json={
            "domain": "i7wap.xyz",
            "mx_host": "mail.i7wap.xyz",
            "api_base_url": "https://mail.i7wap.xyz",
            "sync_with_mail_backend": False,
        },
    )
    assert domain_response.status_code == 200

    batch_response = client.post(
        "/admin/mailboxes/batch",
        headers=admin_headers,
        json={
            "domain": "i7wap.xyz",
            "count": 2,
            "prefix": "qa",
            "group_name": "test",
            "sync_with_mail_backend": False,
        },
    )
    assert batch_response.status_code == 200
    body = batch_response.json()
    assert body["count"] == 2
    assert "----https://mail.i7wap.xyz/mail/" in body["export"]

    first, second = body["items"]
    assert first["email"] != second["email"]
    assert first["token"] != second["token"]

    ingest_response = client.post(
        "/admin/messages",
        headers=admin_headers,
        json={
            "email": first["email"],
            "from_addr": "sender@example.net",
            "subject": "verification",
            "text_body": "Your verification code is 482915.",
        },
    )
    assert ingest_response.status_code == 200

    code_response = client.get(first["api_url"] + "?format=json")
    assert code_response.status_code == 200
    code_body = code_response.json()
    assert code_body["found"] is True
    assert code_body["code"] == "482915"

    wrong_token_response = client.get(
        "/api/mail/code",
        params={"email": first["email"], "token": second["token"]},
    )
    assert wrong_token_response.status_code == 403

    export_response = client.get("/admin/export", headers=admin_headers, params={"domain": "i7wap.xyz"})
    assert export_response.status_code == 200
    assert first["email"] in export_response.text
    assert second["email"] in export_response.text

    mailboxes_response = client.get("/admin/mailboxes", headers=admin_headers)
    first_id = next(item["id"] for item in mailboxes_response.json() if item["email"] == first["email"])
    disable_response = client.post(f"/admin/mailboxes/{first_id}/disable", headers=admin_headers)
    assert disable_response.status_code == 200

    disabled_read_response = client.get(first["api_url"])
    assert disabled_read_response.status_code == 404


def test_box_page_and_browser_download_without_manual_token():
    client = TestClient(app)
    admin_headers = {"X-Admin-Token": "admin-test-token"}

    domain_response = client.post(
        "/admin/domains",
        headers=admin_headers,
        json={
            "domain": "i7wap.xyz",
            "mx_host": "mail.i7wap.xyz",
            "api_base_url": "https://mail.i7wap.xyz",
            "sync_with_mail_backend": False,
        },
    )
    assert domain_response.status_code == 200

    page_response = client.get("/box")
    assert page_response.status_code == 200
    assert "批量生成邮箱" in page_response.text
    assert "复制全部" in page_response.text
    assert "同步域名" in page_response.text
    assert "每批数量" in page_response.text
    assert "X-Admin-Token" not in page_response.text
    assert 'id="prefix"' not in page_response.text

    generate_response = client.post(
        "/box/generate",
        json={
            "domain": "i7wap.xyz",
            "count": 2,
            "group_name": "browser-generate",
            "sync_with_mail_backend": False,
        },
    )
    assert generate_response.status_code == 200
    generate_body = generate_response.json()
    assert generate_body["count"] == 2
    for item in generate_body["items"]:
        local_part = item["email"].split("@", 1)[0]
        assert re.fullmatch(r"[a-z][a-z0-9]{11}", local_part)
        assert not local_part.startswith("box-")

    download_response = client.post(
        "/box/download",
        json={
            "domain": "i7wap.xyz",
            "count": 1,
            "group_name": "browser-download",
            "sync_with_mail_backend": False,
        },
    )
    assert download_response.status_code == 200
    assert download_response.headers["x-mailbox-count"] == "1"
    assert download_response.headers["content-disposition"].endswith('.txt"')
    assert "----https://mail.i7wap.xyz/mail/" in download_response.text


def test_box_domain_prepare_and_generate_auto_create_domain():
    client = TestClient(app)
    admin_headers = {"X-Admin-Token": "admin-test-token"}

    prepare_response = client.post(
        "/box/domain/prepare",
        json={
            "domain": "NewExample.COM.",
            "mx_host": "mail.newexample.com",
            "api_base_url": "https://mail.i7wap.xyz/",
            "sync_with_mail_backend": False,
        },
    )
    assert prepare_response.status_code == 200
    prepare_body = prepare_response.json()
    assert prepare_body["domain"] == "newexample.com"
    assert prepare_body["mx_host"] == "mail.newexample.com"
    assert prepare_body["api_base_url"] == "https://mail.i7wap.xyz"
    assert prepare_body["cloudflare_enabled"] is False
    assert prepare_body["ready"] is True
    assert {record["type"] for record in prepare_body["records"]} >= {"A", "MX", "TXT"}

    domains_response = client.get("/admin/domains", headers=admin_headers)
    assert domains_response.status_code == 200
    assert any(item["domain"] == "newexample.com" for item in domains_response.json())

    generate_response = client.post(
        "/box/generate",
        json={
            "domain": "newexample.com",
            "mx_host": "mail.newexample.com",
            "api_base_url": "https://mail.i7wap.xyz",
            "count": 1,
            "group_name": "auto-domain",
            "sync_with_mail_backend": False,
        },
    )
    assert generate_response.status_code == 200
    body = generate_response.json()
    assert body["count"] == 1
    assert body["items"][0]["email"].endswith("@newexample.com")
    assert "----https://mail.i7wap.xyz/mail/" in body["export"]


def test_read_code_filters_static_asset_links():
    client = TestClient(app)
    admin_headers = {"X-Admin-Token": "admin-test-token"}

    assert client.post(
        "/admin/domains",
        headers=admin_headers,
        json={
            "domain": "i7wap.xyz",
            "mx_host": "mail.i7wap.xyz",
            "api_base_url": "https://mail.i7wap.xyz",
            "sync_with_mail_backend": False,
        },
    ).status_code == 200

    batch_response = client.post(
        "/admin/mailboxes/batch",
        headers=admin_headers,
        json={
            "domain": "i7wap.xyz",
            "count": 1,
            "sync_with_mail_backend": False,
        },
    )
    item = batch_response.json()["items"][0]
    ingest_response = client.post(
        "/admin/messages",
        headers=admin_headers,
        json={
            "email": item["email"],
            "from_addr": "ChatGPT <noreply@tm.openai.com>",
            "subject": "你的 ChatGPT 临时验证码",
            "html_body": (
                "验证码 202123 "
                "https://cdn.openai.com/common/fonts/soehne/soehne-buch.woff2)"
            ),
        },
    )
    assert ingest_response.status_code == 200

    read_response = client.get(item["api_url"] + "?format=json")
    assert read_response.status_code == 200
    body = read_response.json()
    assert body["code"] == "202123"
    assert body["link"] is None


def test_mail_link_displays_full_message_and_keeps_json_format():
    client = TestClient(app)
    admin_headers = {"X-Admin-Token": "admin-test-token"}

    assert client.post(
        "/admin/domains",
        headers=admin_headers,
        json={
            "domain": "i7wap.xyz",
            "mx_host": "mail.i7wap.xyz",
            "api_base_url": "https://mail.i7wap.xyz",
            "sync_with_mail_backend": False,
        },
    ).status_code == 200

    batch_response = client.post(
        "/admin/mailboxes/batch",
        headers=admin_headers,
        json={
            "domain": "i7wap.xyz",
            "count": 1,
            "sync_with_mail_backend": False,
        },
    )
    item = batch_response.json()["items"][0]
    ingest_response = client.post(
        "/admin/messages",
        headers=admin_headers,
        json={
            "email": item["email"],
            "from_addr": "OpenAI <noreply@tm.openai.com>",
            "subject": "New sign-in to your OpenAI account",
            "text_body": "We noticed a new sign-in to your OpenAI account.",
            "html_body": "<html><body><h1>New sign-in</h1><p>We noticed a new sign-in.</p></body></html>",
        },
    )
    assert ingest_response.status_code == 200

    page_response = client.get(item["api_url"])
    assert page_response.status_code == 200
    assert page_response.headers["content-type"].startswith("text/html")
    assert "完整邮件" in page_response.text
    assert "New sign-in to your OpenAI account" in page_response.text
    assert "We noticed a new sign-in to your OpenAI account." in page_response.text
    assert "邮件内容" in page_response.text
    assert "纯文本内容" in page_response.text
    assert "HTML 源码" not in page_response.text
    assert "<div class=\"panel-title\">正文</div>" not in page_response.text

    json_response = client.get(item["api_url"] + "?format=json")
    assert json_response.status_code == 200
    assert json_response.json()["subject"] == "New sign-in to your OpenAI account"


def test_openai_tracking_open_link_is_not_verification_link():
    client = TestClient(app)
    admin_headers = {"X-Admin-Token": "admin-test-token"}

    assert client.post(
        "/admin/domains",
        headers=admin_headers,
        json={
            "domain": "i7wap.xyz",
            "mx_host": "mail.i7wap.xyz",
            "api_base_url": "https://mail.i7wap.xyz",
            "sync_with_mail_backend": False,
        },
    ).status_code == 200

    batch_response = client.post(
        "/admin/mailboxes/batch",
        headers=admin_headers,
        json={
            "domain": "i7wap.xyz",
            "count": 1,
            "sync_with_mail_backend": False,
        },
    )
    item = batch_response.json()["items"][0]
    assert client.post(
        "/admin/messages",
        headers=admin_headers,
        json={
            "email": item["email"],
            "from_addr": "OpenAI <noreply@tm.openai.com>",
            "subject": "New sign-in to your OpenAI account",
            "html_body": (
                "http://url3243.email.openai.com/wf/open?upn=tracking "
                "http://url3243.email.openai.com/ls/click?upn=tracking"
            ),
        },
    ).status_code == 200

    read_response = client.get(item["api_url"] + "?format=json")
    assert read_response.status_code == 200
    assert read_response.json()["link"] is None


def test_chatgpt_html_prefers_visible_six_digit_code_over_inline_style_numbers():
    body = """
    <html>
      <head><style>.x{color:#202123}</style></head>
      <body>
        <div style="padding:0 16px;color:#202123">ChatGPT</div>
        <p>输入此临时验证码以继续：</p>
        <div style="width:32px"></div>
        <div>859349</div>
        <a href="https://cdn.openai.com/common/fonts/soehne/soehne-buch.woff2">font</a>
      </body>
    </html>
    """
    text = "你的 ChatGPT 临时验证码\nChatGPT <noreply@tm.openai.com>\n" + body

    assert extract_code(text) == "859349"
