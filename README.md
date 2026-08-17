# pdfik

> **Where this code lives:** extracted from the PDFik platform monorepo (last sync 2026-08-17).
> Releases to PyPI are cut from the monorepo; issues and PRs are welcome here.

Official Python SDK for [PDFik](https://pdfik.net) — the asynchronous URL/HTML-to-PDF API.

Submit a public URL or raw HTML, get a job id back, and receive an HMAC-signed webhook (or poll) when the PDF is ready. Rendering runs on sandboxed headless Chromium, so modern CSS, web fonts and JavaScript-heavy pages come out the way they look in the browser.

## Features

- **Type Safety**: Type hints for all options and response objects.
- **Sync & Async**: Exposes both `PdfikClient` and `AsyncPdfikClient` using the modern `httpx` engine.
- **Auto Retry**: Automatic exponential backoff for `5xx` and `429` (Rate Limit) errors powered by `tenacity`.
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

## Limits, retention and error codes

- **Generated volume per month** (byte quota, counts rendered output only — downloads are free): Free 0.5 GB, Starter 10 GB, Pro 50 GB, Business 300 GB. Business plans can purchase additional +1 GB blocks from the dashboard. Exceeding the quota returns `429` ([quota-bytes-exceeded](https://docs.pdfik.net/error-codes#quota-bytes-exceeded)).
- **File size**: up to 150 MB per PDF on every plan — contact support if you need more. A larger render fails the job with `FILE_TOO_LARGE` ([file-too-large](https://docs.pdfik.net/error-codes#file-too-large)).
- **Retention**: each file is available for download for 24 hours after the job finishes; the exact deadline is the `expires_at` field in the job status and webhook payload. After that the download returns `410` ([file-expired](https://docs.pdfik.net/error-codes#file-expired)).
- **Downloads**: at most 3 download attempts per file (counted when the stream starts); after that the API returns `429` ([download-attempts-exhausted](https://docs.pdfik.net/error-codes#download-attempts-exhausted)) — re-render the file or contact support. Only 1 download stream may be active at a time; a concurrent request returns `429` ([download-busy](https://docs.pdfik.net/error-codes#download-busy)) — wait ~10 seconds and retry, it does not burn an attempt.

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
    print(f"Received webhook event: {event.get('event_type')} for job: {event.get('job_id')}")
    
    return {"received": True}
```

## License

MIT License.
