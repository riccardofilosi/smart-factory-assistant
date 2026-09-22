"""
overlay_grid.py — proietta le coordinate della breadboard sopra la foto.

Due modelli di calibrazione (la breadboard è la griglia di calibrazione):
- HOMOGRAPHY (4 angoli): veloce, ma assume board PIATTA -> deriva al centro (barrel distortion).
- POLY (griglia di 36 fori): fitta un polinomio 2D ideale->pixel che ASSORBE la collina centrale.

Uso:
  python overlay_grid.py IMG --calibgrid            # clicchi 36 fori noti -> fit polinomio (consigliato)
  python overlay_grid.py IMG --click                # 4 angoli -> homography (rapido, board piatta)
  python overlay_grid.py IMG --reuse                # riusa calib.json (rig fermo)
  python overlay_grid.py IMG --calibgrid --degree 3 # polinomio di grado 3 (collina forte)
La calibrazione si salva in calib.json ed è riusabile finché il rig non si muove.
"""
import sys, os, json, cv2
import numpy as np

ROWS = "abcdefghij"
ROW_U = {"a":0,"b":1,"c":2,"d":3,"e":4,"f":6,"g":7,"h":8,"i":9,"j":10}  # gap e/f
NCOLS = 63   # 63 fori per riga (i numeri STAMPATI sulla board sono inaffidabili)
CALIB = os.path.join(os.path.dirname(__file__), "calib.json")

# fori di controllo per il fit polinomiale (4 righe x 9 colonne = 36), in ordine di click.
# righe j,f = blocco alto ; e,a = blocco basso (2 per blocco -> cattura la curvatura verticale)
_CAL_COLS = [1, 8, 16, 24, 32, 40, 48, 55, 63]
CALIB_HOLES = [(r, c) for r in ("j", "f", "e", "a") for c in _CAL_COLS]

def ideal(row, col):
    return (ROW_U[row], col - 1)   # (u = righe, v = colonne)

# ---------- click generico (downscale + riscalo ai pixel originali) ----------
def click_points(img, labels, max_side=1400):
    h, w = img.shape[:2]
    s = min(1.0, max_side / max(h, w))
    disp = cv2.resize(img, (int(w*s), int(h*s))) if s < 1.0 else img.copy()
    pts = []
    win = "Clicca i fori nell'ordine indicato in alto (ESC annulla, INVIO a fine)"
    def on(ev, x, y, flags, _):
        if ev == cv2.EVENT_LBUTTONDOWN and len(pts) < len(labels):
            pts.append((x/s, y/s))
            cv2.circle(disp, (x, y), 5, (0, 0, 255), -1)
            cv2.putText(disp, labels[len(pts)-1], (x+6, y-6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,0,255), 2)
    cv2.namedWindow(win); cv2.setMouseCallback(win, on)
    while True:
        show = disp.copy()
        msg = f"Clicca: {labels[len(pts)]}" if len(pts) < len(labels) else "OK - premi INVIO"
        col = (0,140,255) if len(pts) < len(labels) else (0,180,0)
        cv2.putText(show, msg, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, col, 2)
        cv2.imshow(win, show)
        k = cv2.waitKey(20) & 0xFF
        if k == 27: cv2.destroyAllWindows(); sys.exit("Annullato.")
        if k == 13 and len(pts) == len(labels): break
    cv2.destroyAllWindows()
    return pts

# ---------- modello HOMOGRAPHY ----------
def homography(corners):
    src = np.float32([ideal("a",1), ideal("j",1), ideal("a",NCOLS), ideal("j",NCOLS)])
    return {"type": "homography", "H": cv2.getPerspectiveTransform(src, np.float32(corners)).tolist()}

# ---------- modello POLY (undistortion) ----------
def monomials(u, v, deg):
    if isinstance(deg, (list, tuple)):          # gradi separati (du righe, dv colonne) = prodotto tensoriale
        du, dv = deg
        return [(u**i)*(v**j) for i in range(du+1) for j in range(dv+1)]
    return [(u**i)*(v**j) for i in range(deg+1) for j in range(deg+1-i)]   # grado totale

def fit_poly(holes, pts, deg):
    A = np.array([monomials(u, v, deg) for (u, v) in (ideal(r, c) for r, c in holes)])
    X = np.array([p[0] for p in pts]); Y = np.array([p[1] for p in pts])
    cx = np.linalg.lstsq(A, X, rcond=None)[0]
    cy = np.linalg.lstsq(A, Y, rcond=None)[0]
    res = np.sqrt((A@cx - X)**2 + (A@cy - Y)**2)   # errore di fit ai punti di controllo (px)
    return {"type": "poly", "degree": deg, "cx": cx.tolist(), "cy": cy.tolist()}, res

# ---------- proiezione (dispatch sul modello) ----------
def project(model, u, v):
    if model["type"] == "table":                       # posizione esatta per-foro (buco per buco)
        x, y = model["holes"][f"{u},{v}"]; return (int(x), int(y))
    if model["type"] == "homography":
        H = np.array(model["H"]); p = H @ np.array([u, v, 1.0]); return (int(p[0]/p[2]), int(p[1]/p[2]))
    if model["type"] == "polyblock":                   # un poly per blocco (niente oscillazione sul canale)
        b = model["bottom"] if u <= 4 else model["top"]
        m = np.array(monomials(u, v, model["degree"]))
        return (int(m @ np.array(b["cx"])), int(m @ np.array(b["cy"])))
    m = np.array(monomials(u, v, model["degree"]))
    return (int(m @ np.array(model["cx"])), int(m @ np.array(model["cy"])))

# ---------- posizioni di tutti i fori (batch; RBF calcolato una volta per blocco) ----------
def positions(model):
    holes = [ideal(r, c) for r in ROWS for c in range(1, NCOLS+1)]
    if model["type"] == "rbf":
        from scipy.interpolate import RBFInterpolator
        pos = {}
        for blk in ("bottom", "top"):
            b = model[blk]
            sm = np.array(b["smooth"], float) if "smooth" in b else model.get("smoothing", 0)
            interp = RBFInterpolator(np.array(b["uv"], float), np.array(b["xy"], float),
                                     smoothing=sm, kernel="thin_plate_spline")
            sel = [uv for uv in holes if (uv[0] <= 4) == (blk == "bottom")]
            for uv, xy in zip(sel, interp(np.array(sel, float))):
                pos[uv] = (int(xy[0]), int(xy[1]))
        for k, (x, y) in model.get("table", {}).items():   # centri misurati: vincono sull'interpolazione
            u, v = map(int, k.split(","))
            pos[(u, v)] = (int(round(x)), int(round(y)))
        return pos
    return {uv: project(model, *uv) for uv in holes}

# ---------- disegno ----------
def draw(img, model):
    out = img.copy(); font = cv2.FONT_HERSHEY_SIMPLEX
    pos = positions(model)
    P = lambda r, c: pos[ideal(r, c)]
    for r in ROWS:
        for c in range(1, NCOLS+1):
            cv2.circle(out, P(r, c), 3, (0, 200, 0), -1)
    for r in ROWS:
        for c in (1, NCOLS):
            x, y = P(r, c)
            cv2.putText(out, r, (x + (-26 if c == 1 else 14), y+6), font, 0.6, (0,0,255), 2)
    for c in [1] + list(range(5, NCOLS, 5)) + [NCOLS]:
        for r, dy in (("j", -16), ("a", 22)):
            x, y = P(r, c)
            tw = cv2.getTextSize(str(c), font, 0.5, 2)[0][0]
            cv2.putText(out, str(c), (x - tw//2, y+dy), font, 0.5, (255,0,0), 2)
    for name, line in model.get("rails", {}).items():     # alimentazione: blu = '-', rosso = '+'
        col = (200, 80, 0) if name.startswith("-") else (0, 0, 220)
        for c, x, y in line:
            cv2.circle(out, (int(x), int(y)), 3, col, -1)
    return out

def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    path, mode = sys.argv[1], sys.argv[2]
    img = cv2.imread(path)
    if img is None:
        sys.exit(f"Non leggo {path}")
    deg = int(sys.argv[sys.argv.index("--degree")+1]) if "--degree" in sys.argv else 3

    if mode == "--calibgrid":
        labels = [f"{r}{c}" for r, c in CALIB_HOLES]
        print("Clicca in ordine questi 36 fori:", ", ".join(labels))
        pts = click_points(img, labels)
        model, res = fit_poly(CALIB_HOLES, pts, deg)
        print(f"fit polinomio grado {deg}: residuo px  max={res.max():.1f}  medio={res.mean():.1f}")
        json.dump(model, open(CALIB, "w")); print("calibrazione salvata:", CALIB)
    elif mode == "--click":
        pts = click_points(img, ["a1 (basso-sx)", "j1 (alto-sx)", "a63 (ULTIMO basso-dx)", "j63 (ULTIMO alto-dx)"])
        model = homography(pts)
        json.dump(model, open(CALIB, "w")); print("calibrazione (homography) salvata:", CALIB)
    elif mode == "--reuse":
        if not os.path.exists(CALIB): sys.exit("Nessuna calib.json: fai prima --calibgrid o --click.")
        model = json.load(open(CALIB)); print("riuso calib:", model["type"])
    else:
        sys.exit(__doc__)

    dst = path.rsplit(".", 1)[0] + "_overlay.png"
    cv2.imwrite(dst, draw(img, model)); print("salvato:", dst)

if __name__ == "__main__":
    main()
