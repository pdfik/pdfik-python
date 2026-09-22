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
