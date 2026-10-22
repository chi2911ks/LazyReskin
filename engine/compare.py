from __future__ import annotations

from pathlib import Path

from .common import ReskinError, write_json


def compare_images(reference: Path, actual: Path, output: Path, config: dict) -> dict:
    try:
        from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageStat
    except ImportError as exc:
        raise ReskinError("Image comparison needs Pillow: install dependencies from requirements.txt") from exc
    Image.MAX_IMAGE_PIXELS = 50_000_000
    output.mkdir(parents=True, exist_ok=True)

    def load(path, crop):
        try:
            with Image.open(path) as original:
                if original.width * original.height > 50_000_000:
                    raise ReskinError("Image exceeds pixel limit")
                rgba = original.convert("RGBA")
                background = Image.new("RGBA", rgba.size, config.get("background", "#ffffff"))
                background.alpha_composite(rgba)
                image = background.convert("RGB")
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise ReskinError(f"Cannot load image {path}: {exc}") from exc
        if crop:
            x, y, width, height = rectangle(crop, image.size)
            image = image.crop((x, y, x + width, y + height))
        return image

    ref = load(reference, config.get("reference_crop"))
    image = load(actual, config.get("actual_crop"))
    if ref.size != image.size:
        result = {"state": "visual-failed", "issues": [{"code": "viewport-mismatch", "reference": list(ref.size), "actual": list(image.size)}]}
        write_json(output / "issues.json", result)
        return result
    tolerance = config.get("pixel_tolerance", 8)
    threshold = config.get("max_mismatch_ratio")
    if not isinstance(tolerance, int) or not 0 <= tolerance <= 255:
        raise ReskinError("pixel_tolerance must be an integer in 0..255")
    if threshold is None:
        result = {"state": "needs-input", "issues": [{"code": "comparison-threshold-required"}]}
        write_json(output / "issues.json", result)
        return result
    if not isinstance(threshold, (int, float)) or not 0 <= threshold <= 1:
        raise ReskinError("max_mismatch_ratio must be in 0..1")
    mask = Image.new("L", ref.size, 255)
    draw = ImageDraw.Draw(mask)
    for item in config.get("masks", []):
        if not item.get("reason"):
            raise ReskinError("Each comparison mask needs a reason")
        x, y, width, height = rectangle(item["bounds"], ref.size)
        draw.rectangle((x, y, x + width - 1, y + height - 1), fill=0)
    included = mask.histogram()[255]
    excluded_ratio = 1 - included / (ref.width * ref.height)
    maximum_mask = config.get("max_mask_ratio", .2)
    if not isinstance(maximum_mask, (int, float)) or not 0 <= maximum_mask < 1:
        raise ReskinError("max_mask_ratio must be in [0,1)")
    if not included or excluded_ratio > maximum_mask:
        raise ReskinError(f"Too much image masked: {excluded_ratio:.2%}")
    difference = ImageChops.difference(ref, image)
    red, green, blue = difference.split()
    maximum = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    mismatch = maximum.point(lambda value: 255 if value > tolerance else 0)
    results = []

    def measure(key, bounds, limit):
        if not isinstance(limit, (int, float)) or not 0 <= limit <= 1:
            raise ReskinError("Region threshold must be in 0..1")
        x, y, width, height = rectangle(bounds, ref.size)
        box = x, y, x + width, y + height
        region_mask = mask.crop(box)
        count = region_mask.histogram()[255]
        if not count:
            raise ReskinError(f"Region {key} is fully masked")
        bad = ImageChops.multiply(mismatch.crop(box), region_mask).histogram()[255]
        ratio = bad / count
        mean = sum(ImageStat.Stat(difference.crop(box), mask=region_mask).mean) / (3 * 255)
        results.append({"region": key, "bounds": list(bounds), "included_pixels": count, "mismatch_ratio": ratio, "mean_absolute_error": mean, "threshold": limit, "passed": ratio <= limit})

    measure("full-image", [0, 0, ref.width, ref.height], threshold)
    for region in config.get("regions", []):
        measure(region["key"], region["bounds"], region.get("max_mismatch_ratio", threshold))
    Image.blend(ref, image, .5).save(output / "overlay.png")
    ImageEnhance.Contrast(difference).enhance(3).save(output / "diff.png")
    mask.save(output / "included-mask.png")
    result = {"state": "visual-passed" if all(item["passed"] for item in results) else "visual-failed", "dimensions": list(ref.size), "masked_ratio": excluded_ratio, "regions": results, "issues": [{"code": "region-mismatch", **item} for item in results if not item["passed"]]}
    write_json(output / "issues.json", result)
    return result


def rectangle(bounds, size):
    if not isinstance(bounds, (list, tuple)) or len(bounds) != 4 or any(not isinstance(value, int) for value in bounds):
        raise ReskinError("Bounds must be integer [x,y,width,height]")
    x, y, width, height = bounds
    if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > size[0] or y + height > size[1]:
        raise ReskinError(f"Bounds {bounds} outside image {size}")
    return x, y, width, height
