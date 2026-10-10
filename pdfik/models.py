from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Any, Dict, Type, TypeVar, get_type_hints

T = TypeVar('T')

class PaperFormat(str, Enum):
    A0 = "A0"
    A1 = "A1"
    A2 = "A2"
    A3 = "A3"
    A4 = "A4"
    A5 = "A5"
    A6 = "A6"
    LETTER = "Letter"
    LEGAL = "Legal"
    TABLOID = "Tabloid"
    LEDGER = "Ledger"

class WaitUntilEvent(str, Enum):
    LOAD = "load"
    DOM_CONTENT_LOADED = "domcontentloaded"
    NETWORK_IDLE = "networkidle"
    COMMIT = "commit"

class JobStatus(str, Enum):
    QUEUED = "queued"
    RENDERING = "rendering"
    UPLOADING = "uploading"
    DONE = "done"
    FAILED = "failed"

class EInvoiceProfile(str, Enum):
    # 'minimum' and 'basicwl' carry accompanying data only and are NOT a
    # legally sufficient e-invoice; use basic/en16931/extended for a complete
    # legal invoice.
    MINIMUM = "minimum"
    BASICWL = "basicwl"
    BASIC = "basic"
    EN16931 = "en16931"
    EXTENDED = "extended"

def _from_dict(cls: Type[T], data: Any) -> Any:
    if data is None:
        return None
    if not isinstance(data, dict):
        return data
    
    # Get class type hints
    try:
        type_hints = get_type_hints(cls)
    except Exception:
        type_hints = {}

    kwargs = {}
    for name, field_type in type_hints.items():
        if name in data:
            val = data[name]
            # Handle list of nested models if any (currently none, but good to have)
            # Handle nested dataclasses
            if hasattr(field_type, '__dataclass_fields__'):
                kwargs[name] = _from_dict(field_type, val)
            # Handle Optional[dataclass]
            elif hasattr(field_type, '__args__'):
                # Extract the inner type from Union/Optional
                args = field_type.__args__
                inner_type = next((a for a in args if hasattr(a, '__dataclass_fields__')), None)
                if inner_type and val is not None:
                    kwargs[name] = _from_dict(inner_type, val)
                else:
                    kwargs[name] = val
            else:
                kwargs[name] = val
    
    return cls(**kwargs)

def _to_dict(obj: Any) -> Any:
    if hasattr(obj, '__dataclass_fields__'):
        res = {}
        for name in obj.__dataclass_fields__:
            val = getattr(obj, name)
            if val is not None:
                res[name] = _to_dict(val)
        return res
    elif isinstance(obj, Enum):
        return obj.value
    elif isinstance(obj, list):
        return [_to_dict(x) for x in obj]
    elif isinstance(obj, dict):
        return {k: _to_dict(v) for k, v in obj.items() if v is not None}
    return obj

@dataclass
class MarginOptions:
    top: Optional[str] = None
    right: Optional[str] = None
    bottom: Optional[str] = None
    left: Optional[str] = None

@dataclass
class WatermarkOptions:
    text: str
    color: Optional[str] = None
    font_size: Optional[str] = None
    rotation_degrees: Optional[int] = None

@dataclass
class CompressionOptions:
    level: Optional[int] = None
    image_quality: Optional[int] = None

@dataclass
class PdfOptions:
    format: Optional[PaperFormat] = None
    landscape: Optional[bool] = None
    margin: Optional[MarginOptions] = None
    print_background: Optional[bool] = None
    display_header_footer: Optional[bool] = None
    header_template: Optional[str] = None
    footer_template: Optional[str] = None
    watermark: Optional[WatermarkOptions] = None
    user_password: Optional[str] = None
    compression: Optional[CompressionOptions] = None
    scale: Optional[float] = None

@dataclass
class RenderOptions:
    page_load_timeout_ms: Optional[int] = None
    wait_until: Optional[WaitUntilEvent] = None
    wait_for_selector: Optional[str] = None
    wait_after_load_ms: Optional[int] = None  # Additional delay in ms (0 to 10000)

    def __post_init__(self):
        if self.wait_after_load_ms is not None and not (0 <= self.wait_after_load_ms <= 10000):
            raise ValueError("wait_after_load_ms must be between 0 and 10000 ms")

@dataclass
class JobAuthOptions:
    type: str
    value: str

@dataclass
class ImageViewport:
    # The browser window the page is opened in, in CSS pixels. You choose the
    # size and the server captures exactly that window — nothing is scaled or
    # fitted. width: 320-1920, height: 320-8192 (outside the range: 422).
    width: int
    height: int

@dataclass
class ImageOptions:
    # Screenshot output options for url_to_image / html_to_image.
    # format: "png" (default) or "jpeg". full_page captures the whole
    # scrollable page instead of just the visible area; it is False by default,
    # so the image is exactly your viewport. With True the height follows the
    # real page and is clipped at 8192 px — a ceiling, not a target, so a
    # 2,000 px page still gives a 2,000 px image. quality is JPEG-only, 1-100
    # — the server rejects quality together with png (422). viewport defaults
    # to 1024x768 server-side; it sets the image width, and with
    # full_page=False (the default) also its height.
    # The format/full_page defaults are sent on the wire (only None is
    # dropped), which matches the server defaults exactly.
    format: str = "png"
    full_page: bool = False
    quality: Optional[int] = None
    viewport: Optional[ImageViewport] = None

@dataclass
class DeliveryOptions:
    # BYOB delivery (every plan): the rendered output is uploaded straight to your
    # own bucket via this presigned HTTPS PUT URL; nothing is stored on
    # PDFik's side. Presign for at least 15 minutes and without a
    # Content-Type condition (the upload sends none). For delivered jobs the
    # job.finished webhook carries neither file_url nor expires_at (PDFik does
    # not record the destination), and the download endpoint answers 404.
    # Not combinable with test mode.
    url: str
    # Only "presigned_put" exists; sent explicitly on the wire.
    mode: str = "presigned_put"

@dataclass
class EInvoiceOptions:
    # Attach a Factur-X e-invoice to a url_to_pdf/html_to_pdf render: the
    # rendered page becomes the human-readable half, the output is normalized
    # to PDF/A-3 and the XML is embedded as factur-x.xml. Mutually exclusive
    # with PdfOptions.user_password (PDF/A forbids encryption) and
    # PdfOptions.compression (re-saving breaks the PDF/A attributes).
    # UN/CEFACT Cross-Industry-Invoice XML (UTF-8, up to 1 MB), validated
    # server-side against the official XSD of the declared profile
    xml: str
    profile: EInvoiceProfile = EInvoiceProfile.EN16931
    # E-invoice container format; only "factur-x" is supported
    format: str = "factur-x"

@dataclass
class JobCreatedResponse:
    job_id: str
    status: str
    detail: str

    @classmethod
    def from_dict(cls: Type[T], data: dict) -> T:
        return _from_dict(cls, data)

@dataclass
class JobMetrics:
    file_size_bytes: Optional[int] = None
    file_size_human: Optional[str] = None
    page_load_ms: Optional[int] = None
    # PDFik processing time (pdf generation, watermark, compression, encryption,
    # upload) — excludes page load and client-requested waits
    processing_ms: Optional[int] = None
    total_duration_ms: Optional[int] = None
    page_count: Optional[int] = None

@dataclass
class JobStatusResponse:
    status: JobStatus
    created_at: Optional[str] = None
    finished_at: Optional[str] = None
    # ISO-8601 UTC timestamp after which the file can no longer be downloaded
    # (finished_at + retention window, currently 24h); None until finished
    expires_at: Optional[str] = None
    pages_count: Optional[int] = None
    error_code: Optional[str] = None
    metrics: Optional[JobMetrics] = None
    # True when the job was submitted in test mode (no real rendering;
    # download returns a sample PDF)
    test: Optional[bool] = None

    @classmethod
    def from_dict(cls: Type[T], data: dict) -> T:
        return _from_dict(cls, data)

@dataclass
class JobFileResponse:
    job_id: str
    download_url: str
    expires_in_seconds: int

    @classmethod
    def from_dict(cls: Type[T], data: dict) -> T:
        return _from_dict(cls, data)
