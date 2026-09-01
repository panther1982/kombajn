"""Obrobka obrazu w pamieci. Port sprawdzonego batcha (bez dysku, bez Drive).

Zachowane z oryginalu:
- guard na pliki <5KB (niedosynchronizowany placeholder),
- img.load() lapiacy obciete/uszkodzone pliki,
- exif_transpose (obrot z telefonu),
- wymuszenie RGB (naprawa CMYK/palety),
- kanwa 1500x1500 + UnsharpMask + petla JPEG do <=300KB.
"""
from io import BytesIO

from PIL import Image, ImageFilter, ImageOps, UnidentifiedImageError


class ImageInputError(ValueError):
    pass


def normalize_input_image(data: bytes) -> BytesIO:
    """Czysci zdjecie wejsciowe przed wyslaniem do modelu. Zwraca bufor PNG."""
    size_kb = len(data) / 1024
    if size_kb < 5:
        raise ImageInputError(
            f"Plik ma tylko {size_kb:.1f} KB - prawdopodobnie niepelny/uszkodzony."
        )
    try:
        img = Image.open(BytesIO(data))
        img.load()  # wymusza pelny odczyt - lapie obciete pliki od razu
    except UnidentifiedImageError:
        raise ImageInputError("Plik nie jest rozpoznawany jako obraz.")

    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        img = img.convert("RGB")

    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    buffer.name = "input.png"  # SDK OpenAI potrzebuje nazwy z rozszerzeniem
    return buffer


def optimize_for_web(image_bytes: bytes) -> bytes:
    """Kanwa 1500x1500 na bialym tle, wyostrzenie, JPEG <=300KB."""
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    img.thumbnail((1500, 1500), Image.Resampling.LANCZOS)

    canvas = Image.new("RGB", (1500, 1500), "white")
    canvas.paste(img, ((1500 - img.width) // 2, (1500 - img.height) // 2))
    canvas = canvas.filter(ImageFilter.UnsharpMask(radius=1.2, percent=120, threshold=3))

    quality = 85
    while True:
        buffer = BytesIO()
        canvas.save(buffer, format="JPEG", quality=quality, optimize=True, progressive=True)
        if len(buffer.getvalue()) / 1024 <= 300 or quality <= 70:
            return buffer.getvalue()
        quality -= 3
