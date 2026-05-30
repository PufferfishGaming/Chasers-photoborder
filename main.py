"""
 Add a border to the image named in the first parameter.
 A new image with {filename}_border... will be generated in the output directory.
 TODO: Read up on sorting images by appearance https://github.com/Visual-Computing/LAS_FLAS/blob/main/README.md
 """

import os
import argparse
import logging
from filemanager import should_include_file, get_directory_files
from border import BorderType
from core import process_image

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO, datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Named aspect-ratio presets -> width/height float. "native" / None = no padding.
RATIO_PRESETS = {
    "native": None,
    "1:1": 1.0,
    "4:5": 4 / 5,
    "5:4": 5 / 4,
    "3:2": 3 / 2,
    "2:3": 2 / 3,
    "16:9": 16 / 9,
    "9:16": 9 / 16,
}


def parse_ratio(value: str) -> float:
    """Parse a ratio string ('4:5', 'native', or 'W:H') into a float or None."""
    if value is None:
        return None
    key = value.strip().lower()
    if key in RATIO_PRESETS:
        return RATIO_PRESETS[key]
    if ":" in key:
        w, h = key.split(":", 1)
        w, h = float(w), float(h)
        if h == 0:
            raise ValueError("Ratio height cannot be zero")
        return w / h
    raise ValueError(f"Unrecognised ratio '{value}'. Use one of {list(RATIO_PRESETS)} or W:H.")


def parse_arguments():
    parser = argparse.ArgumentParser(
        prog='python main.py',
        description='Add a border and exif data to jpg or png photos',
        epilog='Made for fun and to solve a little problem.'
    )
    parser.add_argument('path', help='File or directory path')
    parser.add_argument('-e', '--exif', action='store_true', default=False,
                        help='Print photo exif data on the border')
    parser.add_argument('-p', '--palette', action='store_true', default=False,
                        help='Add colour palette to the photo border')
    parser.add_argument('-t', '--border_type', type=BorderType, choices=list(BorderType), default=BorderType.SMALL,
                        help='Border Type: p for polaroid, s for small, m for medium, l for large, i for instagram')
    parser.add_argument('-r', '--recursive', action='store_true', default=False,
                        help='Process directories recursively')
    parser.add_argument('-o', '--output', default=None,
                        help='Output directory (default: a "bordered" folder next to the input)')
    parser.add_argument('--ratio', default='native',
                        help='Target output aspect ratio, padded with extra border (never crops). '
                             'One of: native, 1:1, 4:5, 5:4, 3:2, 2:3, 16:9, 9:16, or custom W:H. '
                             'Ignored for instagram border type.')
    parser.add_argument('--no-overwrite', action='store_true', default=False,
                        help='Never overwrite existing output files; append " (1)", " (2)", etc. instead')
    parser.add_argument('--include', nargs='+', default=['*.jpg', '*.jpeg', '*.png', '*.JPG', '*.JPEG', '*.PNG'],
                        help='File patterns to include')
    parser.add_argument('--exclude', nargs='+', default=["*_border*"],
                        help='File patterns to exclude (default: *_border*)')
    parser.add_argument('-f', '--font', default='Roboto-Regular.ttf', help='Font file in fonts directory')
    parser.add_argument('-fv', '--fontvariant', default=0, type=int, help='Font style variant index')
    parser.add_argument('-fb', '--fontbold', default='Roboto-Medium.ttf', help='Bold font file in fonts directory')
    parser.add_argument('-fbv', '--fontboldvariant', default=0, type=int, help='Bold font style variant index')
    return parser.parse_args()


def main():
    args = parse_arguments()
    paths = []
    input_root = None

    if os.path.isdir(args.path):
        input_root = os.path.abspath(args.path)
        paths = get_directory_files(args.path, args.recursive, args.include, args.exclude)
    elif os.path.isfile(args.path):
        input_root = os.path.dirname(os.path.abspath(args.path))
        if should_include_file(args.path, args.include, args.exclude):
            paths.append(args.path)
        else:
            logger.info(f'Skipping {args.path} as it does not match the include/exclude patterns')
    else:
        logger.error(f'{args.path} is not a valid file or directory')
        return

    # Default output folder: a "bordered" directory next to the input.
    output_root = args.output or os.path.join(input_root, "bordered")
    os.makedirs(output_root, exist_ok=True)

    moduledir = os.path.dirname(os.path.abspath(__file__))
    fontdir = os.path.join(moduledir, "fonts")

    target_ratio = parse_ratio(args.ratio)

    for path in paths:
        logger.info(f'Adding border to {path}')
        save_path = process_image(
            path=path,
            add_exif=args.exif,
            add_palette=args.palette,
            border_type=args.border_type,
            font=(args.font, args.fontvariant),
            boldfont=(args.fontbold, args.fontboldvariant),
            fontdir=fontdir,
            output_root=output_root,
            input_root=input_root,
            target_ratio=target_ratio,
            overwrite=not args.no_overwrite,
        )
        logger.info(f'Saved as {save_path}')


if __name__ == "__main__":
    try:
        main()
    except ValueError as e:
        logger.error(e)
