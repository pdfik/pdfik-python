# Changelog

All notable changes to `pdfik` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [0.7.1] — 2026-11-10

### Fixed
- `verify_webhook_signature` returns `False` for an empty secret and for a `t` that is not
  plain ASCII digits; spaces around header tokens and tokens without `=` no longer reject
  a genuine delivery.
- The throttle waits of one call are counted once: a `5xx` resend used to restart them
  (up to 15 waits, about 15 minutes, in one call).
- An API key with surrounding whitespace, a line break, a NUL or a non-ASCII character is
  refused at construction, and the message never contains the key (httpx's error did).
- `download_pdf` is not resent: each started stream is one of the file's three attempts.
- The `429` volume quota held by jobs that are still running is waited out like the
  request-rate throttle.

## [0.7.0] — 2026-11-09

### Fixed
- `PdfikClient.verify_webhook_signature` checks every `v1` in `X-PDFik-Signature`. While
  the overlap window of a secret rotation is open, PDFik signs each delivery with both
  secrets and sends both signatures in the one header, the current secret's first:
  `t=<unix seconds>,v1=<signature with the new secret>,v1=<signature with the previous secret>`.
  The helper now accepts the delivery if any `v1` matches the secret you pass, comparing
  each one in constant time. Up to 0.6.0 it kept only the last `v1` — inside a window,
  the previous secret's — so a receiver already switched to the new secret would reject
  every genuine delivery until the window closed: upgrade before you rotate with an
  overlap window. A header with a single `v1`, the ±300 s timestamp rule and the `bool`
  result are unchanged.
- A connection failure — no HTTP answer at all (DNS failure, refused or reset
  connection, TLS error, timeout) or a connection that breaks while the body is being
  read — now raises `PdfikError` with `error_code == "NETWORK_ERROR"` as well as
  `status_code == 503`, the same code the JavaScript, Java and C# clients report. It
  used to carry no `error_code`, so code that branches on the code could not tell it
  apart from a server's own `503`. Retries are unchanged: the ordinary calls resend it
  like a `5xx` (a resent create keeps its `Idempotency-Key`), and the `*_file` methods
  raise it after their one attempt.
- `verify_webhook_signature` returns `False` when `headers` is `None` or not a mapping, as
  the JavaScript, Java and C# helpers do; it used to raise `AttributeError`.

### Changed
- `PdfikClient` and `AsyncPdfikClient` raise `ValueError` for a `base_url` that is not an
  absolute http(s) URL (`api.pdfik.net` without `https://`, or `localhost:8000`). Up to
  0.6.0 such a client was created and every call then failed with a `503`
  "Connection failed: …" — the ordinary calls after three attempts, the `*_file`
  calls after their one.
- A header value httpx refuses to send — a line break, or a leading or trailing space, in
  the API key or `idempotency_key` — raises httpx's own `LocalProtocolError` at once. Up
  to 0.6.0 it became a `503` "Connection failed" that the ordinary calls resent twice,
  although nothing had been sent; it is not a `NETWORK_ERROR`.
- README: "Handling Webhooks" says deliveries are signed once a signing secret has been
  generated (unsigned until then) and describes the signature header, the ±300 s rule and
  the two signatures of a rotation window (it used to advise checking the new secret and
  then the old one yourself); the Auto Retry line names the `NETWORK_ERROR` code.
- Version `0.6.0` → `0.7.0` (`pyproject.toml`).

## [0.6.0] — 2026-11-01

No code changes.

### Changed
- Supported Python is now **3.13+** — the latest stable interpreter with current
  security fixes, the same version the PDFik platform itself runs on. Older
  interpreters (3.8–3.12) are no longer promised or tested; on 3.13 nothing changes.

## [0.5.0] — 2026-10-20

The synchronous-mode release: one call in, the file out. Everything that exists runs
unchanged — the new methods sit next to the job-creating ones, they do not replace them.

### Added
- `url_to_pdf_file`, `html_to_pdf_file`, `markdown_to_pdf_file`, `url_to_image_file`,
  `html_to_image_file` and `einvoice_to_pdf_file` on both `PdfikClient` and
  `AsyncPdfikClient`. Each takes the same keyword arguments as its job-creating sibling
  except `delivery` (the response already is the delivery), sends the request with
  `"wait": true` and returns the output **bytes** — PDF, PNG or JPEG — in one call,
  with no polling and no webhook. The API holds the request for up to 45 seconds; the
  client gives the whole exchange its own 70-second per-request budget (10 s to
  connect), independent of the clients' default timeout, which would otherwise cut a
  held request off after 5 seconds. The file also stays downloadable for the normal
  24 h under the job id named by the `X-PDFik-Job-Id` response header.
- When the render does not finish inside the 45-second budget, the API answers `503`
  ([sync-timeout](https://docs.pdfik.net/error-codes#sync-timeout)) and the method
  raises `PdfikError` with `error_code` `SYNC_TIMEOUT` **without resending the
  request**: the job was cancelled and not charged, and its `Idempotency-Key` was
  released, so calling the method again is safe — but it is deliberately your call,
  not the SDK's. More generally, a `*_file` request is never resent after a `5xx` or
  a dropped connection (unlike every other call): by the time such an answer arrives
  the held render may have finished and been charged, and there is no job id in hand
  to deduplicate a resend against. Rate-limit and concurrency `429`s are still waited
  out exactly as on the create methods, under one `Idempotency-Key` per call.
- A failed render answers `422`
  ([job-failed](https://docs.pdfik.net/error-codes#job-failed)) and raises `PdfikError`
  whose `error_code` is the job's own code (`PAGE_TIMEOUT`, `SSRF_BLOCKED`,
  `FILE_TOO_LARGE`, …), after exactly one request.
- Old-server detection: a server that does not know `wait` ignores it and answers the
  ordinary `202` JSON. The `*_file` methods detect that (a `202`, or JSON on any `2xx`)
  and raise `PdfikError` (`SYNC_MODE_UNSUPPORTED`) naming the created job's id instead
  of handing the JSON back as file bytes — that job renders normally and can still be
  fetched with `wait_for_job` + `download_pdf`.
- README: the quick start now opens with the one-call flow; the asynchronous
  submit/poll/webhook flow stays below for batches and renders that may take longer
  than 45 seconds.
- Tests: the one-call happy path (sync and async), `SYNC_TIMEOUT` and a plain `500`
  raised after exactly one request, the job's own error code on `422`, the old-server
  `202` detection, the 70 s per-request timeout on the wire, and — for all six
  methods — `wait: true` on the wire, no `delivery` field, and the same
  `Idempotency-Key` across a throttle resend.

### Changed
- Version `0.4.2` → `0.5.0` (`pyproject.toml`).

## [0.4.2] — 2026-10-12

A bug-fix release: no new options and no signature changes. Code written against 0.4.1
runs unchanged, and `5xx` responses and connection failures are retried exactly as
before (three attempts with exponential backoff).

### Fixed
- A rate-limit `429` on a create call, on `get_job` or on `download_pdf` (sync and async)
  is now waited out the way it already was while polling: the client sleeps for the time
  the API states (`retry_after_seconds`, else the `Retry-After` header, else 10 s; never
  more than 60 s) and sends the request again — up to five times in a row, after which
  the `429` is raised as before. The in-flight job cap (`CONCURRENCY_LIMIT_EXCEEDED`) is
  handled the same way. Such a `429` used to get three attempts within about three
  seconds and then fail, so on Free (10 requests/min) a program that created a job,
  polled and downloaded could fail at the download, or at the create call, whenever the
  per-minute window was full.
- Quota `429`s are no longer retried: the monthly byte quota (`BYTE_QUOTA_EXCEEDED`), the
  test-mode caps (`TEST_RATE_LIMIT_EXCEEDED`) and an exhausted download counter
  ([download-attempts-exhausted](https://docs.pdfik.net/error-codes#download-attempts-exhausted))
  are raised at once. Waiting a few seconds cannot clear them, and each used to cost
  three requests.
- `download_pdf` (sync and async) waits out a busy file
  ([download-busy](https://docs.pdfik.net/error-codes#download-busy)): the `429` the API
  answers while another stream of the same file is still open. It clears by itself within
  seconds and costs no download attempt, so the client sleeps the time the `Retry-After`
  header names (10 s; same fallback and 60 s cap as above) and asks again. These waits
  count toward the same five in a row; after them the `429` is raised.
- A retried create call can no longer produce a second job. Every create method
  (`url_to_pdf`, `html_to_pdf`, `markdown_to_pdf`, `url_to_image`, `html_to_image`,
  `einvoice_to_pdf`; sync and async) now always sends an `Idempotency-Key` header: the
  `idempotency_key=` you pass, unchanged, or — when you pass none — one random UUID
  generated per call and repeated on every resend of that call, after a `5xx` and after
  a waited-out `429` alike. The API answers a repeated key with the original job. Before,
  when a `5xx` or a dropped connection reached the client after the API had already
  created the job, the request was sent again without a key, and the account got two
  jobs and was charged twice. `get_job`, `wait_for_job` and `download_pdf` send no such
  header.
- `wait_for_job` spends one request on a throttled poll, not three, and then waits the
  stated time once.

### Changed
- Version `0.4.1` → `0.4.2` (`pyproject.toml`).

## [0.4.1] — 2026-10-10

A documentation-only release: no API or behaviour change in the client. Code written
against 0.4.0 runs unchanged.

### Changed
- BYOB `delivery` is available on every plan, Free included — a server-side change of
  2026-10-09; it used to be Pro+. The README section "Deliver to your own bucket (BYOB)"
  and the comment on `DeliveryOptions` now say "every plan", and the docstrings of
  `url_to_pdf`, `html_to_pdf`, `markdown_to_pdf` and `einvoice_to_pdf` (sync and async)
  no longer say "Pro+". The 0.4.0 entry below describes the gate as it was at the time:
  the API no longer answers `402 PLAN_UPGRADE_REQUIRED` with `feature: "delivery"` on
  Free/Starter.
- Version `0.4.0` → `0.4.1` (`pyproject.toml`).

## [0.4.0] — 2026-09-19

The screenshots + Markdown + BYOB delivery release.

### Added
- `PdfikClient.markdown_to_pdf(markdown, *, webhook_url=None, options=None, render=None,
  delivery=None, idempotency_key=None, test=None)` and the same coroutine on
  `AsyncPdfikClient` — call `POST /markdown-to-pdf`. CommonMark plus GFM tables and
  strikethrough; raw HTML inside the Markdown is escaped by the API, never rendered (use
  `html_to_pdf` for full HTML control), and a built-in print stylesheet is applied.
  `options`/`render` carry the same objects and plan gates as `html_to_pdf`. Available on
  every plan, Free included.
- `url_to_image(url, ...)` and `html_to_image(html, ...)` on both clients — call
  `POST /url-to-image` / `POST /html-to-image` and produce PNG or JPEG screenshots
  through the same job flow. New `ImageOptions` dataclass: `format` (`"png"` default |
  `"jpeg"`; the API also accepts `"jpg"` as an alias), `full_page` (default `False`, sent
  on the wire — the visible area only; `True` follows the real page, clipped at
  8,192 px), `quality` (1–100, jpeg only — the API answers `422` when sent with png),
  `viewport` (`ImageViewport(width, height)`, width 320–1920, height 320–8192 CSS px,
  server default 1024×768). `url_to_image` supports the Pro+ `auth` option like `url_to_pdf`.
  `download_pdf` returns the image bytes unchanged (`Content-Type: image/png` /
  `image/jpeg`, filename `{job_id}.png` / `.jpg`); test mode returns the bundled sample
  PNG for either format. One screenshot consumes one render unit plus its bytes, same
  quotas as PDFs.
- `delivery=` keyword (`DeliveryOptions(url, mode="presigned_put")` — an https presigned
  PUT URL for your own bucket; Pro+ plans) on `url_to_pdf`, `html_to_pdf`,
  `einvoice_to_pdf`, `markdown_to_pdf`, `url_to_image` and `html_to_image`, sync and
  async. The rendered output is uploaded straight to your bucket and nothing is stored
  on PDFik's side: the `job.finished` webhook reports the outcome only — no `file_url`
  and no `expires_at`, because the presigned URL is a credential and is never recorded;
  `GET /jobs/{id}/download` answers `404 #output-delivered-externally`.
  Presign for at least 15 minutes and without a Content-Type condition. Not combinable
  with `test` (`400`); on Free/Starter the API answers `402 PLAN_UPGRADE_REQUIRED` with
  `feature: "delivery"`.
- `ImageOptions`, `ImageViewport` and `DeliveryOptions`, exported from `pdfik`.
- README: "Markdown to PDF", "Screenshots" and "Deliver to your own bucket (BYOB)"
  sections.
- Tests: exact request bodies for the three new methods and the `delivery` object on all
  five creation methods, sync and async.

### Changed
- Version `0.3.0` → `0.4.0` (`pyproject.toml`).

### Fixed
- `wait_for_job` (sync and async) waits out an HTTP 429 instead of failing: it sleeps the
  API's `retry_after_seconds` (capped at 60 s) and keeps polling within its `timeout`. On
  Free (10 requests/min) a render longer than ~20 s used to end the wait with an error.
- `JobAuthOptions` is exported from the package root: `from pdfik import JobAuthOptions`
  raised `ImportError` although `auth=` is a public parameter of `url_to_pdf` /
  `url_to_image` (only `pdfik.models` had it).
- Request-validation errors (HTTP 422) now carry a readable message such as
  `options.viewport.width: Input should be less than or equal to 1920`. The API sends
  `detail` as a list for these, and `PdfikError` used to show that list's repr.
- Docstrings of `delivery` and `download_pdf` no longer claim that the `job.finished`
  webhook's `file_url` points into your bucket: for a delivered job the webhook carries
  neither `file_url` nor `expires_at`, and PDFik keeps no record of the destination.

## [0.3.0] — 2026-09-06

The e-invoicing (Factur-X) release. Everything below is what changed in the SDK
after 0.2.2 was published, according to git.

### Added
- `PdfikClient.einvoice_to_pdf(xml, *, profile=EInvoiceProfile.EN16931, template_id=None,
  template=None, webhook_url=None, idempotency_key=None, test=None)` and the same
  coroutine on `AsyncPdfikClient` — call `POST /einvoice-to-pdf`. Send UN/CEFACT
  Cross-Industry Invoice XML (the Factur-X payload, UTF-8, up to 1 MB) and get the usual
  `JobCreatedResponse` back (`202`, `status == "queued"`); the finished job is a PDF/A-3
  with the XML embedded as `factur-x.xml` (Factur-X / ZUGFeRD), fetched with
  `wait_for_job` + `download_pdf` as for any other job. `profile` accepts the
  `EInvoiceProfile` enum or its plain string value and is always sent (default
  `en16931`); `template_id` (a template saved on the dashboard) and `template` (an inline
  block-template dict — the same snake_case JSON the dashboard editor produces) are
  mutually exclusive, omit both to use the account's default template;
  `idempotency_key` is sent as the `Idempotency-Key` header. Available on every plan, Free included.
- `einvoice=` keyword on `url_to_pdf` / `html_to_pdf` (sync and async) taking
  `EInvoiceOptions(xml, profile=EInvoiceProfile.EN16931, format="factur-x")` — wraps the
  caller's own page: the render becomes the visual half and the output is normalized to
  PDF/A-3 with the XML embedded. Not combinable with `options.user_password` or
  `options.compression` (PDF/A forbids encryption, and re-saving breaks PDF/A attributes).
- New error responses, all raised as `PdfikError` and never retried:
  `422 EINVOICE_XML_INVALID` (malformed or unsafe XML, a guideline URN that does not match the declared profile, an XSD failure, or XML over 1 MB),
  `422 EINVOICE_OPTIONS_CONFLICT` (`template_id` together with `template`, or `einvoice`
  with `options.user_password` / `options.compression`), `404 EINVOICE_TEMPLATE_NOT_FOUND` (unknown
  `template_id`).
- `EInvoiceProfile` (str enum: `MINIMUM`, `BASICWL`, `BASIC`, `EN16931`, `EXTENDED`) and
  the `EInvoiceOptions` dataclass, exported from `pdfik`.
- `PaperFormat` now lists all 11 formats the renderer accepts: `A0`–`A6`, `Letter`,
  `Legal`, `Tabloid`, `Ledger` (new members: `A0`, `A1`, `A2`, `A5`, `A6`, `LEDGER`).
- README: "Factur-X e-invoices" section covering both flows and the e-invoicing error
  codes.
- Tests: `einvoice_to_pdf` request body and `Idempotency-Key`, inline `template` + test
  mode, the `einvoice=` option on both endpoints, and the async variant.
- `LICENSE` file (MIT) added to the project root.

### Fixed
- `PdfikError.error_code` was always `None` for API errors: pdf-api's RFC 7807 bodies
  carry the code in `error` (`QUOTA_EXCEEDED`, `PLAN_UPGRADE_REQUIRED`,
  `EINVOICE_XML_INVALID`, …) while the client read `error_code`. It now reads `error`
  first and falls back to `error_code`.

### Changed
- Request validation on the API side (no client code change, but new errors to expect):
  an unknown `PdfOptions.format`, a margin that is not exactly one CSS length per side
  (shorthands such as `"10mm 5mm"`), or an `auth.type` other than `basic` / `bearer`
  (and a `basic` value without `username:password`) are rejected with
  `422 VALIDATION_ERROR` before a job is created or any quota is charged. The SDK does
  not retry `4xx`.
- `pyproject.toml`: `Repository` and `Issues` project URLs added (PyPI links to
  github.com/pdfik/pdfik-python); README intro now describes PDFik as the asynchronous
  URL/HTML-to-PDF API.
- Version `0.2.2` → `0.3.0` (`pyproject.toml`).

## [0.2.2] — 2026-08-07

The version on PyPI when this changelog was introduced (build of 2026-08-07). Earlier
changes live in git history.
