"""radar_capture.py — cattura uno scatto (telefono via HTTP o file locale) e lo
riscala a 2048px di larghezza per la pipeline QC. Nessuna dipendenza nuova."""
import urllib.request
import numpy as np
import cv2

TARGET_W = 2048

def rescale2048(img):
    """Porta l'immagine a larghezza TARGET_W mantenendo le proporzioni."""
    h, w = img.shape[:2]
    if w == TARGET_W:
        return img
    s = TARGET_W / w
    interp = cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC
    return cv2.resize(img, (TARGET_W, int(round(h * s))), interpolation=interp)

def _fetch(url, timeout):
    """Scarica e decodifica uno scatto. Ritorna BGR np.ndarray o None."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            buf = np.frombuffer(r.read(), np.uint8)
    except Exception as e:
        print(f"[capture: {url} non raggiungibile: {e}]")
        return None
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if img is None:
        print("[capture: scatto non decodificabile]")
    return img


def grab_raw(url, timeout=15):
    """Scatto GREZZO dal telefono (nessun rescale né crop): serve alla CALIBRAZIONE."""
    return _fetch(url, timeout)


def apply_calib(img, calib):
    """Ritaglia img (BGR) al rettangolo `calib` = [x0, y0, x1, y1] in FRAZIONI (0-1).
    È il ritaglio deciso in calibrazione, applicato IDENTICO a ogni scatto."""
    if not calib:
        return img
    h, w = img.shape[:2]
    x0, y0, x1, y1 = calib
    xa, xb = int(x0 * w), int(x1 * w)
    ya, yb = int(y0 * h), int(y1 * h)
    if xb - xa < 10 or yb - ya < 10:
        return img
    return img[ya:yb, xa:xb]


def grab(url, timeout=15, calib=None, crop_frac=0.0):
    """Scarica uno scatto, applica la CALIBRAZIONE (ritaglio salvato in frazioni) oppure
    il crop_frac di ripiego (fascia sopra/sotto), poi riscala a 2048px di larghezza.
    Ritorna BGR np.ndarray o None su errore."""
    img = _fetch(url, timeout)
    if img is None:
        return None
    if calib:
        img = apply_calib(img, calib)
    img = rescale2048(img)
    if not calib and crop_frac > 0:
        c = int(img.shape[0] * crop_frac)
        if 0 < c < img.shape[0] // 2:
            img = img[c:img.shape[0] - c]
    return img
