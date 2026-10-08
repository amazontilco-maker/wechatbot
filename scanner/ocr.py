"""Local OCR (RapidOCR, runs offline) -> parse.Line list. Also the unread-badge pixel check."""
from .parse import Line

_engine = None


def ocr(img):
    """img: numpy BGR image or a file path."""
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR
        _engine = RapidOCR()
    result, _ = _engine(img)
    lines = []
    for box, text, _score in result or []:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        lines.append(Line(min(xs), min(ys), max(xs), max(ys), text))
    return lines


def load(path):
    import cv2
    img = cv2.imread(str(path))
    if img is None:
        raise SystemExit(f"cannot read image {path}")
    return img


def has_unread_badge(img, name_y, width=1080):
    """WeChat's unread badge / dot is red, on the top-right corner of the avatar."""
    s = width / 1080
    y1, y2 = max(0, int(name_y - 60 * s)), int(name_y + 10 * s)
    x1, x2 = int(140 * s), int(205 * s)
    region = img[y1:y2, x1:x2]          # BGR
    b, g, r = region[..., 0].astype(int), region[..., 1].astype(int), region[..., 2].astype(int)
    red = (r > 200) & (g < 100) & (b < 100)
    return int(red.sum()) >= 25
