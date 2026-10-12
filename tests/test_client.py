import asyncio
import time
import uuid

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
    EInvoiceOptions,
    EInvoiceProfile,
    ImageOptions,
    ImageViewport,
    DeliveryOptions,
)
from pdfik.models import JobAuthOptions

SAMPLE_CII_XML = '<?xml version="1.0" encoding="UTF-8"?><rsm:CrossIndustryInvoice/>'

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
def test_einvoice_to_pdf_request():
    route = respx.post("https://api.pdfik.net/einvoice-to-pdf").mock(
        return_value=httpx.Response(
            202,
            json={"job_id": "job-einv", "status": "queued", "detail": "Job successfully queued"}
        )
    )

    client = PdfikClient(api_key="sk_test_123")
    res = client.einvoice_to_pdf(
        SAMPLE_CII_XML,
        template_id="tpl-1",
        webhook_url="https://webhook.com",
        idempotency_key="idem-1",
    )

    assert res.job_id == "job-einv"
    assert res.status == "queued"
    assert route.called
    request = route.calls[0].request
    assert request.headers["X-API-Key"] == "sk_test_123"
    assert request.headers["Idempotency-Key"] == "idem-1"

    import json
    body = json.loads(request.read())
    assert body["xml"] == SAMPLE_CII_XML
    assert body["profile"] == "en16931"  # default
    assert body["template_id"] == "tpl-1"
    assert body["webhook_url"] == "https://webhook.com"
    assert "template" not in body
    assert "test" not in body


@respx.mock
def test_einvoice_to_pdf_inline_template_and_test_mode():
    route = respx.post("https://api.pdfik.net/einvoice-to-pdf").mock(
        return_value=httpx.Response(
            202,
            json={"job_id": "job-einv-2", "status": "queued", "detail": "Job successfully queued"}
        )
    )

    client = PdfikClient(api_key="sk_test_123")
    res = client.einvoice_to_pdf(
        SAMPLE_CII_XML,
        profile=EInvoiceProfile.EXTENDED,
        template={"blocks": [{"type": "header"}]},
        test=True,
    )

    assert res.job_id == "job-einv-2"
    assert route.called

    import json
    body = json.loads(route.calls[0].request.read())
    assert body["profile"] == "extended"
    assert body["template"] == {"blocks": [{"type": "header"}]}
    assert body["test"] is True
    assert "template_id" not in body


@respx.mock
def test_url_to_pdf_with_einvoice_option():
    route = respx.post("https://api.pdfik.net/url-to-pdf").mock(
        return_value=httpx.Response(
            202,
            json={"job_id": "job-a", "status": "queued", "detail": "Job successfully queued"}
        )
    )

    client = PdfikClient(api_key="sk_test_123")
    client.url_to_pdf(
        "https://example.com/invoice/42",
        einvoice=EInvoiceOptions(xml=SAMPLE_CII_XML, profile="basic"),
    )

    assert route.called

    import json
    body = json.loads(route.calls[0].request.read())
    assert body["einvoice"]["format"] == "factur-x"
    assert body["einvoice"]["profile"] == "basic"
    assert body["einvoice"]["xml"] == SAMPLE_CII_XML


@respx.mock
def test_html_to_pdf_with_einvoice_option():
    route = respx.post("https://api.pdfik.net/html-to-pdf").mock(
        return_value=httpx.Response(
            202,
            json={"job_id": "job-b", "status": "queued", "detail": "Job successfully queued"}
        )
    )

    client = PdfikClient(api_key="sk_test_123")
    client.html_to_pdf(
        "<h1>Invoice 42</h1>",
        einvoice=EInvoiceOptions(xml=SAMPLE_CII_XML),
    )

    assert route.called

    import json
    body = json.loads(route.calls[0].request.read())
    assert body["einvoice"]["format"] == "factur-x"
    assert body["einvoice"]["profile"] == "en16931"  # default
    assert body["einvoice"]["xml"] == SAMPLE_CII_XML


@respx.mock
def test_markdown_to_pdf_request():
    route = respx.post("https://api.pdfik.net/markdown-to-pdf").mock(
        return_value=httpx.Response(
            202,
            json={"job_id": "job-md", "status": "queued", "detail": "Job successfully queued"}
        )
    )

    client = PdfikClient(api_key="sk_test_123")
    res = client.markdown_to_pdf(
        "# Invoice\n\nHello **world**",
        webhook_url="https://webhook.com",
        options=PdfOptions(format="A4", print_background=True),
        idempotency_key="md-1",
    )

    assert res.job_id == "job-md"
    assert res.status == "queued"
    assert route.called
    request = route.calls[0].request
    assert request.headers["X-API-Key"] == "sk_test_123"
    assert request.headers["Idempotency-Key"] == "md-1"

    import json
    body = json.loads(request.read())
    assert body == {
        "markdown": "# Invoice\n\nHello **world**",
        "webhook_url": "https://webhook.com",
        "options": {"format": "A4", "print_background": True},
    }


@respx.mock
def test_url_to_image_request():
    route = respx.post("https://api.pdfik.net/url-to-image").mock(
        return_value=httpx.Response(
            202,
            json={"job_id": "job-img", "status": "queued", "detail": "Job successfully queued"}
        )
    )

    client = PdfikClient(api_key="sk_test_123")
    res = client.url_to_image(
        "https://example.com",
        options=ImageOptions(
            format="jpeg",
            full_page=False,
            quality=80,
            viewport=ImageViewport(width=1280, height=720),
        ),
        auth=JobAuthOptions(type="bearer", value="tok-123"),
    )

    assert res.job_id == "job-img"
    assert res.status == "queued"
    assert route.called

    import json
    body = json.loads(route.calls[0].request.read())
    assert body == {
        "url": "https://example.com",
        "options": {
            "format": "jpeg",
            "full_page": False,
            "quality": 80,
            "viewport": {"width": 1280, "height": 720},
        },
        "auth": {"type": "bearer", "value": "tok-123"},
    }


@respx.mock
def test_url_to_image_default_options_send_png_and_viewport_only():
    route = respx.post("https://api.pdfik.net/url-to-image").mock(
        return_value=httpx.Response(
            202,
            json={"job_id": "job-img-d", "status": "queued", "detail": "Job successfully queued"}
        )
    )

    client = PdfikClient(api_key="sk_test_123")
    client.url_to_image("https://example.com", options=ImageOptions())

    import json
    body = json.loads(route.calls[0].request.read())
    # The dataclass defaults go on the wire (only None is dropped) and match
    # the server defaults exactly: visible area only (owner decision 2026-09-20).
    assert body["options"] == {"format": "png", "full_page": False}


@respx.mock
def test_html_to_image_request():
    route = respx.post("https://api.pdfik.net/html-to-image").mock(
        return_value=httpx.Response(
            202,
            json={"job_id": "job-img-h", "status": "queued", "detail": "Job successfully queued"}
        )
    )

    client = PdfikClient(api_key="sk_test_123")
    res = client.html_to_image(
        "<h1>Hello</h1>",
        options=ImageOptions(full_page=True),
        test=True,
    )

    assert res.job_id == "job-img-h"
    assert route.called

    import json
    body = json.loads(route.calls[0].request.read())
    assert body == {
        "html": "<h1>Hello</h1>",
        "options": {"format": "png", "full_page": True},
        "test": True,
    }


@respx.mock
def test_delivery_body_sends_explicit_presigned_put_mode():
    route = respx.post("https://api.pdfik.net/url-to-pdf").mock(
        return_value=httpx.Response(
            202,
            json={"job_id": "job-byob", "status": "queued", "detail": "Job successfully queued"}
        )
    )

    presigned = "https://bucket.s3.eu-central-1.amazonaws.com/report.pdf?X-Amz-Signature=abc"
    client = PdfikClient(api_key="sk_test_123")
    # mode is not passed by the caller: the model's default spells it out.
    client.url_to_pdf("https://example.com", delivery=DeliveryOptions(url=presigned))

    import json
    body = json.loads(route.calls[0].request.read())
    assert body == {
        "url": "https://example.com",
        "delivery": {"mode": "presigned_put", "url": presigned},
    }


@respx.mock
def test_delivery_accepted_by_every_job_creating_method():
    delivery = DeliveryOptions(url="https://bucket.example.com/out?sig=1")
    ok = httpx.Response(
        202,
        json={"job_id": "job-byob-2", "status": "queued", "detail": "Job successfully queued"}
    )
    routes = {
        "html-to-pdf": respx.post("https://api.pdfik.net/html-to-pdf").mock(return_value=ok),
        "markdown-to-pdf": respx.post("https://api.pdfik.net/markdown-to-pdf").mock(return_value=ok),
        "url-to-image": respx.post("https://api.pdfik.net/url-to-image").mock(return_value=ok),
        "html-to-image": respx.post("https://api.pdfik.net/html-to-image").mock(return_value=ok),
        "einvoice-to-pdf": respx.post("https://api.pdfik.net/einvoice-to-pdf").mock(return_value=ok),
    }

    client = PdfikClient(api_key="sk_test_123")
    client.html_to_pdf("<h1>Hi</h1>", delivery=delivery)
    client.markdown_to_pdf("# Hi", delivery=delivery)
    client.url_to_image("https://example.com", delivery=delivery)
    client.html_to_image("<h1>Hi</h1>", delivery=delivery)
    client.einvoice_to_pdf(SAMPLE_CII_XML, delivery=delivery)

    import json
    for name, route in routes.items():
        assert route.called, name
        body = json.loads(route.calls[0].request.read())
        assert body["delivery"] == {
            "mode": "presigned_put",
            "url": "https://bucket.example.com/out?sig=1",
        }, name


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
def test_retry_on_500(monkeypatch):
    get_route = respx.get("https://api.pdfik.net/jobs/job-123").mock(
        return_value=httpx.Response(502, text="Bad Gateway")
    )

    # Patch tenacity backoff wait to avoid delays in tests
    client = PdfikClient(api_key="sk_test_123")

    # We can modify the tenacity retry settings dynamically for testing or rely on mock patching.
    # To keep it simple, we patch wait_exponential in tenacity to be 0.
    # The retry object is shared by every client, so the patch must be undone:
    # a plain assignment switched the backoff off for all the tests after this one.
    from tenacity import wait_none
    monkeypatch.setattr(client._request.retry, "wait", wait_none())

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
async def test_async_einvoice_to_pdf():
    route = respx.post("https://api.pdfik.net/einvoice-to-pdf").mock(
        return_value=httpx.Response(
            202,
            json={"job_id": "job-einv-async", "status": "queued", "detail": "Job successfully queued"}
        )
    )

    async with AsyncPdfikClient(api_key="sk_test_123") as client:
        res = await client.einvoice_to_pdf(SAMPLE_CII_XML, profile="basicwl")

        assert res.job_id == "job-einv-async"
        assert res.status == "queued"
        assert route.called

        import json
        body = json.loads(route.calls[0].request.read())
        assert body["xml"] == SAMPLE_CII_XML
        assert body["profile"] == "basicwl"


@pytest.mark.asyncio
@respx.mock
async def test_async_markdown_to_pdf():
    route = respx.post("https://api.pdfik.net/markdown-to-pdf").mock(
        return_value=httpx.Response(
            202,
            json={"job_id": "job-md-async", "status": "queued", "detail": "Job successfully queued"}
        )
    )

    async with AsyncPdfikClient(api_key="sk_test_123") as client:
        res = await client.markdown_to_pdf("# Hi", test=True)

        assert res.job_id == "job-md-async"
        assert route.called

        import json
        body = json.loads(route.calls[0].request.read())
        assert body == {"markdown": "# Hi", "test": True}


@pytest.mark.asyncio
@respx.mock
async def test_async_url_to_image_with_delivery():
    route = respx.post("https://api.pdfik.net/url-to-image").mock(
        return_value=httpx.Response(
            202,
            json={"job_id": "job-img-async", "status": "queued", "detail": "Job successfully queued"}
        )
    )

    async with AsyncPdfikClient(api_key="sk_test_123") as client:
        res = await client.url_to_image(
            "https://example.com",
            options=ImageOptions(viewport=ImageViewport(width=800, height=600)),
            delivery=DeliveryOptions(url="https://bucket.example.com/shot.png?sig=1"),
        )

        assert res.job_id == "job-img-async"
        assert route.called

        import json
        body = json.loads(route.calls[0].request.read())
        assert body["options"]["viewport"] == {"width": 800, "height": 600}
        assert body["delivery"] == {
            "mode": "presigned_put",
            "url": "https://bucket.example.com/shot.png?sig=1",
        }


@pytest.mark.asyncio
@respx.mock
async def test_async_html_to_image():
    route = respx.post("https://api.pdfik.net/html-to-image").mock(
        return_value=httpx.Response(
            202,
            json={"job_id": "job-img-h-async", "status": "queued", "detail": "Job successfully queued"}
        )
    )

    async with AsyncPdfikClient(api_key="sk_test_123") as client:
        res = await client.html_to_image("<h1>Hello</h1>", options=ImageOptions(format="jpeg", quality=70))

        assert res.job_id == "job-img-h-async"
        assert route.called

        import json
        body = json.loads(route.calls[0].request.read())
        assert body["options"] == {"format": "jpeg", "full_page": False, "quality": 70}


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

def test_job_auth_options_is_importable_from_the_package_root():
    # `auth=` is a public parameter, so its type must come from where users import
    # everything else. The tests used to import it from pdfik.models and hid the gap.
    from pdfik import JobAuthOptions as RootJobAuthOptions  # noqa: F401


@respx.mock
def test_validation_error_detail_list_becomes_a_readable_message():
    # pdf-api answers request-validation errors (422) with `detail` as a LIST of
    # {loc, msg}; the message used to be that list's repr.
    respx.post("https://api.pdfik.net/url-to-image").mock(
        return_value=httpx.Response(422, json={
            "type": "https://docs.pdfik.net/error-codes#validation-error",
            "title": "Unprocessable Entity - Request Validation Error",
            "status": 422,
            "detail": [{"loc": ["body", "options", "viewport", "width"],
                        "msg": "Input should be less than or equal to 1920", "type": "less_than_equal"}],
        })
    )
    client = PdfikClient(api_key="sk_live_test")
    with pytest.raises(PdfikError) as exc:
        client.url_to_image("https://example.com")
    assert exc.value.status_code == 422
    assert exc.value.args[0] == "options.viewport.width: Input should be less than or equal to 1920"

@respx.mock
def test_wait_for_job_waits_out_a_429_instead_of_failing():
    # Free is 10 requests/min: a render longer than ~20 s used to end the wait
    # with a 429 once the request-level retries (3) were spent.
    throttled = httpx.Response(429, json={"error": "RATE_LIMIT_EXCEEDED", "detail": "Too many requests. Please try again later.", "retry_after_seconds": 0.01})
    get_route = respx.get("https://api.pdfik.net/jobs/job-123")
    get_route.side_effect = [throttled, throttled, throttled, httpx.Response(200, json={"status": "done"})]
    client = PdfikClient(api_key="sk_test_123")
    res = client.wait_for_job("job-123", timeout=60, poll_interval=0.01)
    assert res.status == "done"
    assert get_route.call_count == 4


@respx.mock
@pytest.mark.asyncio
async def test_async_wait_for_job_waits_out_a_429_instead_of_failing():
    throttled = httpx.Response(429, json={"error": "RATE_LIMIT_EXCEEDED", "detail": "Too many requests. Please try again later.", "retry_after_seconds": 0.01})
    get_route = respx.get("https://api.pdfik.net/jobs/job-123")
    get_route.side_effect = [throttled, throttled, throttled, httpx.Response(200, json={"status": "done"})]
    async with AsyncPdfikClient(api_key="sk_test_123") as client:
        res = await client.wait_for_job("job-123", timeout=60, poll_interval=0.01)
    assert res.status == "done"
    assert get_route.call_count == 4


# Throttle 429s (request rate, in-flight job cap) are waited out on every call, and
# a busy file on the download call; every other 429 is raised at once.

JOB_QUEUED = {"job_id": "job-123", "status": "queued", "detail": "Job successfully queued"}


def rate_limited(seconds):
    # What pdf-api's rate limiter answers: the wait in the body and in Retry-After.
    return httpx.Response(
        429,
        headers={"Retry-After": str(seconds)},
        json={"error": "RATE_LIMIT_EXCEEDED", "detail": "Too many requests. Please try again later.", "retry_after_seconds": seconds},
    )


def concurrency_limited():
    # The in-flight job cap states its wait in the Retry-After header only.
    return httpx.Response(
        429,
        headers={"Retry-After": "5"},
        json={
            "type": "https://docs.pdfik.net/error-codes#concurrency-limit",
            "title": "Too Many Requests - Concurrency Limit",
            "status": 429,
            "error": "CONCURRENCY_LIMIT_EXCEEDED",
            "detail": "You already have 2 jobs queued or rendering; plan 'free' allows 2 at a time. Poll your pending jobs and retry.",
            "plan": "free",
            "limit": 2,
            "in_flight": 2,
            "upgrade_url": "https://pdfik.net/dashboard/billing",
        },
    )


def byte_quota_exceeded():
    return httpx.Response(
        429,
        json={
            "type": "https://docs.pdfik.net/error-codes#quota-bytes-exceeded",
            "title": "Too Many Requests - Byte Quota Exceeded",
            "status": 429,
            "error": "BYTE_QUOTA_EXCEEDED",
            "detail": "Monthly generated-data quota of 536870912 bytes for plan 'free' exceeded (536871000 bytes used this billing window).",
            "plan": "free",
            "limit": 536870912,
            "used": 536871000,
            "upgrade_url": "https://pdfik.net/dashboard/billing",
        },
    )


def capped_test_mode():
    return httpx.Response(
        429,
        headers={"Retry-After": "30"},
        json={
            "type": "https://docs.pdfik.net/error-codes#test-rate-limit-exceeded",
            "title": "Too Many Requests - Test Mode Rate Limit",
            "status": 429,
            "error": "TEST_RATE_LIMIT_EXCEEDED",
            "detail": "Test-mode submissions are free of quota but capped at 60 per minute per account. Retry after the window resets.",
            "limit": 60,
            "window": "minute",
        },
    )


def download_refused(slug, retry_after=None):
    # The download endpoint's 429s carry no "error" code, only the type URL.
    return httpx.Response(
        429,
        headers={"Retry-After": str(retry_after)} if retry_after is not None else None,
        json={
            "type": f"https://docs.pdfik.net/error-codes#{slug}",
            "title": "Too Many Requests",
            "status": 429,
            "detail": "This file cannot be downloaded right now.",
        },
    )


def download_busy(retry_after=10):
    # Another stream of the same file is still open; the API names 10 s.
    return download_refused("download-busy", retry_after)


@pytest.fixture
def sleeps(monkeypatch):
    """Every sleep a client asks for, in order. Nothing really sleeps."""
    recorded = []

    async def record(seconds):
        recorded.append(seconds)

    monkeypatch.setattr(time, "sleep", recorded.append)
    monkeypatch.setattr(asyncio, "sleep", record)
    # tenacity keeps its own reference to the sleep function it was built with.
    monkeypatch.setattr(PdfikClient._request.retry, "sleep", recorded.append)
    monkeypatch.setattr(AsyncPdfikClient._request.retry, "sleep", record)
    return recorded


@respx.mock
def test_download_waits_out_a_rate_limit_429(sleeps):
    route = respx.get("https://api.pdfik.net/jobs/job-123/download")
    route.side_effect = [rate_limited(7), rate_limited(2), httpx.Response(200, content=b"%PDF-1.7 sample")]

    client = PdfikClient(api_key="sk_test_123")
    assert client.download_pdf("job-123") == b"%PDF-1.7 sample"
    assert sleeps == [7, 2]
    assert route.call_count == 3


@respx.mock
def test_create_waits_out_a_rate_limit_429_and_resends_the_same_request(sleeps):
    route = respx.post("https://api.pdfik.net/url-to-pdf")
    route.side_effect = [rate_limited(4), httpx.Response(202, json=JOB_QUEUED)]

    client = PdfikClient(api_key="sk_test_123")
    res = client.url_to_pdf("https://example.com", idempotency_key="idem-1")

    assert res.job_id == "job-123"
    assert sleeps == [4]
    assert route.call_count == 2
    first, second = route.calls[0].request, route.calls[1].request
    assert second.read() == first.read()
    assert second.headers["Idempotency-Key"] == first.headers["Idempotency-Key"] == "idem-1"


@respx.mock
def test_create_waits_out_the_in_flight_job_cap(sleeps):
    route = respx.post("https://api.pdfik.net/html-to-pdf")
    route.side_effect = [concurrency_limited(), httpx.Response(202, json=JOB_QUEUED)]

    client = PdfikClient(api_key="sk_test_123")
    res = client.html_to_pdf("<h1>Hello</h1>")

    assert res.job_id == "job-123"
    assert sleeps == [5]
    assert route.call_count == 2


@respx.mock
def test_get_job_waits_out_a_rate_limit_429(sleeps):
    route = respx.get("https://api.pdfik.net/jobs/job-123")
    route.side_effect = [rate_limited(9), httpx.Response(200, json={"status": "rendering"})]

    client = PdfikClient(api_key="sk_test_123")
    assert client.get_job("job-123").status == "rendering"
    assert sleeps == [9]
    assert route.call_count == 2


@respx.mock
def test_throttle_that_never_clears_is_raised_after_five_waits(sleeps):
    route = respx.get("https://api.pdfik.net/jobs/job-123/download").mock(return_value=rate_limited(3))

    client = PdfikClient(api_key="sk_test_123")
    with pytest.raises(PdfikError) as exc_info:
        client.download_pdf("job-123")

    assert exc_info.value.status_code == 429
    assert exc_info.value.error_code == "RATE_LIMIT_EXCEEDED"
    assert sleeps == [3, 3, 3, 3, 3]
    assert route.call_count == 6


@respx.mock
def test_throttle_wait_is_capped_and_has_a_fallback(sleeps):
    route = respx.post("https://api.pdfik.net/url-to-pdf")
    route.side_effect = [
        rate_limited(3600),
        httpx.Response(429, json={"error": "RATE_LIMIT_EXCEEDED", "detail": "Too many requests. Please try again later."}),
        httpx.Response(202, json=JOB_QUEUED),
    ]

    client = PdfikClient(api_key="sk_test_123")
    client.url_to_pdf("https://example.com")

    # 60 s is the most the server can make a caller wait; 10 s when it names no time.
    assert sleeps == [60, 10]


@pytest.mark.parametrize("refusal", [byte_quota_exceeded, capped_test_mode])
@respx.mock
def test_quota_429_on_create_is_raised_at_once(sleeps, refusal):
    route = respx.post("https://api.pdfik.net/url-to-pdf").mock(return_value=refusal())

    client = PdfikClient(api_key="sk_test_123")
    with pytest.raises(PdfikError) as exc_info:
        client.url_to_pdf("https://example.com")

    assert exc_info.value.status_code == 429
    assert exc_info.value.error_code == refusal().json()["error"]
    assert sleeps == []
    assert route.call_count == 1


@respx.mock
def test_exhausted_download_attempts_are_raised_at_once(sleeps):
    # Also a 429 with a Retry-After, but waiting cannot bring the attempts back.
    route = respx.get("https://api.pdfik.net/jobs/job-123/download").mock(
        return_value=download_refused("download-attempts-exhausted", 3600)
    )

    client = PdfikClient(api_key="sk_test_123")
    with pytest.raises(PdfikError) as exc_info:
        client.download_pdf("job-123")

    assert exc_info.value.status_code == 429
    assert sleeps == []
    assert route.call_count == 1


@respx.mock
def test_download_waits_out_a_busy_file(sleeps):
    route = respx.get("https://api.pdfik.net/jobs/job-123/download")
    route.side_effect = [download_busy(), httpx.Response(200, content=b"%PDF-1.7 sample")]

    client = PdfikClient(api_key="sk_test_123")
    assert client.download_pdf("job-123") == b"%PDF-1.7 sample"
    assert sleeps == [10]
    assert route.call_count == 2


@pytest.mark.parametrize(
    "body",
    [
        {"type": "https://docs.pdfik.net/error-codes#download-busy", "status": 429, "detail": "Another download of this file is already in progress."},
        {"error": "DOWNLOAD_BUSY", "status": 429, "detail": "Another download of this file is already in progress."},
    ],
    ids=["type only", "error code only"],
)
@respx.mock
def test_busy_file_is_recognised_by_the_type_or_by_the_error_code(sleeps, body):
    route = respx.get("https://api.pdfik.net/jobs/job-123/download")
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "10"}, json=body),
        httpx.Response(200, content=b"%PDF-1.7 sample"),
    ]

    client = PdfikClient(api_key="sk_test_123")
    assert client.download_pdf("job-123") == b"%PDF-1.7 sample"
    assert sleeps == [10]
    assert route.call_count == 2


@respx.mock
def test_busy_file_that_never_clears_is_raised_after_five_waits(sleeps):
    route = respx.get("https://api.pdfik.net/jobs/job-123/download").mock(return_value=download_busy())

    client = PdfikClient(api_key="sk_test_123")
    with pytest.raises(PdfikError) as exc_info:
        client.download_pdf("job-123")

    assert exc_info.value.status_code == 429
    assert exc_info.value.response_body and "#download-busy" in exc_info.value.response_body
    assert sleeps == [10, 10, 10, 10, 10]
    assert route.call_count == 6


@respx.mock
def test_busy_file_and_rate_limit_share_the_five_waits_of_one_download(sleeps):
    route = respx.get("https://api.pdfik.net/jobs/job-123/download")
    route.side_effect = [rate_limited(7), download_busy(), rate_limited(2), download_busy(), rate_limited(4), download_busy()]

    client = PdfikClient(api_key="sk_test_123")
    with pytest.raises(PdfikError) as exc_info:
        client.download_pdf("job-123")

    assert exc_info.value.status_code == 429
    assert sleeps == [7, 10, 2, 10, 4]
    assert route.call_count == 6


@respx.mock
def test_busy_file_wait_is_capped_and_has_a_fallback(sleeps):
    route = respx.get("https://api.pdfik.net/jobs/job-123/download")
    route.side_effect = [download_busy(3600), download_busy(None), httpx.Response(200, content=b"%PDF-1.7 sample")]

    client = PdfikClient(api_key="sk_test_123")
    assert client.download_pdf("job-123") == b"%PDF-1.7 sample"
    assert sleeps == [60, 10]


@respx.mock
def test_busy_file_is_a_throttle_on_the_download_call_only(sleeps):
    route = respx.get("https://api.pdfik.net/jobs/job-123").mock(return_value=download_busy())

    client = PdfikClient(api_key="sk_test_123")
    with pytest.raises(PdfikError) as exc_info:
        client.get_job("job-123")

    assert exc_info.value.status_code == 429
    assert sleeps == []
    assert route.call_count == 1


@respx.mock
def test_500_then_200_succeeds_with_the_exponential_backoff(sleeps):
    route = respx.get("https://api.pdfik.net/jobs/job-123/download")
    route.side_effect = [httpx.Response(500, text="Internal Server Error"), httpx.Response(200, content=b"%PDF-1.7 sample")]

    client = PdfikClient(api_key="sk_test_123")
    assert client.download_pdf("job-123") == b"%PDF-1.7 sample"
    assert sleeps == [1]
    assert route.call_count == 2


@respx.mock
def test_throttle_does_not_count_as_a_failed_attempt(sleeps):
    # Three attempts are allowed for 5xx; a waited-out 429 is not one of them.
    route = respx.get("https://api.pdfik.net/jobs/job-123")
    route.side_effect = [
        rate_limited(6),
        httpx.Response(502, text="Bad Gateway"),
        httpx.Response(502, text="Bad Gateway"),
        httpx.Response(200, json={"status": "done"}),
    ]

    client = PdfikClient(api_key="sk_test_123")
    assert client.get_job("job-123").status == "done"
    assert sleeps == [6, 1, 2]
    assert route.call_count == 4


@respx.mock
def test_wait_for_job_waits_once_per_429(sleeps):
    get_route = respx.get("https://api.pdfik.net/jobs/job-123")
    get_route.side_effect = [rate_limited(7), rate_limited(4), httpx.Response(200, json={"status": "done"})]

    client = PdfikClient(api_key="sk_test_123")
    res = client.wait_for_job("job-123", timeout=60)

    assert res.status == "done"
    assert sleeps == [7, 4]
    assert get_route.call_count == 3


@respx.mock
def test_wait_for_job_checks_its_timeout_before_waiting_out_a_429(sleeps):
    # The request path must not wait on behalf of the poll: it knows no timeout.
    get_route = respx.get("https://api.pdfik.net/jobs/job-123").mock(return_value=rate_limited(7))

    client = PdfikClient(api_key="sk_test_123")
    with pytest.raises(PdfikError) as exc_info:
        client.wait_for_job("job-123", timeout=0)

    assert exc_info.value.status_code == 408
    assert sleeps == []
    assert get_route.call_count == 1


# Every create call carries an Idempotency-Key: the caller's, or one generated per
# call and repeated on every resend, so a retried create cannot make a second job.

# The same lambdas serve both clients: the async one hands back a coroutine.
CREATE_CALLS = [
    ("/url-to-pdf", lambda client, **kw: client.url_to_pdf("https://example.com", **kw)),
    ("/html-to-pdf", lambda client, **kw: client.html_to_pdf("<h1>Hello</h1>", **kw)),
    ("/markdown-to-pdf", lambda client, **kw: client.markdown_to_pdf("# Hello", **kw)),
    ("/url-to-image", lambda client, **kw: client.url_to_image("https://example.com", **kw)),
    ("/html-to-image", lambda client, **kw: client.html_to_image("<h1>Hello</h1>", **kw)),
    ("/einvoice-to-pdf", lambda client, **kw: client.einvoice_to_pdf(SAMPLE_CII_XML, **kw)),
]
CREATE_IDS = [path for path, _ in CREATE_CALLS]


def idempotency_keys(route):
    return [call.request.headers.get("Idempotency-Key") for call in route.calls]


def is_uuid4(value):
    try:
        parsed = uuid.UUID(value)
    except (TypeError, ValueError):
        return False
    return parsed.version == 4 and str(parsed) == value


@pytest.mark.parametrize("path, create", CREATE_CALLS, ids=CREATE_IDS)
@respx.mock
def test_create_resent_after_a_500_carries_the_same_generated_idempotency_key(sleeps, path, create):
    route = respx.post("https://api.pdfik.net" + path)
    route.side_effect = [httpx.Response(500, text="Internal Server Error"), httpx.Response(202, json=JOB_QUEUED)]

    client = PdfikClient(api_key="sk_test_123")
    assert create(client).job_id == "job-123"

    first, second = idempotency_keys(route)
    assert is_uuid4(first)
    assert second == first


@pytest.mark.parametrize("path, create", CREATE_CALLS, ids=CREATE_IDS)
@respx.mock
def test_create_resent_after_a_throttle_wait_carries_the_same_generated_idempotency_key(sleeps, path, create):
    route = respx.post("https://api.pdfik.net" + path)
    route.side_effect = [rate_limited(4), concurrency_limited(), httpx.Response(202, json=JOB_QUEUED)]

    client = PdfikClient(api_key="sk_test_123")
    assert create(client).job_id == "job-123"

    assert sleeps == [4, 5]
    first, second, third = idempotency_keys(route)
    assert is_uuid4(first)
    assert second == third == first


@pytest.mark.parametrize("path, create", CREATE_CALLS, ids=CREATE_IDS)
@respx.mock
def test_each_create_call_generates_its_own_idempotency_key(path, create):
    route = respx.post("https://api.pdfik.net" + path).mock(return_value=httpx.Response(202, json=JOB_QUEUED))

    client = PdfikClient(api_key="sk_test_123")
    create(client)
    create(client)

    first, second = idempotency_keys(route)
    assert is_uuid4(first) and is_uuid4(second)
    assert first != second


@pytest.mark.parametrize("path, create", CREATE_CALLS, ids=CREATE_IDS)
@respx.mock
def test_caller_idempotency_key_is_sent_unchanged_on_every_attempt(sleeps, path, create):
    route = respx.post("https://api.pdfik.net" + path)
    route.side_effect = [
        httpx.Response(500, text="Internal Server Error"),
        rate_limited(4),
        httpx.Response(202, json=JOB_QUEUED),
    ]

    client = PdfikClient(api_key="sk_test_123")
    assert create(client, idempotency_key="order-42:invoice_7.v1").job_id == "job-123"

    assert sleeps == [1, 4]
    assert idempotency_keys(route) == ["order-42:invoice_7.v1"] * 3


@respx.mock
def test_status_and_download_requests_carry_no_idempotency_key():
    status = respx.get("https://api.pdfik.net/jobs/job-123").mock(
        return_value=httpx.Response(200, json={"status": "done"})
    )
    download = respx.get("https://api.pdfik.net/jobs/job-123/download").mock(
        return_value=httpx.Response(200, content=b"%PDF-1.7 sample")
    )

    client = PdfikClient(api_key="sk_test_123")
    client.get_job("job-123")
    client.wait_for_job("job-123")
    client.download_pdf("job-123")

    assert idempotency_keys(status) == [None, None]
    assert idempotency_keys(download) == [None]


@pytest.mark.asyncio
@respx.mock
async def test_async_download_waits_out_a_rate_limit_429(sleeps):
    route = respx.get("https://api.pdfik.net/jobs/job-123/download")
    route.side_effect = [rate_limited(7), rate_limited(2), httpx.Response(200, content=b"%PDF-1.7 sample")]

    async with AsyncPdfikClient(api_key="sk_test_123") as client:
        assert await client.download_pdf("job-123") == b"%PDF-1.7 sample"
    assert sleeps == [7, 2]
    assert route.call_count == 3


@pytest.mark.asyncio
@respx.mock
async def test_async_create_waits_out_a_rate_limit_429_and_the_in_flight_job_cap(sleeps):
    route = respx.post("https://api.pdfik.net/url-to-pdf")
    route.side_effect = [rate_limited(4), concurrency_limited(), httpx.Response(202, json=JOB_QUEUED)]

    async with AsyncPdfikClient(api_key="sk_test_123") as client:
        res = await client.url_to_pdf("https://example.com")

    assert res.job_id == "job-123"
    assert sleeps == [4, 5]
    assert route.call_count == 3


@pytest.mark.asyncio
@respx.mock
async def test_async_throttle_that_never_clears_is_raised_after_five_waits(sleeps):
    route = respx.post("https://api.pdfik.net/url-to-pdf").mock(return_value=rate_limited(3))

    async with AsyncPdfikClient(api_key="sk_test_123") as client:
        with pytest.raises(PdfikError) as exc_info:
            await client.url_to_pdf("https://example.com")

    assert exc_info.value.status_code == 429
    assert exc_info.value.error_code == "RATE_LIMIT_EXCEEDED"
    assert sleeps == [3, 3, 3, 3, 3]
    assert route.call_count == 6


@pytest.mark.asyncio
@respx.mock
async def test_async_quota_429_on_create_is_raised_at_once(sleeps):
    route = respx.post("https://api.pdfik.net/url-to-pdf").mock(return_value=byte_quota_exceeded())

    async with AsyncPdfikClient(api_key="sk_test_123") as client:
        with pytest.raises(PdfikError) as exc_info:
            await client.url_to_pdf("https://example.com")

    assert exc_info.value.status_code == 429
    assert exc_info.value.error_code == "BYTE_QUOTA_EXCEEDED"
    assert sleeps == []
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_async_500_then_200_succeeds_with_the_exponential_backoff(sleeps):
    route = respx.get("https://api.pdfik.net/jobs/job-123/download")
    route.side_effect = [httpx.Response(500, text="Internal Server Error"), httpx.Response(200, content=b"%PDF-1.7 sample")]

    async with AsyncPdfikClient(api_key="sk_test_123") as client:
        assert await client.download_pdf("job-123") == b"%PDF-1.7 sample"
    assert sleeps == [1]
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_async_wait_for_job_waits_once_per_429(sleeps):
    get_route = respx.get("https://api.pdfik.net/jobs/job-123")
    get_route.side_effect = [rate_limited(7), rate_limited(4), httpx.Response(200, json={"status": "done"})]

    async with AsyncPdfikClient(api_key="sk_test_123") as client:
        res = await client.wait_for_job("job-123", timeout=60)

    assert res.status == "done"
    assert sleeps == [7, 4]
    assert get_route.call_count == 3


@pytest.mark.asyncio
@respx.mock
async def test_async_download_waits_out_a_busy_file(sleeps):
    route = respx.get("https://api.pdfik.net/jobs/job-123/download")
    route.side_effect = [download_busy(), httpx.Response(200, content=b"%PDF-1.7 sample")]

    async with AsyncPdfikClient(api_key="sk_test_123") as client:
        assert await client.download_pdf("job-123") == b"%PDF-1.7 sample"
    assert sleeps == [10]
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_async_busy_file_is_recognised_by_the_error_code_alone(sleeps):
    route = respx.get("https://api.pdfik.net/jobs/job-123/download")
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "10"}, json={"error": "DOWNLOAD_BUSY", "status": 429}),
        httpx.Response(200, content=b"%PDF-1.7 sample"),
    ]

    async with AsyncPdfikClient(api_key="sk_test_123") as client:
        assert await client.download_pdf("job-123") == b"%PDF-1.7 sample"
    assert sleeps == [10]
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_async_busy_file_that_never_clears_is_raised_after_five_waits(sleeps):
    route = respx.get("https://api.pdfik.net/jobs/job-123/download").mock(return_value=download_busy())

    async with AsyncPdfikClient(api_key="sk_test_123") as client:
        with pytest.raises(PdfikError) as exc_info:
            await client.download_pdf("job-123")

    assert exc_info.value.status_code == 429
    assert sleeps == [10, 10, 10, 10, 10]
    assert route.call_count == 6


@pytest.mark.asyncio
@respx.mock
async def test_async_exhausted_download_attempts_are_raised_at_once(sleeps):
    route = respx.get("https://api.pdfik.net/jobs/job-123/download").mock(
        return_value=download_refused("download-attempts-exhausted", 3600)
    )

    async with AsyncPdfikClient(api_key="sk_test_123") as client:
        with pytest.raises(PdfikError) as exc_info:
            await client.download_pdf("job-123")

    assert exc_info.value.status_code == 429
    assert sleeps == []
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_async_busy_file_is_a_throttle_on_the_download_call_only(sleeps):
    route = respx.get("https://api.pdfik.net/jobs/job-123").mock(return_value=download_busy())

    async with AsyncPdfikClient(api_key="sk_test_123") as client:
        with pytest.raises(PdfikError) as exc_info:
            await client.get_job("job-123")

    assert exc_info.value.status_code == 429
    assert sleeps == []
    assert route.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("path, create", CREATE_CALLS, ids=CREATE_IDS)
@respx.mock
async def test_async_create_keeps_one_generated_idempotency_key_across_a_500_and_a_throttle_wait(sleeps, path, create):
    route = respx.post("https://api.pdfik.net" + path)
    route.side_effect = [
        httpx.Response(500, text="Internal Server Error"),
        rate_limited(4),
        httpx.Response(202, json=JOB_QUEUED),
    ]

    async with AsyncPdfikClient(api_key="sk_test_123") as client:
        assert (await create(client)).job_id == "job-123"

    assert sleeps == [1, 4]
    first, second, third = idempotency_keys(route)
    assert is_uuid4(first)
    assert second == third == first


@pytest.mark.asyncio
@pytest.mark.parametrize("path, create", CREATE_CALLS, ids=CREATE_IDS)
@respx.mock
async def test_async_each_create_call_generates_its_own_idempotency_key(path, create):
    route = respx.post("https://api.pdfik.net" + path).mock(return_value=httpx.Response(202, json=JOB_QUEUED))

    async with AsyncPdfikClient(api_key="sk_test_123") as client:
        await create(client)
        await create(client)

    first, second = idempotency_keys(route)
    assert is_uuid4(first) and is_uuid4(second)
    assert first != second


@pytest.mark.asyncio
@pytest.mark.parametrize("path, create", CREATE_CALLS, ids=CREATE_IDS)
@respx.mock
async def test_async_caller_idempotency_key_is_sent_unchanged_on_every_attempt(sleeps, path, create):
    route = respx.post("https://api.pdfik.net" + path)
    route.side_effect = [
        httpx.Response(500, text="Internal Server Error"),
        rate_limited(4),
        httpx.Response(202, json=JOB_QUEUED),
    ]

    async with AsyncPdfikClient(api_key="sk_test_123") as client:
        assert (await create(client, idempotency_key="order-42:invoice_7.v1")).job_id == "job-123"

    assert sleeps == [1, 4]
    assert idempotency_keys(route) == ["order-42:invoice_7.v1"] * 3


@pytest.mark.asyncio
@respx.mock
async def test_async_status_and_download_requests_carry_no_idempotency_key():
    status = respx.get("https://api.pdfik.net/jobs/job-123").mock(
        return_value=httpx.Response(200, json={"status": "done"})
    )
    download = respx.get("https://api.pdfik.net/jobs/job-123/download").mock(
        return_value=httpx.Response(200, content=b"%PDF-1.7 sample")
    )

    async with AsyncPdfikClient(api_key="sk_test_123") as client:
        await client.get_job("job-123")
        await client.wait_for_job("job-123")
        await client.download_pdf("job-123")

    assert idempotency_keys(status) == [None, None]
    assert idempotency_keys(download) == [None]
