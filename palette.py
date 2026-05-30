"""
Image colour palette functions
"""
import math
import extcolors
from PIL import Image, ImageDraw

# Colour extraction walks every pixel, so it is by far the most expensive stage
# of the pipeline on large (e.g. 24MP) images. The dominant colours of a photo
# are visually identical whether sampled from the full image or a downscaled
# copy, so we shrink the image to at most EXTRACT_MAX_EDGE pixels on its longest
# side before extraction. This typically cuts the pixel count (and therefore the
# extraction time) by 20-50x with no visible change to the resulting palette.
EXTRACT_MAX_EDGE = 1000


def extract_colors(img):
    # tolerance = 32
    tolerance = 32
    limit = 5

    # Downscale a copy for extraction only. The original image is untouched.
    longest_edge = max(img.width, img.height)
    if longest_edge > EXTRACT_MAX_EDGE:
        scale = EXTRACT_MAX_EDGE / longest_edge
        sample = img.resize(
            (max(1, round(img.width * scale)), max(1, round(img.height * scale))),
            Image.BILINEAR,
        )
    else:
        sample = img

    colors, pixel_count = extcolors.extract_from_image(sample, tolerance, limit)

    if sample is not img:
        sample.close()

    return colors


def render_color_platte(colors, size):
    # size = 150
    columns = 6
    width = int(min(len(colors), columns) * size)
    height = int((math.floor(len(colors) / columns) + 1) * size)
    result = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    canvas = ImageDraw.Draw(result)
    for idx, color in enumerate(colors):
        x = int((idx % columns) * size)
        y = int(math.floor(idx / columns) * size)
        # canvas.rectangle([(x, y), (x + size - 1, y + size - 1)], fill=color[0])
        canvas.rectangle([(x, y), (x + size, y + size)], fill=color[0])
    return result


def overlay_palette(img: Image, color_palette: Image, offset):
    img.paste(color_palette, offset)

    return img


def load_image_color_palette(img, size):
    colors = extract_colors(img)
    color_palette = render_color_platte(colors, size)
    # img = overlay_palette(img, color_palette)
    return color_palette
