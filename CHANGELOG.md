# Changelog

All notable changes to `pdfik` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

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
