from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
ASSETS.mkdir(exist_ok=True)


def rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, size, size), radius=radius, fill=255)
    return mask


def vertical_gradient(size: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    image = Image.new("RGBA", (size, size))
    pixels = image.load()
    for y in range(size):
        ratio = y / (size - 1)
        color = tuple(round(top[i] * (1 - ratio) + bottom[i] * ratio) for i in range(3))
        for x in range(size):
            pixels[x, y] = (*color, 255)
    return image


def draw_icon(size: int = 1024) -> Image.Image:
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    radius = round(size * 0.225)
    margin = round(size * 0.075)

    shadow_mask = rounded_mask(size - margin * 2, radius)
    shadow = Image.new("RGBA", (size - margin * 2, size - margin * 2), (0, 0, 0, 140))
    shadow.putalpha(shadow_mask)
    shadow = shadow.filter(ImageFilter.GaussianBlur(round(size * 0.035)))
    canvas.alpha_composite(shadow, (margin, round(margin * 1.18)))

    body = vertical_gradient(size - margin * 2, (104, 176, 255), (0, 113, 227))
    mask = rounded_mask(size - margin * 2, radius)
    body.putalpha(mask)
    canvas.alpha_composite(body, (margin, margin))

    draw = ImageDraw.Draw(canvas, "RGBA")
    inner = margin
    maxc = size - margin
    draw.rounded_rectangle(
        (inner + 12, inner + 12, maxc - 12, maxc - 12),
        radius=radius - 10,
        outline=(255, 255, 255, 72),
        width=round(size * 0.012),
    )

    overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay, "RGBA")
    overlay_draw.rounded_rectangle(
        (round(size * 0.24), round(size * 0.22), round(size * 0.76), round(size * 0.56)),
        radius=round(size * 0.09),
        fill=(255, 255, 255, 34),
        outline=(255, 255, 255, 54),
        width=round(size * 0.008),
    )
    canvas.alpha_composite(overlay)

    play = [
        (round(size * 0.42), round(size * 0.30)),
        (round(size * 0.42), round(size * 0.49)),
        (round(size * 0.58), round(size * 0.395)),
    ]
    draw.polygon(play, fill=(255, 255, 255, 245))

    arrow_x = round(size * 0.5)
    draw.rounded_rectangle(
        (arrow_x - round(size * 0.044), round(size * 0.55), arrow_x + round(size * 0.044), round(size * 0.73)),
        radius=round(size * 0.025),
        fill=(255, 255, 255, 245),
    )
    draw.polygon(
        [
            (round(size * 0.37), round(size * 0.69)),
            (round(size * 0.63), round(size * 0.69)),
            (round(size * 0.5), round(size * 0.84)),
        ],
        fill=(255, 255, 255, 245),
    )
    draw.rounded_rectangle(
        (round(size * 0.33), round(size * 0.80), round(size * 0.67), round(size * 0.86)),
        radius=round(size * 0.025),
        fill=(255, 255, 255, 245),
    )

    return canvas


def main() -> None:
    icon = draw_icon()
    icon.save(ASSETS / "app_icon.png")
    icon.resize((64, 64), Image.Resampling.LANCZOS).save(ASSETS / "app_icon_64.png")
    icon.save(
        ASSETS / "app_icon.ico",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )


if __name__ == "__main__":
    main()
