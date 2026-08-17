import pytest
import respx
import httpx
from pdfik import (
    PdfikClient,
    AsyncPdfikClient,
    PdfikError,
    PdfOptions,
    RenderOptions,
    MarginOptions,
)

# Sync Tests

@respx.mock
def test_url_to_pdf_request():
    route = respx.post("https://api.pdfik.net/url-to-pdf").mock(
        return_value=httpx.Response(
            202,
            json={"job_id": "job-123", "status": "queued", "detail": "Job successfully queued"}
        )
    )

    client = PdfikClient(api_key="sk_test_123")
    res = client.url_to_pdf(
        "https://example.com",
        webhook_url="https://webhook.com",
        options=PdfOptions(margin=MarginOptions(top="10px"))
    )

    assert res.job_id == "job-123"
    assert res.status == "queued"
    assert route.called
    request = route.calls[0].request
    assert request.headers["X-API-Key"] == "sk_test_123"
    assert "application/json" in request.headers["Content-Type"]
    
    import json
    body = json.loads(request.read())
    assert body["url"] == "https://example.com"
    assert body["webhook_url"] == "https://webhook.com"
    assert body["options"]["margin"]["top"] == "10px"


@respx.mock
def test_html_to_pdf_request():
    route = respx.post("https://api.pdfik.net/html-to-pdf").mock(
        return_value=httpx.Response(
            202,
            json={"job_id": "job-456", "status": "queued", "detail": "Job successfully queued"}
        )
    )

    client = PdfikClient(api_key="sk_test_123")
    res = client.html_to_pdf("<h1>Hello</h1>", options=PdfOptions(landscape=True))

    assert res.job_id == "job-456"
    assert res.status == "queued"
    assert route.called
    request = route.calls[0].request
    
    import json
    body = json.loads(request.read())
    assert body["html"] == "<h1>Hello</h1>"
    assert body["options"]["landscape"] is True


@respx.mock
def test_wait_for_job_polls():
    get_route = respx.get(url__startswith="https://api.pdfik.net/jobs/job-123")
    get_route.side_effect = [
        httpx.Response(200, json={"status": "queued"}),
        httpx.Response(200, json={"status": "rendering"}),
        httpx.Response(200, json={"status": "done", "pages_count": 3}),
    ]

    client = PdfikClient(api_key="sk_test_123")
    res = client.wait_for_job("job-123", poll_interval=0.01)
    
    assert res.status == "done"
    assert res.pages_count == 3
    assert get_route.call_count == 3


@respx.mock
def test_wait_for_job_raises_on_failed():
    get_route = respx.get("https://api.pdfik.net/jobs/job-123").mock(
        return_value=httpx.Response(200, json={"status": "failed", "error_code": "SSRF_BLOCKED"})
    )

    client = PdfikClient(api_key="sk_test_123")
    with pytest.raises(PdfikError) as exc_info:
        client.wait_for_job("job-123", poll_interval=0.01)
    
    assert exc_info.value.status_code == 400
    assert exc_info.value.error_code == "SSRF_BLOCKED"


@respx.mock
def test_wait_for_job_timeout():
    get_route = respx.get("https://api.pdfik.net/jobs/job-123").mock(
        return_value=httpx.Response(200, json={"status": "queued"})
    )

    client = PdfikClient(api_key="sk_test_123")
    with pytest.raises(PdfikError) as exc_info:
        client.wait_for_job("job-123", timeout=1, poll_interval=0.2)
    
    assert exc_info.value.status_code == 408
    assert "timed out" in str(exc_info.value)


@respx.mock
def test_retry_on_500():
    get_route = respx.get("https://api.pdfik.net/jobs/job-123").mock(
        return_value=httpx.Response(502, text="Bad Gateway")
    )

    # Patch tenacity backoff wait to avoid delays in tests
    client = PdfikClient(api_key="sk_test_123")
    
    # We can modify the tenacity retry settings dynamically for testing or rely on mock patching.
    # To keep it simple, we patch wait_exponential in tenacity to be 0
    from tenacity import wait_none
    client._request.retry.wait = wait_none()

    with pytest.raises(PdfikError) as exc_info:
        client.get_job("job-123")
        
    assert exc_info.value.status_code == 502
    assert get_route.call_count == 3


@respx.mock
def test_no_retry_on_401():
    get_route = respx.get("https://api.pdfik.net/jobs/job-123").mock(
        return_value=httpx.Response(401, json={"detail": "Invalid API key"})
    )

    client = PdfikClient(api_key="sk_test_123")
    with pytest.raises(PdfikError) as exc_info:
        client.get_job("job-123")
        
    assert exc_info.value.status_code == 401
    assert get_route.call_count == 1


# Async Tests

@pytest.mark.asyncio
@respx.mock
async def test_async_url_to_pdf():
    route = respx.post("https://api.pdfik.net/url-to-pdf").mock(
        return_value=httpx.Response(
            202,
            json={"job_id": "job-async", "status": "queued", "detail": "Job successfully queued"}
        )
    )

    async with AsyncPdfikClient(api_key="sk_test_123") as client:
        res = await client.url_to_pdf("https://example.com")
        
        assert res.job_id == "job-async"
        assert res.status == "queued"
        assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_async_wait_for_job():
    get_route = respx.get(url__startswith="https://api.pdfik.net/jobs/job-async")
    get_route.side_effect = [
        httpx.Response(200, json={"status": "rendering"}),
        httpx.Response(200, json={"status": "done", "pages_count": 2}),
    ]

    async with AsyncPdfikClient(api_key="sk_test_123") as client:
        res = await client.wait_for_job("job-async", poll_interval=0.01)
        
        assert res.status == "done"
        assert res.pages_count == 2
        assert get_route.call_count == 2


def test_verify_webhook_signature():
    import hmac
    import hashlib
    import time

    raw_body = b'{"job_id": "123", "event_type": "job.completed"}'
    timestamp = str(int(time.time()))
    secret = "whsec_testsecret"

    signed_payload = f"{timestamp}.{raw_body.decode('utf-8')}".encode("utf-8")
    expected_sig = hmac.new(
        secret.encode("utf-8"), signed_payload, hashlib.sha256
    ).hexdigest()

    headers = {
        "X-PDFik-Signature": f"t={timestamp},v1={expected_sig}",
        "X-PDFik-Timestamp": timestamp,
    }

    # Verify signature
    assert PdfikClient.verify_webhook_signature(raw_body, headers, secret) is True

    # Test invalid signature
    assert PdfikClient.verify_webhook_signature(raw_body, headers, "wrong_secret") is False

    # Test missing headers
    assert PdfikClient.verify_webhook_signature(raw_body, {}, secret) is False

