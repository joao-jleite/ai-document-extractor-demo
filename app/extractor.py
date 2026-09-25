"""Claude API call: PDF/image in, `ExtractedDocument` out (structured output).

- PDFs go as a native `document` block (Claude reads text + layout + images).
- JPG/PNG/WEBP go as an `image` block (vision), after EXIF-rotation and resizing.
- `client.messages.parse(output_format=WireDocument)` constrains the answer to
  our JSON schema and validates it with Pydantic; `to_document()` then converts
  it to `ExtractedDocument` (see app/schema.py for why the two differ).
- Errors: invalid files are rejected before any API call; timeouts, network
  errors, 429/5xx and invalid/truncated responses are retried ONCE; auth/model
  errors fail fast with a clear message.
"""

from __future__ import annotations

import base64
import io
import time
from dataclasses import dataclass, field

import anthropic
import pydantic
from PIL import Image, ImageOps, UnidentifiedImageError

from .config import settings
from .schema import ExtractedDocument, WireDocument

MAX_IMAGE_EDGE = 2400          # px; larger photos are downscaled before upload
MAX_IMAGE_BYTES = 4_500_000    # API limit is 5 MB per image


class InvalidFileError(ValueError):
    """The uploaded file cannot be processed (wrong type, corrupt, too large)."""


class ExtractionError(RuntimeError):
    """The model call failed after retries (or failed in a non-retryable way)."""

    def __init__(self, message: str, *, status_code: int = 502, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


SYSTEM_PROMPT = """You are a data-extraction engine for procurement documents: Brazilian NF-e \
(DANFE), purchase orders and commercial invoices, written in Portuguese, Spanish or English. \
The input may be a clean PDF or a phone photo of a printed page.

Rules:
- Transcribe what is PRINTED. Never correct arithmetic, never compute a missing value, never \
"fix" a total that looks wrong - downstream validation needs the document exactly as printed.
- If a field is not printed or not legible, return an empty string for text fields and null \
for numbers (and mention it in extraction_notes when it should have been there).
- Numbers: convert locale formats to plain numbers. "1.234,56" -> 1234.56 (pt/es). \
Chilean pesos have no decimals: "1.234.567" -> 1234567.
- Dates: YYYY-MM-DD.
- Currency: ISO 4217 code. A DANFE is always BRL.
- supplier = the seller (NF-e emitente, PO proveedor/fornecedor/vendor). \
buyer = the purchaser (NF-e destinatario, the company that issues the PO).
- Keep tax IDs exactly as printed, including dots, slashes and dashes.
- Ignore watermarks, stamps and banners such as "DEMO"; they are not document data.
- extraction_notes: short notes about unreadable or ambiguous spots only."""

USER_PROMPT = "Extract the data of this document."


@dataclass
class PreparedInput:
    kind: str                # "document" | "image"
    media_type: str
    data_b64: str
    notes: list[str] = field(default_factory=list)


@dataclass
class ExtractionResult:
    document: ExtractedDocument
    model: str
    attempts: int
    seconds: float
    usage: dict
    stop_reason: str | None


# ---------------------------------------------------------------------------
# Input handling
# ---------------------------------------------------------------------------

def sniff_media_type(data: bytes) -> str | None:
    """Detect the file type from magic bytes (never trust the extension)."""
    if data[:5] == b"%PDF-":
        return "application/pdf"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def prepare_input(data: bytes, filename: str = "upload") -> PreparedInput:
    if not data:
        raise InvalidFileError(f"'{filename}' is empty.")
    max_bytes = int(settings.max_upload_mb * 1024 * 1024)
    if len(data) > max_bytes:
        raise InvalidFileError(
            f"'{filename}' is {len(data) / 1e6:.1f} MB; the limit is {settings.max_upload_mb:g} MB."
        )
    media_type = sniff_media_type(data)
    if media_type is None:
        raise InvalidFileError(
            f"'{filename}' is not a PDF, JPG, PNG or WEBP file (checked by content, not extension)."
        )

    if media_type == "application/pdf":
        if b"%%EOF" not in data[-2048:]:
            raise InvalidFileError(f"'{filename}' looks truncated or corrupt (no PDF trailer).")
        return PreparedInput("document", media_type, base64.standard_b64encode(data).decode())

    # Images: verify, apply EXIF rotation (phone photos), downscale, strip metadata.
    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img).convert("RGB")
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise InvalidFileError(f"'{filename}' is not a readable image ({exc}).") from exc

    notes = []
    if max(img.size) > MAX_IMAGE_EDGE:
        img.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE), Image.LANCZOS)
        notes.append(f"image downscaled to {img.size[0]}x{img.size[1]}")
    if min(img.size) < 400:
        raise InvalidFileError(f"'{filename}' is too small ({img.size[0]}x{img.size[1]}) to read.")

    quality = 90
    while True:  # re-encode as JPEG, lowering quality until under the API limit
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        if buf.tell() <= MAX_IMAGE_BYTES or quality <= 50:
            break
        quality -= 10
    return PreparedInput("image", "image/jpeg", base64.standard_b64encode(buf.getvalue()).decode(), notes)


def _content_blocks(prepared: PreparedInput) -> list[dict]:
    source = {"type": "base64", "media_type": prepared.media_type, "data": prepared.data_b64}
    return [{"type": prepared.kind, "source": source}, {"type": "text", "text": USER_PROMPT}]


# ---------------------------------------------------------------------------
# Model call with explicit retry policy
# ---------------------------------------------------------------------------

def _client() -> anthropic.Anthropic:
    # max_retries=0: the retry policy lives in extract() so it is explicit and testable.
    return anthropic.Anthropic(max_retries=0, timeout=settings.request_timeout_s)


def extract(data: bytes, filename: str = "upload", *, model: str | None = None) -> ExtractionResult:
    prepared = prepare_input(data, filename)
    model = model or settings.model
    client = _client()
    started = time.perf_counter()
    last_error: ExtractionError | None = None

    for attempt in (1, 2):  # first try + one retry
        try:
            response = client.messages.parse(
                model=model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": _content_blocks(prepared)}],
                output_format=WireDocument,
                output_config={"effort": settings.effort},
            )
            if response.stop_reason == "refusal":
                raise ExtractionError("The model declined to process this document.", retryable=True)
            if response.stop_reason == "max_tokens":
                raise ExtractionError("The model response was truncated (max_tokens).", retryable=True)
            if response.parsed_output is None:
                raise ExtractionError("The model returned no structured output.", retryable=True)
            parsed = response.parsed_output.to_document()  # may raise ValidationError -> retried
            usage = {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            }
            return ExtractionResult(
                document=parsed,
                model=response.model,
                attempts=attempt,
                seconds=round(time.perf_counter() - started, 2),
                usage=usage,
                stop_reason=response.stop_reason,
            )

        # --- non-retryable: configuration / request problems ---------------
        except anthropic.AuthenticationError as exc:
            raise ExtractionError("ANTHROPIC_API_KEY is missing or invalid.", status_code=401) from exc
        except anthropic.PermissionDeniedError as exc:
            raise ExtractionError("This API key has no access to the model.", status_code=403) from exc
        except anthropic.NotFoundError as exc:
            raise ExtractionError(f"Model '{model}' not found. Check ANTHROPIC_MODEL.", status_code=400) from exc
        except (anthropic.BadRequestError, anthropic.RequestTooLargeError) as exc:
            raise ExtractionError(f"The API rejected the document: {exc.message}", status_code=400) from exc

        # --- retryable: transient failures ----------------------------------
        except anthropic.RateLimitError as exc:
            wait = min(float(exc.response.headers.get("retry-after", "5") or 5), 20.0)
            last_error = ExtractionError("Rate limited by the API.", status_code=429, retryable=True)
            if attempt == 1:
                time.sleep(wait)
            continue
        except anthropic.APIStatusError as exc:  # 5xx, 529 overloaded...
            last_error = ExtractionError(f"API error {exc.status_code}.", retryable=True)
        except anthropic.APITimeoutError:
            last_error = ExtractionError(
                f"The model did not answer within {settings.request_timeout_s:g}s.",
                status_code=504, retryable=True,
            )
        except anthropic.APIConnectionError:
            last_error = ExtractionError("Could not reach the Claude API (network).", retryable=True)
        except pydantic.ValidationError as exc:
            # Raised by messages.parse() when the JSON does not match the schema
            # (e.g. truncated output). A second attempt usually succeeds.
            last_error = ExtractionError(
                f"Invalid structured response ({exc.error_count()} schema errors).", retryable=True
            )
        except ExtractionError as exc:
            last_error = exc

        if attempt == 1:
            time.sleep(2)  # small backoff before the single retry

    assert last_error is not None
    raise ExtractionError(f"{last_error} (failed after 2 attempts)", status_code=last_error.status_code)
