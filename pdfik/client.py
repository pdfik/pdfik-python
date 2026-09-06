import time
import asyncio
import hmac
import hashlib
from typing import Optional
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

from .exceptions import PdfikError
from .models import (
    JobCreatedResponse,
    JobStatusResponse,
    JobFileResponse,
    PdfOptions,
    RenderOptions,
    JobAuthOptions,
    EInvoiceOptions,
    EInvoiceProfile,
    _to_dict
)

def _is_retryable_exception(exception: BaseException) -> bool:
    return isinstance(exception, PdfikError) and (exception.status_code >= 500 or exception.status_code == 429)

class PdfikClient:
    def __init__(self, api_key: str, base_url: str = "https://api.pdfik.net"):
        if not api_key:
            raise ValueError("API key is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        retry=retry_if_exception(_is_retryable_exception),
        reraise=True
    )
    def _request(self, method: str, path: str, json_data: Optional[dict] = None, extra_headers: Optional[dict] = None) -> httpx.Response:
        url = f"{self.base_url}{path}"
        headers = {
            "X-API-Key": self.api_key,
        }
        if extra_headers:
            headers.update(extra_headers)

        try:
            response = self.client.request(method, url, headers=headers, json=json_data)
        except httpx.RequestError as exc:
            raise PdfikError(f"Connection failed: {exc}", 503)

        if response.is_error:
            error_code = None
            message = f"API request failed with status {response.status_code}"
            response_body = response.text
            try:
                data = response.json()
                if isinstance(data, dict):
                    message = data.get("detail", message)
                    # pdf-api's RFC 7807 bodies carry the code in "error";
                    # "error_code" is a fallback for any body that still uses it.
                    error_code = data.get("error") or data.get("error_code")
            except Exception:
                pass
            raise PdfikError(message, response.status_code, error_code, response_body)
            
        return response

    def url_to_pdf(
        self,
        url: str,
        *,
        webhook_url: Optional[str] = None,
        options: Optional[PdfOptions] = None,
        render: Optional[RenderOptions] = None,
        auth: Optional[JobAuthOptions] = None,
        einvoice: Optional[EInvoiceOptions] = None,
        idempotency_key: Optional[str] = None,
        test: Optional[bool] = None
    ) -> JobCreatedResponse:
        """Submit a job that renders a public URL to a PDF.

        Pass ``einvoice`` to embed a Factur-X CII XML into the rendered PDF:
        the output is normalized to PDF/A-3 with the XML attached as
        ``factur-x.xml``. Not combinable with ``options.user_password`` or
        ``options.compression``.

        Set ``test=True`` to run the job in test mode: it goes through the full
        pipeline (statuses, webhook, download) without real rendering, and the
        download returns a sample PDF. Quotas are not debited — test jobs are
        free and rate-limited instead (60/min, 2,000/day). Job status
        and webhook payloads always carry ``test: true|false``.
        """
        body = {"url": url}
        if webhook_url:
            body["webhook_url"] = webhook_url
        if options:
            body["options"] = _to_dict(options)
        if render:
            body["render"] = _to_dict(render)
        if auth:
            body["auth"] = _to_dict(auth)
        if einvoice:
            body["einvoice"] = _to_dict(einvoice)
        if test:
            body["test"] = True

        extra_headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        res = self._request("POST", "/url-to-pdf", json_data=body, extra_headers=extra_headers)
        return JobCreatedResponse.from_dict(res.json())

    def html_to_pdf(
        self,
        html: str,
        *,
        webhook_url: Optional[str] = None,
        options: Optional[PdfOptions] = None,
        render: Optional[RenderOptions] = None,
        einvoice: Optional[EInvoiceOptions] = None,
        idempotency_key: Optional[str] = None,
        test: Optional[bool] = None
    ) -> JobCreatedResponse:
        """Submit a job that renders raw HTML markup to a PDF.

        Pass ``einvoice`` to embed a Factur-X CII XML into the rendered PDF:
        the output is normalized to PDF/A-3 with the XML attached as
        ``factur-x.xml``. Not combinable with ``options.user_password`` or
        ``options.compression``.

        Set ``test=True`` to run the job in test mode: it goes through the full
        pipeline (statuses, webhook, download) without real rendering, and the
        download returns a sample PDF. Quotas are not debited — test jobs are
        free and rate-limited instead (60/min, 2,000/day). Job status
        and webhook payloads always carry ``test: true|false``.
        """
        body = {"html": html}
        if webhook_url:
            body["webhook_url"] = webhook_url
        if options:
            body["options"] = _to_dict(options)
        if render:
            body["render"] = _to_dict(render)
        if einvoice:
            body["einvoice"] = _to_dict(einvoice)
        if test:
            body["test"] = True

        extra_headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        res = self._request("POST", "/html-to-pdf", json_data=body, extra_headers=extra_headers)
        return JobCreatedResponse.from_dict(res.json())

    def einvoice_to_pdf(
        self,
        xml: str,
        *,
        profile: EInvoiceProfile = EInvoiceProfile.EN16931,
        template_id: Optional[str] = None,
        template: Optional[dict] = None,
        webhook_url: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        test: Optional[bool] = None
    ) -> JobCreatedResponse:
        """Submit a Factur-X e-invoice job: the UN/CEFACT CII ``xml`` is
        validated against the official XSD of the declared ``profile``, a
        human-readable invoice is built from a block template, rendered to
        PDF/A-3 and the XML is embedded as ``factur-x.xml`` (Factur-X /
        ZUGFeRD hybrid). Then poll/download via the normal job flow.

        ``template_id`` (a template saved in the dashboard) and ``template``
        (an inline block-template dict) are mutually exclusive; omit both to
        use the account default template. Available on every plan; on Free
        the output carries a PDFik.net watermark. Profiles ``minimum`` and
        ``basicwl`` carry accompanying data only and are NOT a legally
        sufficient e-invoice.

        Set ``test=True`` to run the job in test mode: it goes through the full
        pipeline (statuses, webhook, download) without real rendering, and the
        download returns a sample PDF. Quotas are not debited — test jobs are
        free and rate-limited instead (60/min, 2,000/day). Job status
        and webhook payloads always carry ``test: true|false``.
        """
        body = {"xml": xml, "profile": _to_dict(profile)}
        if template_id:
            body["template_id"] = template_id
        if template:
            body["template"] = template
        if webhook_url:
            body["webhook_url"] = webhook_url
        if test:
            body["test"] = True

        extra_headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        res = self._request("POST", "/einvoice-to-pdf", json_data=body, extra_headers=extra_headers)
        return JobCreatedResponse.from_dict(res.json())

    def get_job(self, job_id: str) -> JobStatusResponse:
        res = self._request("GET", f"/jobs/{job_id}")
        return JobStatusResponse.from_dict(res.json())

    def wait_for_job(
        self,
        job_id: str,
        *,
        timeout: int = 120,
        poll_interval: int = 2
    ) -> JobStatusResponse:
        start_time = time.time()
        while True:
            job = self.get_job(job_id)
            if job.status in ("done", "failed"):
                if job.status == "failed":
                    raise PdfikError(
                        f"Job {job_id} failed",
                        400,
                        job.error_code or "JOB_FAILED"
                    )
                return job
                
            if time.time() - start_time >= timeout:
                raise PdfikError(f"Job {job_id} timed out", 408, "TIMEOUT")
                
            time.sleep(poll_interval)

    def get_file_url(self, job_id: str) -> JobFileResponse:
        clean_url = self.base_url.rstrip("/")
        return JobFileResponse(
            job_id=job_id,
            download_url=f"{clean_url}/jobs/{job_id}/download",
            expires_in_seconds=-1
        )

    def download_pdf(self, job_id: str) -> bytes:
        res = self._request("GET", f"/jobs/{job_id}/download")
        return res.content

    def close(self):
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    @staticmethod
    def verify_webhook_signature(raw_body: bytes, headers: dict, secret: str) -> bool:
        headers_lower = {k.lower(): v for k, v in headers.items()}
        signature_header = headers_lower.get("x-pdfik-signature")
        timestamp_header = headers_lower.get("x-pdfik-timestamp")

        if not signature_header or not timestamp_header:
            return False

        try:
            parts = dict(p.split("=", 1) for p in signature_header.split(","))
            timestamp = parts.get("t")
            received_sig = parts.get("v1")

            if not timestamp or not received_sig:
                return False

            now = int(time.time())
            if abs(now - int(timestamp)) > 300:
                return False

            signed_payload = f"{timestamp}.{raw_body.decode('utf-8')}".encode("utf-8")
            expected_sig = hmac.new(
                secret.encode("utf-8"), signed_payload, hashlib.sha256
            ).hexdigest()

            return hmac.compare_digest(expected_sig, received_sig)
        except Exception:
            return False



class AsyncPdfikClient:
    def __init__(self, api_key: str, base_url: str = "https://api.pdfik.net"):
        if not api_key:
            raise ValueError("API key is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        retry=retry_if_exception(_is_retryable_exception),
        reraise=True
    )
    async def _request(self, method: str, path: str, json_data: Optional[dict] = None, extra_headers: Optional[dict] = None) -> httpx.Response:
        url = f"{self.base_url}{path}"
        headers = {
            "X-API-Key": self.api_key,
        }
        if extra_headers:
            headers.update(extra_headers)

        try:
            response = await self.client.request(method, url, headers=headers, json=json_data)
        except httpx.RequestError as exc:
            raise PdfikError(f"Connection failed: {exc}", 503)

        if response.is_error:
            error_code = None
            message = f"API request failed with status {response.status_code}"
            response_body = response.text
            try:
                data = response.json()
                if isinstance(data, dict):
                    message = data.get("detail", message)
                    # pdf-api's RFC 7807 bodies carry the code in "error";
                    # "error_code" is a fallback for any body that still uses it.
                    error_code = data.get("error") or data.get("error_code")
            except Exception:
                pass
            raise PdfikError(message, response.status_code, error_code, response_body)
            
        return response

    async def url_to_pdf(
        self,
        url: str,
        *,
        webhook_url: Optional[str] = None,
        options: Optional[PdfOptions] = None,
        render: Optional[RenderOptions] = None,
        auth: Optional[JobAuthOptions] = None,
        einvoice: Optional[EInvoiceOptions] = None,
        idempotency_key: Optional[str] = None,
        test: Optional[bool] = None
    ) -> JobCreatedResponse:
        """Submit a job that renders a public URL to a PDF.

        Pass ``einvoice`` to embed a Factur-X CII XML into the rendered PDF:
        the output is normalized to PDF/A-3 with the XML attached as
        ``factur-x.xml``. Not combinable with ``options.user_password`` or
        ``options.compression``.

        Set ``test=True`` to run the job in test mode: it goes through the full
        pipeline (statuses, webhook, download) without real rendering, and the
        download returns a sample PDF. Quotas are not debited — test jobs are
        free and rate-limited instead (60/min, 2,000/day). Job status
        and webhook payloads always carry ``test: true|false``.
        """
        body = {"url": url}
        if webhook_url:
            body["webhook_url"] = webhook_url
        if options:
            body["options"] = _to_dict(options)
        if render:
            body["render"] = _to_dict(render)
        if auth:
            body["auth"] = _to_dict(auth)
        if einvoice:
            body["einvoice"] = _to_dict(einvoice)
        if test:
            body["test"] = True

        extra_headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        res = await self._request("POST", "/url-to-pdf", json_data=body, extra_headers=extra_headers)
        return JobCreatedResponse.from_dict(res.json())

    async def html_to_pdf(
        self,
        html: str,
        *,
        webhook_url: Optional[str] = None,
        options: Optional[PdfOptions] = None,
        render: Optional[RenderOptions] = None,
        einvoice: Optional[EInvoiceOptions] = None,
        idempotency_key: Optional[str] = None,
        test: Optional[bool] = None
    ) -> JobCreatedResponse:
        """Submit a job that renders raw HTML markup to a PDF.

        Pass ``einvoice`` to embed a Factur-X CII XML into the rendered PDF:
        the output is normalized to PDF/A-3 with the XML attached as
        ``factur-x.xml``. Not combinable with ``options.user_password`` or
        ``options.compression``.

        Set ``test=True`` to run the job in test mode: it goes through the full
        pipeline (statuses, webhook, download) without real rendering, and the
        download returns a sample PDF. Quotas are not debited — test jobs are
        free and rate-limited instead (60/min, 2,000/day). Job status
        and webhook payloads always carry ``test: true|false``.
        """
        body = {"html": html}
        if webhook_url:
            body["webhook_url"] = webhook_url
        if options:
            body["options"] = _to_dict(options)
        if render:
            body["render"] = _to_dict(render)
        if einvoice:
            body["einvoice"] = _to_dict(einvoice)
        if test:
            body["test"] = True

        extra_headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        res = await self._request("POST", "/html-to-pdf", json_data=body, extra_headers=extra_headers)
        return JobCreatedResponse.from_dict(res.json())

    async def einvoice_to_pdf(
        self,
        xml: str,
        *,
        profile: EInvoiceProfile = EInvoiceProfile.EN16931,
        template_id: Optional[str] = None,
        template: Optional[dict] = None,
        webhook_url: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        test: Optional[bool] = None
    ) -> JobCreatedResponse:
        """Submit a Factur-X e-invoice job: the UN/CEFACT CII ``xml`` is
        validated against the official XSD of the declared ``profile``, a
        human-readable invoice is built from a block template, rendered to
        PDF/A-3 and the XML is embedded as ``factur-x.xml`` (Factur-X /
        ZUGFeRD hybrid). Then poll/download via the normal job flow.

        ``template_id`` (a template saved in the dashboard) and ``template``
        (an inline block-template dict) are mutually exclusive; omit both to
        use the account default template. Available on every plan; on Free
        the output carries a PDFik.net watermark. Profiles ``minimum`` and
        ``basicwl`` carry accompanying data only and are NOT a legally
        sufficient e-invoice.

        Set ``test=True`` to run the job in test mode: it goes through the full
        pipeline (statuses, webhook, download) without real rendering, and the
        download returns a sample PDF. Quotas are not debited — test jobs are
        free and rate-limited instead (60/min, 2,000/day). Job status
        and webhook payloads always carry ``test: true|false``.
        """
        body = {"xml": xml, "profile": _to_dict(profile)}
        if template_id:
            body["template_id"] = template_id
        if template:
            body["template"] = template
        if webhook_url:
            body["webhook_url"] = webhook_url
        if test:
            body["test"] = True

        extra_headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        res = await self._request("POST", "/einvoice-to-pdf", json_data=body, extra_headers=extra_headers)
        return JobCreatedResponse.from_dict(res.json())

    async def get_job(self, job_id: str) -> JobStatusResponse:
        res = await self._request("GET", f"/jobs/{job_id}")
        return JobStatusResponse.from_dict(res.json())

    async def wait_for_job(
        self,
        job_id: str,
        *,
        timeout: int = 120,
        poll_interval: int = 2
    ) -> JobStatusResponse:
        start_time = time.time()
        while True:
            job = await self.get_job(job_id)
            if job.status in ("done", "failed"):
                if job.status == "failed":
                    raise PdfikError(
                        f"Job {job_id} failed",
                        400,
                        job.error_code or "JOB_FAILED"
                    )
                return job
                
            if time.time() - start_time >= timeout:
                raise PdfikError(f"Job {job_id} timed out", 408, "TIMEOUT")
                
            await asyncio.sleep(poll_interval)

    async def get_file_url(self, job_id: str) -> JobFileResponse:
        clean_url = self.base_url.rstrip("/")
        return JobFileResponse(
            job_id=job_id,
            download_url=f"{clean_url}/jobs/{job_id}/download",
            expires_in_seconds=-1
        )

    async def download_pdf(self, job_id: str) -> bytes:
        res = await self._request("GET", f"/jobs/{job_id}/download")
        return res.content

    async def close(self):
        await self.client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
