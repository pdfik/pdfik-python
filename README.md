# pdfik

> **Where this code lives:** extracted from the PDFik platform monorepo (last sync 2026-10-12).
> Releases to PyPI are cut from the monorepo; issues and PRs are welcome here.

Official Python SDK for [PDFik](https://pdfik.net) — the asynchronous URL/HTML-to-PDF API.

Submit a public URL or raw HTML, get a job id back, and receive an HMAC-signed webhook (or poll) when the PDF is ready. Rendering runs on sandboxed headless Chromium, so modern CSS, web fonts and JavaScript-heavy pages come out the way they look in the browser.

## Features

- **Type Safety**: Type hints for all options and response objects.
- **Sync & Async**: Exposes both `PdfikClient` and `AsyncPdfikClient` using the modern `httpx` engine.
- **Auto Retry**: `5xx` and connection errors are retried with exponential backoff (powered by `tenacity`). A rate-limit or concurrency-limit `429` is waited out for the time the API states (at most 60 s, up to five times in a row) on every call — create, status, download — not only while polling, and so is a busy file on `download_pdf`. Every other `429` (byte quota, test-mode caps, exhausted download attempts) is raised at once.
- **No duplicate jobs**: every create call carries an `Idempotency-Key` header — the `idempotency_key=` you pass, or a random UUID generated once per call and repeated on every resend — so a retried create returns the original job instead of making (and billing) a second one.
- **DX Affordances**: Built-in polling logic (`wait_for_job`) and file download helpers.

## Installation

```bash
pip install pdfik
```

## Quick Start

### Convert public URL to PDF (Sync)

```python
from pdfik import PdfikClient, PdfOptions, MarginOptions

# Initialize the client
client = PdfikClient(api_key="sk_live_...")

# 1. Submit the URL to be rendered
job = client.url_to_pdf(
    "https://example.com",
    options=PdfOptions(
        format="A4",
        landscape=False,
        print_background=True,
        margin=MarginOptions(top="10mm", bottom="10mm")
    )
)

print(f"Job created: {job.job_id}. Waiting for rendering...")

# 2. Poll until the job completes
result = client.wait_for_job(job.job_id)
print(f"Job finished! Pages: {result.pages_count}")

# 3. Download the PDF bytes
pdf_bytes = client.download_pdf(job.job_id)

with open("output.pdf", "wb") as f:
    f.write(pdf_bytes)
print("PDF saved to output.pdf")

# Close connection pool
client.close()
```

### Convert raw HTML to PDF (Async)

```python
import asyncio
from pdfik import AsyncPdfikClient, PdfOptions

async def main():
    async with AsyncPdfikClient(api_key="sk_live_...") as client:
        job = await client.html_to_pdf(
            "<h1>Hello World</h1><p>Sent from PDFik Python SDK</p>",
            options=PdfOptions(format="Letter")
        )
        
        result = await client.wait_for_job(job.job_id)
        print(f"Job finished! Status: {result.status}")
        
        pdf_bytes = await client.download_pdf(job.job_id)

asyncio.run(main())
```

### Get the direct download URL

Get the direct API download URL for the generated PDF (requires the "X-API-Key" header to download):

```python
file_info = client.get_file_url(job.job_id)
print(f"Direct Download URL: {file_info.download_url}")
```

### Test mode

Pass `test=True` to run a job through the full pipeline (statuses, webhook, download) without real rendering — the download returns a small sample PDF. Test jobs are free (no PDF or byte quota is debited) and rate-limited instead: 60 test calls per minute and 2,000 per day per account. Job status and webhook payloads always carry `test: true|false`, so you can safely exercise your integration end to end:

```python
job = client.url_to_pdf("https://example.com", test=True)
result = client.wait_for_job(job.job_id)
print(result.test)        # True
print(result.expires_at)  # e.g. "2026-08-05T12:00:00Z"

pdf_bytes = client.download_pdf(job.job_id)  # sample PDF
```

### Markdown to PDF

`markdown_to_pdf` converts Markdown (CommonMark + GFM tables and strikethrough) with a built-in print stylesheet. Raw HTML inside the Markdown is escaped, not rendered — use `html_to_pdf` for full HTML control. The same `options` (paper format, margins, header/footer, watermark, ...) and job flow apply. The Markdown may be up to 100,000 characters (longer input is rejected with `422`); Markdown whose converted document is too large for the processing queue is rejected with `413` ([payload-too-large-for-queue](https://docs.pdfik.net/error-codes#payload-too-large-for-queue)) and is not charged. Example:

```python
job = client.markdown_to_pdf(
    "# Report\n\n| Item | Price |\n| --- | --- |\n| Render | $0.01 |",
    options=PdfOptions(format="A4"),
)
result = client.wait_for_job(job.job_id)
pdf_bytes = client.download_pdf(job.job_id)
```

### Screenshots

`url_to_image` / `html_to_image` capture a page as a PNG (default) or JPEG instead of a PDF. `ImageOptions`: `format` (`"png"` | `"jpeg"`), `full_page` (default `False` - the visible area only; with `True` the height follows the real page and is clipped at 8,192 px, which is a ceiling and not a target, so a 2,000 px page still gives a 2,000 px image, and horizontal overflow beyond the viewport width is never captured), `quality` (1-100, JPEG only) and `viewport` (`ImageViewport(width, height)`; you pick the window size and we capture exactly that - nothing is scaled or fitted - `width` 320-1920, `height` 320-8192, defaults to 1024x768). Polling is identical to the PDF endpoints, and `download_pdf` returns the raw image bytes (`image/png` or `image/jpeg`, filename `{job_id}.png`/`.jpg`):

```python
from pdfik import ImageOptions, ImageViewport

job = client.url_to_image(
    "https://example.com",
    options=ImageOptions(
        format="jpeg",
        quality=80,
        full_page=True,
        viewport=ImageViewport(width=1280, height=720),
    ),
)
result = client.wait_for_job(job.job_id)
image_bytes = client.download_pdf(job.job_id)  # JPEG bytes

shot = client.html_to_image("<h1>Hello</h1>")  # 1024x768 PNG
```

`url_to_image` also accepts the Pro+ `auth` option (basic/bearer), exactly as `url_to_pdf`. Passing `quality` together with PNG is rejected with `422`.

### Deliver to your own bucket (BYOB)

Pass `delivery=` (every plan, on every job-creating method) and the output is uploaded straight to your own bucket via a presigned PUT URL; nothing is stored on PDFik's side. The URL must be `https` on the standard port 443 (any other port is rejected with `422`); presign it for at least 15 minutes and without a Content-Type condition:

```python
from pdfik import DeliveryOptions

job = client.url_to_pdf(
    "https://example.com",
    delivery=DeliveryOptions(url=presigned_put_url),  # mode "presigned_put" is the only mode
)
client.wait_for_job(job.job_id)  # "done" means the PUT to your bucket succeeded
```

Once a delivered job is done, the `job.finished` webhook reports the outcome only: it carries neither `file_url` nor `expires_at`, because PDFik keeps no copy and never records where the file went (the presigned URL is a credential, so it is used once and forgotten). You already know the destination — you signed it. `download_pdf` answers `404` ([output-delivered-externally](https://docs.pdfik.net/error-codes#output-delivered-externally)) — the file only exists in your bucket. `delivery` cannot be combined with `test=True` (`400`).

### Factur-X e-invoices

Turn a UN/CEFACT Cross-Industry-Invoice XML into a Factur-X / ZUGFeRD hybrid: a human-readable PDF/A-3 with the XML embedded as `factur-x.xml`. The XML is validated against the official XSD of the declared profile before any quota is spent; the output is validated with veraPDF and Mustangproject. Schema-valid does not mean tax-compliant — the invoice content is the caller's responsibility. Available on every plan;

```python
from pdfik import PdfikClient

client = PdfikClient(api_key="sk_live_...")

# The visual invoice is built from a block template (saved in the dashboard,
# passed inline as `template=`, or the built-in default when both are omitted).
job = client.einvoice_to_pdf(
    cii_xml,                      # your CII XML string, UTF-8, up to 1 MB
    profile="en16931",            # minimum | basicwl | basic | en16931 | extended
    template_id="c0ffee00-...",   # optional; mutually exclusive with template=
)
result = client.wait_for_job(job.job_id)
pdf_bytes = client.download_pdf(job.job_id)
```

Alternatively, render your own page as the visual half — pass `einvoice=` to `url_to_pdf` / `html_to_pdf` and the output is normalized to PDF/A-3 with the XML embedded (not combinable with `user_password` or `compression`, which break PDF/A):

```python
from pdfik import EInvoiceOptions

job = client.html_to_pdf(
    invoice_html,
    einvoice=EInvoiceOptions(xml=cii_xml, profile="en16931"),
)
```

Profiles `minimum` and `basicwl` carry accompanying data only and are **not** a legally sufficient e-invoice. Error codes: [einvoice-xml-invalid](https://docs.pdfik.net/error-codes#einvoice-xml-invalid), [einvoice-options-conflict](https://docs.pdfik.net/error-codes#einvoice-options-conflict), [einvoice-template-not-found](https://docs.pdfik.net/error-codes#einvoice-template-not-found), [payload-too-large-for-queue](https://docs.pdfik.net/error-codes#payload-too-large-for-queue).

## Limits, retention and error codes

- **Generated volume per month** (byte quota, counts rendered output only — downloads are free): Free 0.5 GB, Starter 10 GB, Pro 50 GB, Business 300 GB. Business plans can purchase additional +1 GB blocks from the dashboard. Exceeding the quota returns `429` ([quota-bytes-exceeded](https://docs.pdfik.net/error-codes#quota-bytes-exceeded)).
- **File size**: up to 150 MB per PDF on every plan — contact support if you need more. A larger render fails the job with `FILE_TOO_LARGE` ([file-too-large](https://docs.pdfik.net/error-codes#file-too-large)).
- **Retention**: each file is available for download for 24 hours after the job finishes; the exact deadline is the `expires_at` field in the job status and webhook payload. After that the download returns `410` ([file-expired](https://docs.pdfik.net/error-codes#file-expired)).
- **Downloads**: at most 3 download attempts per file (counted when the stream starts); after that the API returns `429` ([download-attempts-exhausted](https://docs.pdfik.net/error-codes#download-attempts-exhausted)) — re-render the file or contact support. Only 1 download stream may be active at a time; a concurrent request returns `429` ([download-busy](https://docs.pdfik.net/error-codes#download-busy)), which does not burn an attempt — `download_pdf` waits the ~10 seconds the API names and asks again by itself (up to five times), so you only see this error if the file stays busy.

### Handling Webhooks

If you specify `webhook_url` in the options of `url_to_pdf` or `html_to_pdf`, PDFik will send an HTTP `POST` request to your server when the rendering job is finished.

You should verify the authenticity of the webhook by validating the signature. We provide a static helper `verify_webhook_signature` in `PdfikClient` for this purpose.

During a secret rotation both the new and the previous secret verify for the overlap
window you pick in the dashboard (1 hour to 5 days, or none at all), so check the new
one first and fall back to the old one while you roll your receivers out.

Custom delivery headers (e.g. a Cloudflare Access service token) are configured once on
the dashboard Webhooks page and attached to every delivery — nothing to pass per request.

#### Example (FastAPI):

```python
from fastapi import FastAPI, Request, HTTPException
from pdfik import PdfikClient

app = FastAPI()

# Get your webhook secret from the PDFik Dashboard
WEBHOOK_SECRET = "whsec_..."

@app.post("/webhook")
async def handle_webhook(request: Request):
    # Retrieve headers
    headers = dict(request.headers)
    
    # Retrieve the raw payload bytes
    raw_body = await request.body()
    
    # Verify signature
    is_valid = PdfikClient.verify_webhook_signature(raw_body, headers, WEBHOOK_SECRET)
    
    if not is_valid:
        raise HTTPException(status_code=400, detail="Invalid signature")
        
    # Process event
    event = await request.json()
    # The payload has no event type: a finished job carries status "done" (or "failed").
    print(f"Job {event.get('job_id')} finished with status {event.get('status')}")
    
    return {"received": True}
```

## License

MIT License.
