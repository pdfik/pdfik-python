import json
import time
import asyncio
import hmac
import hashlib
import uuid
from typing import Optional, Tuple
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
    ImageOptions,
    DeliveryOptions,
    _to_dict
)

# The two 429s that clear by themselves within a minute: the per-account request
# rate and the in-flight job cap. A byte-quota 429, the test-mode caps and an
# exhausted download counter do not, so they are raised at once.
_THROTTLE_CASES = ("rate-limit-exceeded", "concurrency-limit-exceeded")
# The download call has a third one: another stream of the same file is still open.
# It clears within seconds and costs no download attempt. An exhausted download
# counter is a 429 too and stays out: waiting cannot bring the attempts back.
_DOWNLOAD_THROTTLE_CASES = _THROTTLE_CASES + ("download-busy",)
# A throttle is waited out, not counted as a failed attempt — but not forever:
# after this many waits in a row the 429 is raised.
_THROTTLE_WAITS = 5
_RETRY_AFTER_FALLBACK = 10.0
_RETRY_AFTER_CAP = 60.0
# The per-request budget of a synchronous (`wait: true`) call. The clients are
# built with httpx's defaults — 5 s of READ timeout — which would cut a held
# request long before the server's 45 s render budget. 70 s leaves room for the
# hold, the file transfer and the gateways in between, without parking the
# caller forever when a proxy swallows the response.
_SYNC_WAIT_TIMEOUT = httpx.Timeout(70.0, connect=10.0)

def _is_retryable_exception(exception: BaseException) -> bool:
    return isinstance(exception, PdfikError) and exception.status_code >= 500

def _idempotency_header(idempotency_key: Optional[str]) -> dict:
    # Built once per create call, outside _request: _request runs again for every
    # resend, and a resend must carry the key of the first attempt — the API then
    # answers with the job that attempt created instead of creating a second one.
    return {"Idempotency-Key": idempotency_key or str(uuid.uuid4())}

def _problem(err: PdfikError) -> dict:
    try:
        data = json.loads(err.response_body or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}

def _is_throttled(err: PdfikError, cases: Tuple[str, ...] = _THROTTLE_CASES) -> bool:
    """True for a 429 that is one of ``cases``: the fragment of the problem's
    ``type`` URL or, as the API sends today, the ``error`` code in upper snake
    case."""
    if err.status_code != 429:
        return False
    problem_type = str(_problem(err).get("type") or "")
    code = str(err.error_code or "").upper()
    return any(
        problem_type.endswith("#" + case) or code == case.replace("-", "_").upper()
        for case in cases
    )

def _throttle_delay(err: PdfikError, fallback: float, retry_after: Optional[str] = None) -> float:
    """Seconds to wait after a 429: the API's `retry_after_seconds`, else its
    `Retry-After` header (both capped at 60 s so the server cannot park the
    caller), else `fallback`."""
    for stated in (_problem(err).get("retry_after_seconds"), retry_after):
        if stated is None:
            continue
        try:
            seconds = float(stated)
        except (TypeError, ValueError, OverflowError):
            continue
        if seconds > 0:
            return min(seconds, _RETRY_AFTER_CAP)
    return float(fallback)


def _error_message(detail) -> Optional[str]:
    """Readable text for an error body's ``detail``.

    Request-validation errors (422) carry a LIST of ``{"loc": [...], "msg": ...}``
    items instead of a string; printed raw they read as a Python list repr. They
    become ``"options.viewport.width: Input should be less than or equal to 1920"``.
    """
    if isinstance(detail, list):
        parts = []
        for item in detail:
            if isinstance(item, dict):
                loc = ".".join(str(x) for x in item.get("loc", []) if x != "body")
                msg = item.get("msg", "")
                parts.append("%s: %s" % (loc, msg) if loc else str(msg))
            else:
                parts.append(str(item))
        return "; ".join(p for p in parts if p) or None
    if detail:
        return str(detail)
    return None


def _error_from(response: httpx.Response) -> PdfikError:
    error_code = None
    message = f"API request failed with status {response.status_code}"
    response_body = response.text
    try:
        data = response.json()
        if isinstance(data, dict):
            message = _error_message(data.get("detail")) or message
            # pdf-api's RFC 7807 bodies carry the code in "error";
            # "error_code" is a fallback for any body that still uses it.
            error_code = data.get("error") or data.get("error_code")
    except Exception:
        pass
    return PdfikError(message, response.status_code, error_code, response_body)


def _sync_file_bytes(response: httpx.Response) -> bytes:
    """The file bytes of a synchronous (``wait: true``) success.

    An OLD server that predates ``wait`` ignores the unknown field and answers
    the ordinary 202 JSON — handing that back as file bytes would save a few
    lines of JSON as output.pdf, and nobody notices until a reader chokes on
    it. So a 202, or JSON on any 2xx, raises instead and names the job id: the
    job WAS created and renders normally, it just has to be fetched the
    asynchronous way (``wait_for_job`` + ``download_pdf``).
    """
    content_type = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if response.status_code == 202 or content_type == "application/json":
        job_id = None
        try:
            data = response.json()
            if isinstance(data, dict) and data.get("job_id"):
                job_id = str(data["job_id"])
        except Exception:
            job_id = None
        raise PdfikError(
            "The server answered %d with JSON instead of the file: it does not "
            "support wait=true. Job %s was still created and renders normally - "
            "fetch it with wait_for_job() + download_pdf(), or use the plain "
            "create method against this server." % (response.status_code, job_id or "(id unreadable)"),
            response.status_code,
            "SYNC_MODE_UNSUPPORTED",
            response.text,
        )
    return response.content


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
    def _request(self, method: str, path: str, json_data: Optional[dict] = None, extra_headers: Optional[dict] = None, throttle_waits: int = _THROTTLE_WAITS, throttle_cases: Tuple[str, ...] = _THROTTLE_CASES) -> httpx.Response:
        url = f"{self.base_url}{path}"
        headers = {
            "X-API-Key": self.api_key,
        }
        if extra_headers:
            headers.update(extra_headers)

        waits = 0
        while True:
            try:
                response = self.client.request(method, url, headers=headers, json=json_data)
            except httpx.RequestError as exc:
                raise PdfikError(f"Connection failed: {exc}", 503)

            if not response.is_error:
                return response

            error = _error_from(response)
            # A throttled request created no job and started no download stream, so
            # sending it again is safe.
            if not _is_throttled(error, throttle_cases) or waits >= throttle_waits:
                raise error
            waits += 1
            time.sleep(_throttle_delay(error, _RETRY_AFTER_FALLBACK, response.headers.get("Retry-After")))

    def _request_once(self, method: str, path: str, json_data: Optional[dict] = None, extra_headers: Optional[dict] = None, throttle_waits: int = _THROTTLE_WAITS) -> httpx.Response:
        """One attempt, outside tenacity — the request path of the synchronous
        (``wait: true``) ``*_file`` methods.

        It deliberately duplicates ``_request``'s loop instead of calling it:
        a held request must never be resent after a 5xx or a dropped
        connection, because by the time such an answer reaches us the render
        may already have finished and been charged, and unlike the 202 flow
        there is no job id in hand to deduplicate against. The 503 SYNC_TIMEOUT
        in particular says "cancelled, NOT charged, retry if YOU want" — an
        automatic resend would burn another 45 s on a render we already know is
        too slow. Throttle 429s ARE still waited out: a throttled request
        created no job, so resending it under the same Idempotency-Key is safe.
        The per-request timeout overrides the client's default (5 s of read
        timeout), which would cut every held request short.
        """
        url = f"{self.base_url}{path}"
        headers = {
            "X-API-Key": self.api_key,
        }
        if extra_headers:
            headers.update(extra_headers)

        waits = 0
        while True:
            try:
                response = self.client.request(method, url, headers=headers, json=json_data, timeout=_SYNC_WAIT_TIMEOUT)
            except httpx.RequestError as exc:
                raise PdfikError(f"Connection failed: {exc}", 503)

            if not response.is_error:
                return response

            error = _error_from(response)
            if not _is_throttled(error) or waits >= throttle_waits:
                raise error
            waits += 1
            time.sleep(_throttle_delay(error, _RETRY_AFTER_FALLBACK, response.headers.get("Retry-After")))

    def _request_file(self, path: str, body: dict, idempotency_key: Optional[str]) -> bytes:
        # `wait: true` turns the create into a held request: the answer is the
        # file itself (or an RFC 7807 problem), never a job id to poll.
        body["wait"] = True
        extra_headers = _idempotency_header(idempotency_key)
        res = self._request_once("POST", path, json_data=body, extra_headers=extra_headers)
        return _sync_file_bytes(res)

    def url_to_pdf(
        self,
        url: str,
        *,
        webhook_url: Optional[str] = None,
        options: Optional[PdfOptions] = None,
        render: Optional[RenderOptions] = None,
        auth: Optional[JobAuthOptions] = None,
        einvoice: Optional[EInvoiceOptions] = None,
        delivery: Optional[DeliveryOptions] = None,
        idempotency_key: Optional[str] = None,
        test: Optional[bool] = None
    ) -> JobCreatedResponse:
        """Submit a job that renders a public URL to a PDF.

        Pass ``einvoice`` to embed a Factur-X CII XML into the rendered PDF:
        the output is normalized to PDF/A-3 with the XML attached as
        ``factur-x.xml``. Not combinable with ``options.user_password`` or
        ``options.compression``.

        Pass ``delivery`` to upload the output straight to your own
        bucket via a presigned PUT URL: nothing is stored on PDFik's side,
        the download endpoint answers 404 for such jobs, and the
        ``job.finished`` webhook carries neither ``file_url`` nor
        ``expires_at`` (PDFik does not record where the file went — you
        already know). Not combinable with ``test``.

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
        if delivery:
            body["delivery"] = _to_dict(delivery)
        if test:
            body["test"] = True

        extra_headers = _idempotency_header(idempotency_key)
        res = self._request("POST", "/url-to-pdf", json_data=body, extra_headers=extra_headers)
        return JobCreatedResponse.from_dict(res.json())

    def url_to_pdf_file(
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
    ) -> bytes:
        """Render a public URL to a PDF in ONE call and return the PDF bytes.

        ``url_to_pdf``'s request sent with ``wait: true``: the API holds it
        until the render finishes (45 s at most) and answers with the file
        itself. Same keyword arguments as ``url_to_pdf`` except ``delivery`` —
        the response already IS the delivery. The file also stays downloadable
        for the normal 24 h under the job id the ``X-PDFik-Job-Id`` response
        header names.

        Raises ``PdfikError`` instead of returning:

        - ``SYNC_TIMEOUT`` (503) after 45 s. The job was cancelled, nothing was
          charged and the Idempotency-Key was released, so trying again is safe
          — but the client never resends this one by itself: the render was
          already too slow once, and a page that slow belongs in the
          asynchronous flow (``url_to_pdf`` + webhook or ``wait_for_job``).
        - the job's own error code (422, e.g. ``PAGE_TIMEOUT``) when the render
          itself failed.
        - ``SYNC_MODE_UNSUPPORTED`` naming the created job when an old API
          ignores ``wait`` and answers the plain 202 JSON — that job still
          renders and can be fetched with ``wait_for_job`` + ``download_pdf``.

        Unlike every other call, a 5xx or a dropped connection is NOT retried
        here (the render may have finished — and been charged — by then);
        throttle 429s are waited out as usual, under one Idempotency-Key.
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

        return self._request_file("/url-to-pdf", body, idempotency_key)

    def html_to_pdf(
        self,
        html: str,
        *,
        webhook_url: Optional[str] = None,
        options: Optional[PdfOptions] = None,
        render: Optional[RenderOptions] = None,
        einvoice: Optional[EInvoiceOptions] = None,
        delivery: Optional[DeliveryOptions] = None,
        idempotency_key: Optional[str] = None,
        test: Optional[bool] = None
    ) -> JobCreatedResponse:
        """Submit a job that renders raw HTML markup to a PDF.

        Pass ``einvoice`` to embed a Factur-X CII XML into the rendered PDF:
        the output is normalized to PDF/A-3 with the XML attached as
        ``factur-x.xml``. Not combinable with ``options.user_password`` or
        ``options.compression``.

        Pass ``delivery`` to upload the output straight to your own
        bucket via a presigned PUT URL: nothing is stored on PDFik's side,
        the download endpoint answers 404 for such jobs, and the
        ``job.finished`` webhook carries neither ``file_url`` nor
        ``expires_at`` (PDFik does not record where the file went — you
        already know). Not combinable with ``test``.

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
        if delivery:
            body["delivery"] = _to_dict(delivery)
        if test:
            body["test"] = True

        extra_headers = _idempotency_header(idempotency_key)
        res = self._request("POST", "/html-to-pdf", json_data=body, extra_headers=extra_headers)
        return JobCreatedResponse.from_dict(res.json())

    def html_to_pdf_file(
        self,
        html: str,
        *,
        webhook_url: Optional[str] = None,
        options: Optional[PdfOptions] = None,
        render: Optional[RenderOptions] = None,
        einvoice: Optional[EInvoiceOptions] = None,
        idempotency_key: Optional[str] = None,
        test: Optional[bool] = None
    ) -> bytes:
        """Render raw HTML to a PDF in ONE call and return the PDF bytes.

        ``html_to_pdf`` sent with ``wait: true`` — same keyword arguments
        except ``delivery`` (the response is the delivery). See
        ``url_to_pdf_file`` for the synchronous contract: 503 ``SYNC_TIMEOUT``
        (job cancelled, not charged, never auto-resent), 422 with the job's own
        error code, and a clear error when an old server answers 202 JSON.
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

        return self._request_file("/html-to-pdf", body, idempotency_key)

    def markdown_to_pdf(
        self,
        markdown: str,
        *,
        webhook_url: Optional[str] = None,
        options: Optional[PdfOptions] = None,
        render: Optional[RenderOptions] = None,
        delivery: Optional[DeliveryOptions] = None,
        idempotency_key: Optional[str] = None,
        test: Optional[bool] = None
    ) -> JobCreatedResponse:
        """Submit a job that converts Markdown to a PDF.

        CommonMark plus GFM tables and strikethrough are supported; raw HTML
        inside the Markdown is escaped, not rendered — use ``html_to_pdf``
        for full HTML control. A built-in print stylesheet is applied, and
        ``options`` (paper format, margins, header/footer, watermark, ...)
        works exactly as on ``html_to_pdf``. Job polling and download are
        identical to the other PDF endpoints.

        ``delivery`` uploads the output straight to your own bucket
        via a presigned PUT URL; ``test=True`` runs the full pipeline without
        real rendering. The two cannot be combined.
        """
        body = {"markdown": markdown}
        if webhook_url:
            body["webhook_url"] = webhook_url
        if options:
            body["options"] = _to_dict(options)
        if render:
            body["render"] = _to_dict(render)
        if delivery:
            body["delivery"] = _to_dict(delivery)
        if test:
            body["test"] = True

        extra_headers = _idempotency_header(idempotency_key)
        res = self._request("POST", "/markdown-to-pdf", json_data=body, extra_headers=extra_headers)
        return JobCreatedResponse.from_dict(res.json())

    def markdown_to_pdf_file(
        self,
        markdown: str,
        *,
        webhook_url: Optional[str] = None,
        options: Optional[PdfOptions] = None,
        render: Optional[RenderOptions] = None,
        idempotency_key: Optional[str] = None,
        test: Optional[bool] = None
    ) -> bytes:
        """Convert Markdown to a PDF in ONE call and return the PDF bytes.

        ``markdown_to_pdf`` sent with ``wait: true`` — same keyword arguments
        except ``delivery`` (the response is the delivery). See
        ``url_to_pdf_file`` for the synchronous contract: 503 ``SYNC_TIMEOUT``
        (job cancelled, not charged, never auto-resent), 422 with the job's own
        error code, and a clear error when an old server answers 202 JSON.
        """
        body = {"markdown": markdown}
        if webhook_url:
            body["webhook_url"] = webhook_url
        if options:
            body["options"] = _to_dict(options)
        if render:
            body["render"] = _to_dict(render)
        if test:
            body["test"] = True

        return self._request_file("/markdown-to-pdf", body, idempotency_key)

    def url_to_image(
        self,
        url: str,
        *,
        webhook_url: Optional[str] = None,
        options: Optional[ImageOptions] = None,
        render: Optional[RenderOptions] = None,
        auth: Optional[JobAuthOptions] = None,
        delivery: Optional[DeliveryOptions] = None,
        idempotency_key: Optional[str] = None,
        test: Optional[bool] = None
    ) -> JobCreatedResponse:
        """Submit a job that captures a public URL as an image (screenshot).

        ``options`` controls the output: ``format`` "png" (default) or
        "jpeg", ``full_page`` (default False - the visible area only),
        ``quality`` (JPEG only) and ``viewport`` (default 1024x768; width
        320-1920, height 320-8192). Poll with
        ``wait_for_job`` and fetch the bytes with ``download_pdf`` — for
        image jobs the download returns ``image/png`` or ``image/jpeg``
        instead of a PDF.

        ``auth`` (Pro+) attaches basic/bearer credentials exactly as on
        ``url_to_pdf``. ``delivery`` and ``test`` behave as on the PDF
        endpoints and cannot be combined with each other.
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
        if delivery:
            body["delivery"] = _to_dict(delivery)
        if test:
            body["test"] = True

        extra_headers = _idempotency_header(idempotency_key)
        res = self._request("POST", "/url-to-image", json_data=body, extra_headers=extra_headers)
        return JobCreatedResponse.from_dict(res.json())

    def url_to_image_file(
        self,
        url: str,
        *,
        webhook_url: Optional[str] = None,
        options: Optional[ImageOptions] = None,
        render: Optional[RenderOptions] = None,
        auth: Optional[JobAuthOptions] = None,
        idempotency_key: Optional[str] = None,
        test: Optional[bool] = None
    ) -> bytes:
        """Capture a public URL as an image in ONE call and return the raw
        PNG or JPEG bytes (whichever ``options.format`` asked for).

        ``url_to_image`` sent with ``wait: true`` — same keyword arguments
        except ``delivery`` (the response is the delivery). See
        ``url_to_pdf_file`` for the synchronous contract: 503 ``SYNC_TIMEOUT``
        (job cancelled, not charged, never auto-resent), 422 with the job's own
        error code, and a clear error when an old server answers 202 JSON.
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
        if test:
            body["test"] = True

        return self._request_file("/url-to-image", body, idempotency_key)

    def html_to_image(
        self,
        html: str,
        *,
        webhook_url: Optional[str] = None,
        options: Optional[ImageOptions] = None,
        render: Optional[RenderOptions] = None,
        delivery: Optional[DeliveryOptions] = None,
        idempotency_key: Optional[str] = None,
        test: Optional[bool] = None
    ) -> JobCreatedResponse:
        """Submit a job that captures raw HTML markup as an image (screenshot).

        ``options`` controls the output: ``format`` "png" (default) or
        "jpeg", ``full_page`` (default False - the visible area only),
        ``quality`` (JPEG only) and ``viewport`` (default 1024x768; width
        320-1920, height 320-8192). Poll with
        ``wait_for_job`` and fetch the bytes with ``download_pdf`` — for
        image jobs the download returns ``image/png`` or ``image/jpeg``
        instead of a PDF.

        ``delivery`` and ``test`` behave as on the PDF endpoints and cannot
        be combined with each other.
        """
        body = {"html": html}
        if webhook_url:
            body["webhook_url"] = webhook_url
        if options:
            body["options"] = _to_dict(options)
        if render:
            body["render"] = _to_dict(render)
        if delivery:
            body["delivery"] = _to_dict(delivery)
        if test:
            body["test"] = True

        extra_headers = _idempotency_header(idempotency_key)
        res = self._request("POST", "/html-to-image", json_data=body, extra_headers=extra_headers)
        return JobCreatedResponse.from_dict(res.json())

    def html_to_image_file(
        self,
        html: str,
        *,
        webhook_url: Optional[str] = None,
        options: Optional[ImageOptions] = None,
        render: Optional[RenderOptions] = None,
        idempotency_key: Optional[str] = None,
        test: Optional[bool] = None
    ) -> bytes:
        """Capture raw HTML as an image in ONE call and return the raw PNG or
        JPEG bytes (whichever ``options.format`` asked for).

        ``html_to_image`` sent with ``wait: true`` — same keyword arguments
        except ``delivery`` (the response is the delivery). See
        ``url_to_pdf_file`` for the synchronous contract: 503 ``SYNC_TIMEOUT``
        (job cancelled, not charged, never auto-resent), 422 with the job's own
        error code, and a clear error when an old server answers 202 JSON.
        """
        body = {"html": html}
        if webhook_url:
            body["webhook_url"] = webhook_url
        if options:
            body["options"] = _to_dict(options)
        if render:
            body["render"] = _to_dict(render)
        if test:
            body["test"] = True

        return self._request_file("/html-to-image", body, idempotency_key)

    def einvoice_to_pdf(
        self,
        xml: str,
        *,
        profile: EInvoiceProfile = EInvoiceProfile.EN16931,
        template_id: Optional[str] = None,
        template: Optional[dict] = None,
        webhook_url: Optional[str] = None,
        delivery: Optional[DeliveryOptions] = None,
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

        Pass ``delivery`` to upload the output straight to your own
        bucket via a presigned PUT URL: nothing is stored on PDFik's side,
        the download endpoint answers 404 for such jobs, and the
        ``job.finished`` webhook carries neither ``file_url`` nor
        ``expires_at`` (PDFik does not record where the file went — you
        already know). Not combinable with ``test``.

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
        if delivery:
            body["delivery"] = _to_dict(delivery)
        if test:
            body["test"] = True

        extra_headers = _idempotency_header(idempotency_key)
        res = self._request("POST", "/einvoice-to-pdf", json_data=body, extra_headers=extra_headers)
        return JobCreatedResponse.from_dict(res.json())

    def einvoice_to_pdf_file(
        self,
        xml: str,
        *,
        profile: EInvoiceProfile = EInvoiceProfile.EN16931,
        template_id: Optional[str] = None,
        template: Optional[dict] = None,
        webhook_url: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        test: Optional[bool] = None
    ) -> bytes:
        """Build a Factur-X e-invoice in ONE call and return the PDF/A-3 bytes.

        ``einvoice_to_pdf`` sent with ``wait: true`` — same keyword arguments
        except ``delivery`` (the response is the delivery). See
        ``url_to_pdf_file`` for the synchronous contract: 503 ``SYNC_TIMEOUT``
        (job cancelled, not charged, never auto-resent), 422 with the job's own
        error code, and a clear error when an old server answers 202 JSON.
        Note: the "minimum/basicwl is not a legally sufficient e-invoice" note
        that rides the async 202's ``detail`` arrives here as the
        ``X-PDFik-Warning`` response header, which this bytes-only helper does
        not surface — the bytes are the complete output either way.
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

        return self._request_file("/einvoice-to-pdf", body, idempotency_key)

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
            try:
                # throttle_waits=0: the waiting is done below, where `timeout` is
                # checked — the request path must not wait for the same 429 too.
                res = self._request("GET", f"/jobs/{job_id}", throttle_waits=0)
                job = JobStatusResponse.from_dict(res.json())
            except PdfikError as e:
                # A 429 while polling is a throttle, not a failure: on Free (10
                # requests/min) a render longer than ~20 s used to end the wait
                # with an error. Wait it out, within the same timeout.
                if e.status_code != 429:
                    raise
                if time.time() - start_time >= timeout:
                    raise PdfikError(f"Job {job_id} timed out", 408, "TIMEOUT")
                time.sleep(_throttle_delay(e, poll_interval))
                continue
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
        """Download the finished job's output bytes.

        Works for every job type: PDF jobs return the PDF, image jobs
        (``url_to_image`` / ``html_to_image``) return the raw PNG/JPEG bytes.
        Jobs submitted with ``delivery`` have no download here — the file
        lives in your own bucket, at the address you presigned, and this
        endpoint answers 404.
        """
        res = self._request("GET", f"/jobs/{job_id}/download", throttle_cases=_DOWNLOAD_THROTTLE_CASES)
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
    async def _request(self, method: str, path: str, json_data: Optional[dict] = None, extra_headers: Optional[dict] = None, throttle_waits: int = _THROTTLE_WAITS, throttle_cases: Tuple[str, ...] = _THROTTLE_CASES) -> httpx.Response:
        url = f"{self.base_url}{path}"
        headers = {
            "X-API-Key": self.api_key,
        }
        if extra_headers:
            headers.update(extra_headers)

        waits = 0
        while True:
            try:
                response = await self.client.request(method, url, headers=headers, json=json_data)
            except httpx.RequestError as exc:
                raise PdfikError(f"Connection failed: {exc}", 503)

            if not response.is_error:
                return response

            error = _error_from(response)
            # Same as the sync client: a throttle is waited out and the request resent.
            if not _is_throttled(error, throttle_cases) or waits >= throttle_waits:
                raise error
            waits += 1
            await asyncio.sleep(_throttle_delay(error, _RETRY_AFTER_FALLBACK, response.headers.get("Retry-After")))

    async def _request_once(self, method: str, path: str, json_data: Optional[dict] = None, extra_headers: Optional[dict] = None, throttle_waits: int = _THROTTLE_WAITS) -> httpx.Response:
        """One attempt, outside tenacity — same as the sync client's
        ``_request_once`` (see there for WHY a ``wait: true`` request must never
        be resent after a 5xx or a dropped connection, while throttle 429s are
        still waited out under one Idempotency-Key)."""
        url = f"{self.base_url}{path}"
        headers = {
            "X-API-Key": self.api_key,
        }
        if extra_headers:
            headers.update(extra_headers)

        waits = 0
        while True:
            try:
                response = await self.client.request(method, url, headers=headers, json=json_data, timeout=_SYNC_WAIT_TIMEOUT)
            except httpx.RequestError as exc:
                raise PdfikError(f"Connection failed: {exc}", 503)

            if not response.is_error:
                return response

            error = _error_from(response)
            if not _is_throttled(error) or waits >= throttle_waits:
                raise error
            waits += 1
            await asyncio.sleep(_throttle_delay(error, _RETRY_AFTER_FALLBACK, response.headers.get("Retry-After")))

    async def _request_file(self, path: str, body: dict, idempotency_key: Optional[str]) -> bytes:
        # Same as the sync client: `wait: true` makes the answer the file
        # itself (or an RFC 7807 problem), never a job id to poll.
        body["wait"] = True
        extra_headers = _idempotency_header(idempotency_key)
        res = await self._request_once("POST", path, json_data=body, extra_headers=extra_headers)
        return _sync_file_bytes(res)

    async def url_to_pdf(
        self,
        url: str,
        *,
        webhook_url: Optional[str] = None,
        options: Optional[PdfOptions] = None,
        render: Optional[RenderOptions] = None,
        auth: Optional[JobAuthOptions] = None,
        einvoice: Optional[EInvoiceOptions] = None,
        delivery: Optional[DeliveryOptions] = None,
        idempotency_key: Optional[str] = None,
        test: Optional[bool] = None
    ) -> JobCreatedResponse:
        """Submit a job that renders a public URL to a PDF.

        Pass ``einvoice`` to embed a Factur-X CII XML into the rendered PDF:
        the output is normalized to PDF/A-3 with the XML attached as
        ``factur-x.xml``. Not combinable with ``options.user_password`` or
        ``options.compression``.

        Pass ``delivery`` to upload the output straight to your own
        bucket via a presigned PUT URL: nothing is stored on PDFik's side,
        the download endpoint answers 404 for such jobs, and the
        ``job.finished`` webhook carries neither ``file_url`` nor
        ``expires_at`` (PDFik does not record where the file went — you
        already know). Not combinable with ``test``.

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
        if delivery:
            body["delivery"] = _to_dict(delivery)
        if test:
            body["test"] = True

        extra_headers = _idempotency_header(idempotency_key)
        res = await self._request("POST", "/url-to-pdf", json_data=body, extra_headers=extra_headers)
        return JobCreatedResponse.from_dict(res.json())

    async def url_to_pdf_file(
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
    ) -> bytes:
        """Render a public URL to a PDF in ONE call and return the PDF bytes.

        The coroutine twin of ``PdfikClient.url_to_pdf_file`` — see its
        docstring for the full synchronous contract: ``wait: true``, 45 s
        server budget, 503 ``SYNC_TIMEOUT`` (job cancelled, not charged, never
        auto-resent), 422 with the job's own error code, old-server 202 JSON
        detection, and no ``delivery`` (the response is the delivery).
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

        return await self._request_file("/url-to-pdf", body, idempotency_key)

    async def html_to_pdf(
        self,
        html: str,
        *,
        webhook_url: Optional[str] = None,
        options: Optional[PdfOptions] = None,
        render: Optional[RenderOptions] = None,
        einvoice: Optional[EInvoiceOptions] = None,
        delivery: Optional[DeliveryOptions] = None,
        idempotency_key: Optional[str] = None,
        test: Optional[bool] = None
    ) -> JobCreatedResponse:
        """Submit a job that renders raw HTML markup to a PDF.

        Pass ``einvoice`` to embed a Factur-X CII XML into the rendered PDF:
        the output is normalized to PDF/A-3 with the XML attached as
        ``factur-x.xml``. Not combinable with ``options.user_password`` or
        ``options.compression``.

        Pass ``delivery`` to upload the output straight to your own
        bucket via a presigned PUT URL: nothing is stored on PDFik's side,
        the download endpoint answers 404 for such jobs, and the
        ``job.finished`` webhook carries neither ``file_url`` nor
        ``expires_at`` (PDFik does not record where the file went — you
        already know). Not combinable with ``test``.

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
        if delivery:
            body["delivery"] = _to_dict(delivery)
        if test:
            body["test"] = True

        extra_headers = _idempotency_header(idempotency_key)
        res = await self._request("POST", "/html-to-pdf", json_data=body, extra_headers=extra_headers)
        return JobCreatedResponse.from_dict(res.json())

    async def html_to_pdf_file(
        self,
        html: str,
        *,
        webhook_url: Optional[str] = None,
        options: Optional[PdfOptions] = None,
        render: Optional[RenderOptions] = None,
        einvoice: Optional[EInvoiceOptions] = None,
        idempotency_key: Optional[str] = None,
        test: Optional[bool] = None
    ) -> bytes:
        """Render raw HTML to a PDF in ONE call and return the PDF bytes.

        The coroutine twin of ``PdfikClient.html_to_pdf_file``; the synchronous
        contract is described on ``PdfikClient.url_to_pdf_file``.
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

        return await self._request_file("/html-to-pdf", body, idempotency_key)

    async def markdown_to_pdf(
        self,
        markdown: str,
        *,
        webhook_url: Optional[str] = None,
        options: Optional[PdfOptions] = None,
        render: Optional[RenderOptions] = None,
        delivery: Optional[DeliveryOptions] = None,
        idempotency_key: Optional[str] = None,
        test: Optional[bool] = None
    ) -> JobCreatedResponse:
        """Submit a job that converts Markdown to a PDF.

        CommonMark plus GFM tables and strikethrough are supported; raw HTML
        inside the Markdown is escaped, not rendered — use ``html_to_pdf``
        for full HTML control. A built-in print stylesheet is applied, and
        ``options`` (paper format, margins, header/footer, watermark, ...)
        works exactly as on ``html_to_pdf``. Job polling and download are
        identical to the other PDF endpoints.

        ``delivery`` uploads the output straight to your own bucket
        via a presigned PUT URL; ``test=True`` runs the full pipeline without
        real rendering. The two cannot be combined.
        """
        body = {"markdown": markdown}
        if webhook_url:
            body["webhook_url"] = webhook_url
        if options:
            body["options"] = _to_dict(options)
        if render:
            body["render"] = _to_dict(render)
        if delivery:
            body["delivery"] = _to_dict(delivery)
        if test:
            body["test"] = True

        extra_headers = _idempotency_header(idempotency_key)
        res = await self._request("POST", "/markdown-to-pdf", json_data=body, extra_headers=extra_headers)
        return JobCreatedResponse.from_dict(res.json())

    async def markdown_to_pdf_file(
        self,
        markdown: str,
        *,
        webhook_url: Optional[str] = None,
        options: Optional[PdfOptions] = None,
        render: Optional[RenderOptions] = None,
        idempotency_key: Optional[str] = None,
        test: Optional[bool] = None
    ) -> bytes:
        """Convert Markdown to a PDF in ONE call and return the PDF bytes.

        The coroutine twin of ``PdfikClient.markdown_to_pdf_file``; the
        synchronous contract is described on ``PdfikClient.url_to_pdf_file``.
        """
        body = {"markdown": markdown}
        if webhook_url:
            body["webhook_url"] = webhook_url
        if options:
            body["options"] = _to_dict(options)
        if render:
            body["render"] = _to_dict(render)
        if test:
            body["test"] = True

        return await self._request_file("/markdown-to-pdf", body, idempotency_key)

    async def url_to_image(
        self,
        url: str,
        *,
        webhook_url: Optional[str] = None,
        options: Optional[ImageOptions] = None,
        render: Optional[RenderOptions] = None,
        auth: Optional[JobAuthOptions] = None,
        delivery: Optional[DeliveryOptions] = None,
        idempotency_key: Optional[str] = None,
        test: Optional[bool] = None
    ) -> JobCreatedResponse:
        """Submit a job that captures a public URL as an image (screenshot).

        ``options`` controls the output: ``format`` "png" (default) or
        "jpeg", ``full_page`` (default False - the visible area only),
        ``quality`` (JPEG only) and ``viewport`` (default 1024x768; width
        320-1920, height 320-8192). Poll with
        ``wait_for_job`` and fetch the bytes with ``download_pdf`` — for
        image jobs the download returns ``image/png`` or ``image/jpeg``
        instead of a PDF.

        ``auth`` (Pro+) attaches basic/bearer credentials exactly as on
        ``url_to_pdf``. ``delivery`` and ``test`` behave as on the PDF
        endpoints and cannot be combined with each other.
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
        if delivery:
            body["delivery"] = _to_dict(delivery)
        if test:
            body["test"] = True

        extra_headers = _idempotency_header(idempotency_key)
        res = await self._request("POST", "/url-to-image", json_data=body, extra_headers=extra_headers)
        return JobCreatedResponse.from_dict(res.json())

    async def url_to_image_file(
        self,
        url: str,
        *,
        webhook_url: Optional[str] = None,
        options: Optional[ImageOptions] = None,
        render: Optional[RenderOptions] = None,
        auth: Optional[JobAuthOptions] = None,
        idempotency_key: Optional[str] = None,
        test: Optional[bool] = None
    ) -> bytes:
        """Capture a public URL as an image in ONE call and return the raw
        PNG or JPEG bytes.

        The coroutine twin of ``PdfikClient.url_to_image_file``; the
        synchronous contract is described on ``PdfikClient.url_to_pdf_file``.
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
        if test:
            body["test"] = True

        return await self._request_file("/url-to-image", body, idempotency_key)

    async def html_to_image(
        self,
        html: str,
        *,
        webhook_url: Optional[str] = None,
        options: Optional[ImageOptions] = None,
        render: Optional[RenderOptions] = None,
        delivery: Optional[DeliveryOptions] = None,
        idempotency_key: Optional[str] = None,
        test: Optional[bool] = None
    ) -> JobCreatedResponse:
        """Submit a job that captures raw HTML markup as an image (screenshot).

        ``options`` controls the output: ``format`` "png" (default) or
        "jpeg", ``full_page`` (default False - the visible area only),
        ``quality`` (JPEG only) and ``viewport`` (default 1024x768; width
        320-1920, height 320-8192). Poll with
        ``wait_for_job`` and fetch the bytes with ``download_pdf`` — for
        image jobs the download returns ``image/png`` or ``image/jpeg``
        instead of a PDF.

        ``delivery`` and ``test`` behave as on the PDF endpoints and cannot
        be combined with each other.
        """
        body = {"html": html}
        if webhook_url:
            body["webhook_url"] = webhook_url
        if options:
            body["options"] = _to_dict(options)
        if render:
            body["render"] = _to_dict(render)
        if delivery:
            body["delivery"] = _to_dict(delivery)
        if test:
            body["test"] = True

        extra_headers = _idempotency_header(idempotency_key)
        res = await self._request("POST", "/html-to-image", json_data=body, extra_headers=extra_headers)
        return JobCreatedResponse.from_dict(res.json())

    async def html_to_image_file(
        self,
        html: str,
        *,
        webhook_url: Optional[str] = None,
        options: Optional[ImageOptions] = None,
        render: Optional[RenderOptions] = None,
        idempotency_key: Optional[str] = None,
        test: Optional[bool] = None
    ) -> bytes:
        """Capture raw HTML as an image in ONE call and return the raw PNG or
        JPEG bytes.

        The coroutine twin of ``PdfikClient.html_to_image_file``; the
        synchronous contract is described on ``PdfikClient.url_to_pdf_file``.
        """
        body = {"html": html}
        if webhook_url:
            body["webhook_url"] = webhook_url
        if options:
            body["options"] = _to_dict(options)
        if render:
            body["render"] = _to_dict(render)
        if test:
            body["test"] = True

        return await self._request_file("/html-to-image", body, idempotency_key)

    async def einvoice_to_pdf(
        self,
        xml: str,
        *,
        profile: EInvoiceProfile = EInvoiceProfile.EN16931,
        template_id: Optional[str] = None,
        template: Optional[dict] = None,
        webhook_url: Optional[str] = None,
        delivery: Optional[DeliveryOptions] = None,
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

        Pass ``delivery`` to upload the output straight to your own
        bucket via a presigned PUT URL: nothing is stored on PDFik's side,
        the download endpoint answers 404 for such jobs, and the
        ``job.finished`` webhook carries neither ``file_url`` nor
        ``expires_at`` (PDFik does not record where the file went — you
        already know). Not combinable with ``test``.

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
        if delivery:
            body["delivery"] = _to_dict(delivery)
        if test:
            body["test"] = True

        extra_headers = _idempotency_header(idempotency_key)
        res = await self._request("POST", "/einvoice-to-pdf", json_data=body, extra_headers=extra_headers)
        return JobCreatedResponse.from_dict(res.json())

    async def einvoice_to_pdf_file(
        self,
        xml: str,
        *,
        profile: EInvoiceProfile = EInvoiceProfile.EN16931,
        template_id: Optional[str] = None,
        template: Optional[dict] = None,
        webhook_url: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        test: Optional[bool] = None
    ) -> bytes:
        """Build a Factur-X e-invoice in ONE call and return the PDF/A-3 bytes.

        The coroutine twin of ``PdfikClient.einvoice_to_pdf_file`` (including
        the ``X-PDFik-Warning`` note there); the synchronous contract is
        described on ``PdfikClient.url_to_pdf_file``.
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

        return await self._request_file("/einvoice-to-pdf", body, idempotency_key)

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
            try:
                res = await self._request("GET", f"/jobs/{job_id}", throttle_waits=0)
                job = JobStatusResponse.from_dict(res.json())
            except PdfikError as e:
                # Same as the sync client: a 429 while polling is waited out here,
                # not in the request path.
                if e.status_code != 429:
                    raise
                if time.time() - start_time >= timeout:
                    raise PdfikError(f"Job {job_id} timed out", 408, "TIMEOUT")
                await asyncio.sleep(_throttle_delay(e, poll_interval))
                continue
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
        """Download the finished job's output bytes.

        Works for every job type: PDF jobs return the PDF, image jobs
        (``url_to_image`` / ``html_to_image``) return the raw PNG/JPEG bytes.
        Jobs submitted with ``delivery`` have no download here — the file
        lives in your own bucket, at the address you presigned, and this
        endpoint answers 404.
        """
        res = await self._request("GET", f"/jobs/{job_id}/download", throttle_cases=_DOWNLOAD_THROTTLE_CASES)
        return res.content

    async def close(self):
        await self.client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
