"""Bounded encoding for trusted browser captures, separate from card budgets.

These are internal memory/transport budgets, not claimed QQ platform limits.
No resize, palette quantization or chroma subsampling is used. Lossless PNG is
preferred; large photographic pages may use high-quality JPEG before splitting.
"""

from dataclasses import dataclass
from io import BytesIO

MAX_PAGE_BYTES = 4_900_000  # Core-selected webpage profile, not a QQ hard limit.
MAX_RAW_BYTES = 32 * 1024 * 1024
MAX_PIXELS = 48_000_000
MAX_WIDTH = 1440
MAX_PAGE_HEIGHT = 43200
MAX_OUTPUT_BYTES = 24_000_000
CAPTURE_TILE_HEIGHT = 4096  # Avoid Chromium's observed 16384px repeated raster.


class ImageBudgetError(ValueError):
    pass


@dataclass(frozen=True)
class EncodedPage:
    content: bytes
    width: int
    height: int
    format: str
    quality: int | None
    png_bytes: int


def _encode_rgb(image) -> EncodedPage:
    width, height = image.size
    with BytesIO() as output:
        image.save(output, format="PNG", optimize=True)
        png = output.getvalue()
    if len(png) <= MAX_PAGE_BYTES:
        return EncodedPage(png, width, height, "PNG", None, len(png))
    png_size = len(png)
    del png
    for quality in (95, 92, 90, 89, 88):
        with BytesIO() as output:
            image.save(
                output, format="JPEG", quality=quality, subsampling=0, optimize=True
            )
            encoded = output.getvalue()
        if len(encoded) <= MAX_PAGE_BYTES:
            return EncodedPage(encoded, width, height, "JPEG", quality, png_size)
    raise ImageBudgetError("official_page_encoded_budget")


async def capture_region(page, *, x, y, width, height) -> EncodedPage:
    """Capture bounded raster tiles and stitch exact rows before final encoding.

    Output page boundaries remain content-aware. These internal tiles are never
    separately sent and may cross content: their rows join without gaps/overlap.
    A single very tall Chromium clip can repeat raster content after 16384px.
    """
    from PIL import Image

    if (
        not 0 < width <= MAX_WIDTH
        or not 0 < height <= MAX_PAGE_HEIGHT
        or width * height > MAX_PIXELS
        or (
            Image.MAX_IMAGE_PIXELS is not None
            and width * height > Image.MAX_IMAGE_PIXELS
        )
    ):
        raise ImageBudgetError("official_page_pixel_budget")
    with Image.new("RGB", (width, height), "white") as canvas:
        for offset in range(0, height, CAPTURE_TILE_HEIGHT):
            tile_height = min(CAPTURE_TILE_HEIGHT, height - offset)
            raw = await page.screenshot(
                type="png",
                # Playwright otherwise trims clip to the viewport (900px).
                # Full-page coordinate mode with a small clip avoids both that
                # truncation and Chromium's oversized raster repetition.
                full_page=True,
                clip={"x": x, "y": y + offset, "width": width, "height": tile_height},
                animations="disabled",
                timeout=20000,
            )
            if type(raw) is not bytes or not 0 < len(raw) <= MAX_RAW_BYTES:
                raise ImageBudgetError("official_page_tile_budget")
            with Image.open(BytesIO(raw), formats=("PNG",)) as tile:
                if (
                    tile.size != (width, tile_height)
                    or tile.mode not in ("RGB", "RGBA")
                    or tile.n_frames != 1
                ):
                    raise ValueError("official_page_tile_invalid")
                tile.load()
                if tile.mode == "RGBA":
                    with tile.getchannel("A") as alpha:
                        canvas.paste(tile, (0, offset), alpha)
                else:
                    canvas.paste(tile, (0, offset))
            del raw
        return _encode_rgb(canvas)


def encode_page(content: bytes) -> EncodedPage:
    """Decode only bounded browser PNG, strip metadata, preserve native pixels."""
    from PIL import Image

    if type(content) is not bytes or not 0 < len(content) <= MAX_RAW_BYTES:
        raise ImageBudgetError("official_page_encoded_budget")
    with Image.open(BytesIO(content), formats=("PNG",)) as source:
        width, height = source.size
        if (
            not 0 < width <= MAX_WIDTH
            or not 0 < height <= MAX_PAGE_HEIGHT
            or width * height > MAX_PIXELS
            or source.mode not in ("RGB", "RGBA")
            or source.n_frames != 1
            or (
                Image.MAX_IMAGE_PIXELS is not None
                and width * height > Image.MAX_IMAGE_PIXELS
            )
        ):
            raise ImageBudgetError("official_page_pixel_budget")
        # Flatten the browser's optional alpha on the actual white page backing.
        source.load()
        with Image.new("RGB", source.size, "white") as image:
            if source.mode == "RGBA":
                with source.getchannel("A") as alpha:
                    image.paste(source, mask=alpha)
            else:
                image.paste(source)
            return _encode_rgb(image)
