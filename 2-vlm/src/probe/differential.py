"""
differential.py — motore geometrico del controllo: DOVE via occupazione per-foro,
COSA via VLM.

Board ferma. Per ogni step K:
  1. blur-diff(K-1, K)            -> LOCALIZZA il componente aggiunto (blob) = regione
  2. segnale di occupazione per-foro nella regione (quanto cambia il centro del foro)
  3. la CLASSE (dal VLM) sceglie il TEMPLATE dei pin -> fori esatti:
       bottone  = 4 corner (gambe a col c-1/c+1, righe e/f a cavallo del gap),
                  centro scelto massimizzando il segnale sui 4 fori insieme (batte l'ombra);
       2-pin (resistenza/led/cap/jumper) / 3-pin (transistor) = fori estremi lungo l'asse del blob.
Senza classe: stampa i fori candidati per occupazione decrescente.

Uso:
  python differential.py            # tutti gli step, solo localizzazione + candidati
  python differential.py K classe   # step K con template (es. python differential.py 1 bottone)
"""
import os, sys, cv2, base64
import numpy as np
import overlay_grid as og
import build_map as bm

ROWS = og.ROWS
U_ROW = {u: r for r, u in og.ROW_U.items()}
CE, CF = og.ROW_U["e"], og.ROW_U["f"]
CD, CG = og.ROW_U["d"], og.ROW_U["g"]
def name(u, v):
    """Nome foro: griglia 'g7' (u int, v 0-based); rail = solo il segno '+'/'-'
    (elettricamente la rail è un nodo unico: la colonna non conta)."""
    return u[0] if isinstance(u, str) else f"{U_ROW[u]}{v+1}"
def ru(u): return 100 if isinstance(u, str) else ROWS.index(U_ROW[u])
TRACE = None                      # radar: lista di (kind, cand, score); None = spento (default)
def _tr(kind, cand, sc):
    if TRACE is not None: TRACE.append((kind, list(cand), float(sc)))

# vocabolario = componenti del Manuale Operativo Definitivo (golden-data.json)
PINS = {"bottone": 4, "button": 4, "resistenza": 2, "resistor": 2, "led": 2,
        "diodo": 2, "condensatore": 2, "cap": 2, "jumper": 2, "cavetto": 2,
        "transistor": 3, "trimmer": 3, "potenziometro": 3,
        "fotoresistenza": 2, "ldr": 2, "buzzer": 2, "rgb": 4,
        "optoaccoppiatore": 6, "opto": 6}
FREE2 = {"jumper", "cavetto"}   # FILI (colorati, arco libero); la fotoresistenza è un
                                # corpo+gambe come la resistenza -> ramo 2-pin standard

# --- modalità QC vs golden ---
KIND2CLS = {"button": "bottone", "resistor": "resistenza", "led": "led",
            "led_rgb": "rgb", "jumper": "jumper", "transistor": "transistor",
            "cap": "condensatore", "diode": "diodo", "ldr": "fotoresistenza",
            "trimmer": "trimmer", "buzzer": "buzzer", "optocoupler": "optoaccoppiatore"}
HIDDEN = {"buzzer", "trimmer"}   # pin sotto il corpo -> mai oltre STIMATO (verifica
                                 # a vista). Bottone e opto hanno un ramo dedicato
                                 # (coppie+quadrupla, doppia terna).

SIGMA, THR, THR_LO, OPEN, MIN_AREA, R = 3, 30, 8, 5, 500, 6

def blur_gray(img):
    return cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32), (0, 0), SIGMA)

def board_mask(pos, shape, grow=45):
    pts = np.array(list(pos.values()), np.int32)
    m = np.zeros(shape[:2], np.uint8)
    cv2.fillConvexPoly(m, cv2.convexHull(pts), 255)
    return cv2.dilate(m, np.ones((grow, grow), np.uint8))

def find_blobs(cur, prev, bmask, nmax=4):
    """TUTTI i cambiamenti significativi tra prev e cur (non solo il più grande: inserire
    un componente può urtarne un altro -> due blob). Ogni blob = core a soglia alta ESTESO
    per isteresi con le strutture SOTTILI del segnale debole (le gambe stanno sotto THR).
    Ritorna [(pts, bbox), ...] per area decrescente."""
    d = np.abs(blur_gray(cur) - blur_gray(prev))
    strong = ((d > THR).astype(np.uint8) * 255) & bmask
    strong = cv2.morphologyEx(strong, cv2.MORPH_OPEN, np.ones((OPEN, OPEN), np.uint8))
    weak = ((d > THR_LO).astype(np.uint8) * 255) & bmask
    # solo strutture SOTTILI del segnale debole (gambe): l'apertura larga toglie le ombre estese
    thin = weak & ~cv2.morphologyEx(weak, cv2.MORPH_OPEN, np.ones((11, 11), np.uint8))
    ext = cv2.morphologyEx(thin | strong, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    _, le = cv2.connectedComponents(ext)
    n, ls, stats, _ = cv2.connectedComponentsWithStats(strong)
    cand = sorted(((stats[i, cv2.CC_STAT_AREA], i) for i in range(1, n)
                   if stats[i, cv2.CC_STAT_AREA] >= MIN_AREA), reverse=True)[:nmax]
    out, used = [], set()
    for _, i in cand:
        ys, xs = np.where(ls == i)
        lab = int(le[ys[0], xs[0]])
        if lab == 0 or lab in used: continue      # già dentro un blob esteso preso prima
        used.add(lab)
        ys, xs = np.where(le == lab)
        out.append((np.column_stack([xs, ys]).astype(np.float32),
                    (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))))
    return out

def occ(g0, g1, pos, u, v):
    """Segnale di occupazione del foro (u,v): |variazione media del centro|."""
    if (u, v) not in pos: return 0.0
    x, y = pos[(u, v)]
    p0, p1 = g0[y-R:y+R, x-R:x+R], g1[y-R:y+R, x-R:x+R]
    if p0.size == 0 or p1.size == 0: return 0.0
    return abs(float(p1.mean() - p0.mean()))

def region_holes(pos, bbox, pad=40):
    x0, y0, x1, y1 = bbox
    return [uv for uv, (x, y) in pos.items() if x0-pad <= x <= x1+pad and y0-pad <= y <= y1+pad]

# vocabolario = SOLO componenti citati nelle 10 commesse del Manuale Operativo Definitivo,
# con descrittore visivo del pezzo REALE del kit (attacca gli scambi tipo buzzer->resistenza)
DESCR = {
    "bottone": "quadratino nero ~6mm con tasto tondo, 4 gambe, a cavallo della fessura centrale",
    "resistenza": "cilindretto blu/beige con bande colorate e 2 fili sottili in linea",
    "led": "cupolina colorata 5mm (rossa/verde/gialla/blu) con 2 gambe",
    "rgb": "led 5mm a lente smerigliata/lattea con 4 gambe in fila",
    "condensatore": "cilindro nero con striscia chiara sul fianco (elettrolitico), montato"
                    " CORICATO in riga o VERTICALE a cavallo",
    "transistor": "corpo nero a mezzaluna (TO-92) con 3 gambe",
    "trimmer": "quadrato blu con vite/croce di regolazione al centro",
    "jumper": "filo RIGIDO colorato piegato a ponte, nudo alle estremita' (niente guaine)",
    "cavetto": "filo FLESSIBILE ad arco con guaine di plastica NERE alle due estremita'",
    "fotoresistenza": "dischetto chiaro con serpentina rossastra in vista, 2 gambe",
    "buzzer": "cilindro nero alto ~12mm, foro o adesivo sul dorso, corpo che copre i pin",
    "diodo": "cilindretto nero con banda chiara a UN capo, 2 fili",
    "optoaccoppiatore": "chip DIP BIANCO/beige con scritte nere, 6 piedini a cavallo della fessura",
}
VOCAB = ", ".join(DESCR)
_VOC_LINES = "\n".join(f"- {k}: {v}" for k, v in DESCR.items())
CLASSIFY_SYS = f"""Foto ritagliata di UN componente appena inserito su una breadboard.
Dimmi COS'E'. Rispondi con UNA sola parola, tra queste (a destra come riconoscerle
sul kit reale):
{_VOC_LINES}
Se incerto scegli la piu' probabile. Nessun'altra parola."""

def _match_cls(w):
    for cls in sorted(PINS, key=len, reverse=True):   # 'fotoresistenza' prima di 'resistenza'
        if cls in w: return cls
    return None

def _parse_cls_col(raw):
    w = raw.strip().lower()
    cls = _match_cls(w)
    if cls:
        col = next((t for t in w.replace(",", " ").split() if t != cls and t.isalpha()), "")
        return cls, col
    return None, None

def vlm_classify(crop_bgr):
    """Classifica il componente nel crop via VLM (risposta a una parola).
    Ritorna la classe o None. Il colore lo misura OpenCV (blob_color), non il VLM."""
    import read_holes as rh
    url = "data:image/png;base64," + base64.b64encode(cv2.imencode(".png", crop_bgr)[1]).decode()
    msgs = [{"role": "system", "content": CLASSIFY_SYS},
            {"role": "user", "content": [{"type": "text", "text": "Che componente e'?"},
                                         {"type": "image_url", "image_url": {"url": url}}]}]
    try:
        raw, _ = rh.chat(msgs)
    except SystemExit:
        return None
    return _match_cls(raw.strip().lower())

def blob_color(img, pts):
    """Colore dominante SATURO del blob (per il registro): nome da HUES o ''. Deterministico."""
    xs = pts[:, 0].astype(int); ys = pts[:, 1].astype(int)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[ys, xs, 0], hsv[ys, xs, 1], hsv[ys, xs, 2]
    sel = (s > 100) & (v > 60)
    if sel.sum() < 150: return ""
    hh = h[sel]
    best, bn = "", 0
    for nome, rngs in HUES.items():
        n = sum(int(((hh >= lo) & (hh <= hi)).sum()) for lo, hi in rngs)
        if n > bn: bn, best = n, nome
    return best if bn >= 150 else ""

ARRIVED_SYS = """Foto di una breadboard durante un montaggio in corso. Il componente
APPENA AGGIUNTO e' quello DENTRO IL RIQUADRO MAGENTA. Tutto cio' che sta fuori dal
riquadro e' dei passi precedenti ({{elenco}}) e NON conta, anche se e' piu' grande o
piu' vistoso del pezzo nel riquadro (es. un cavetto blu nuovo accanto a un led
rosso gia' montato veniva letto 'led').
Dimmi COSA e' stato aggiunto (solo il pezzo nel riquadro).
Rispondi con DUE parole: classe e colore.
Classi possibili (a destra come riconoscerle sul kit reale):
{voci}
Esempio: "jumper blu". Nessun'altra parola.""".format(voci=_VOC_LINES)

def vlm_arrived(img, bboxes, state):
    """Step con urti: chiede al VLM COSA è arrivato, dandogli il registro come contesto.
    Ritorna (classe, colore) o (None, None)."""
    import read_holes as rh
    x0 = max(0, min(b[0] for b in bboxes) - 90); y0 = max(0, min(b[1] for b in bboxes) - 90)
    crop = img[y0:max(b[3] for b in bboxes) + 90, x0:max(b[2] for b in bboxes) + 90].copy()
    # Il riquadro magenta sul pezzo nuovo: senza, il VLM sceglie l'oggetto più
    # vistoso del crop (es. un cavetto blu letto "led" per la cupola accanto).
    for b in bboxes:
        cv2.rectangle(crop, (int(b[0]) - x0 - 8, int(b[1]) - y0 - 8),
                      (int(b[2]) - x0 + 8, int(b[3]) - y0 + 8), (255, 0, 255), 4)
    elenco = ", ".join(f"{c['cls']} {c.get('col', '')}".strip() for c in state) or "nessuno"
    url = "data:image/png;base64," + base64.b64encode(cv2.imencode(".png", crop)[1]).decode()
    msgs = [{"role": "system", "content": ARRIVED_SYS.format(elenco=elenco)},
            {"role": "user", "content": [{"type": "text", "text": "Cosa e' stato aggiunto?"},
                                         {"type": "image_url", "image_url": {"url": url}}]}]
    try:
        raw, _ = rh.chat(msgs)
    except SystemExit:
        return None, None
    return _parse_cls_col(raw)

HUES = {"rosso": [(0, 10), (165, 180)], "arancione": [(8, 20)], "giallo": [(20, 35)],
        "verde": [(36, 85)], "azzurro": [(85, 105)], "blu": [(90, 130)], "viola": [(125, 155)]}

def color_blob(img, color, region_mask):
    """Il blob più grande del COLORE dato dentro region_mask (per puntare il componente
    nuovo indicato dal VLM). Ritorna (pts, bbox) o None (colore ignoto/non trovato)."""
    rng = HUES.get(color)
    if not rng: return None
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    m = np.zeros(img.shape[:2], np.uint8)
    for lo, hi in rng:
        m |= cv2.inRange(hsv, (lo, 100, 60), (hi, 255, 255))
    m &= region_mask
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    n, lbl, stats, _ = cv2.connectedComponentsWithStats(m)
    if n < 2: return None
    i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    if stats[i, cv2.CC_STAT_AREA] < 300: return None
    ys, xs = np.where(lbl == i)
    return (np.column_stack([xs, ys]).astype(np.float32),
            (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())))

PICK_SYS = """Foto ravvicinata di una breadboard. C'e' un componente ({cls}) inserito.
I cerchi magenta numerati indicano fori candidati: la gamba/terminale del componente
SCENDE DENTRO uno solo di questi fori (negli altri al massimo ci passa sopra il filo o
c'e' un'ombra). Rispondi SOLO con il numero del cerchio giusto."""

PICK2_SYS = """Foto ravvicinata di una breadboard con un JUMPER (filo/cavetto). I cerchi
magenta numerati marcano fori candidati. In DUE di essi il filo ENTRA nel foro (sono gli
estremi del jumper); sugli altri il filo passa solo SOPRA senza entrare.
Rispondi SOLO con i due numeri separati da virgola, esempio: 1,4"""

def wire_endpoints(img, pts, pad=150):
    """Segue il FILO del jumper per colore (i jumper del kit sono saturi; la board è bianca)
    e ritorna i due estremi geodetici del filo = dove entra nei fori. None se non trova filo."""
    from collections import deque
    x0, y0 = max(0, int(pts[:, 0].min()) - pad), max(0, int(pts[:, 1].min()) - pad)
    x1, y1 = int(pts[:, 0].max()) + pad, int(pts[:, 1].max()) + pad
    sub = img[y0:y1, x0:x1]
    hsv = cv2.cvtColor(sub, cv2.COLOR_BGR2HSV)
    mask = ((hsv[:, :, 1] > 110) & (hsv[:, :, 2] > 60)).astype(np.uint8)
    # erosione: le strisce rosse/blu STAMPATE sulle rail sono sature ma sottili -> spariscono
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    n, lbl, stats, _ = cv2.connectedComponentsWithStats(mask)
    if n < 2: return None
    i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    if stats[i, cv2.CC_STAT_AREA] < 400: return None
    comp = (lbl == i)[::2, ::2]                       # downsample x2 per la BFS
    ys, xs = np.nonzero(comp)
    def far(sy, sx):
        dist = np.full(comp.shape, -1, np.int16)
        dq = deque([(sy, sx)]); dist[sy, sx] = 0
        best = (0, sy, sx)
        while dq:
            y, x = dq.popleft(); d = dist[y, x]
            if d > best[0]: best = (d, y, x)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    yy, xx = y + dy, x + dx
                    if 0 <= yy < comp.shape[0] and 0 <= xx < comp.shape[1] \
                       and comp[yy, xx] and dist[yy, xx] < 0:
                        dist[yy, xx] = d + 1; dq.append((yy, xx))
        return best, dist
    (_, ay, ax), _ = far(ys[0], xs[0])
    (_, by, bx), dist_a = far(ay, ax)                 # geodetiche dal tip A; B = tip opposto
    _, dist_b = far(by, bx)
    def tip_ext(ty, tx, dist):
        """Il rosso muore qualche px PRIMA del foro (ombra/erosione): estendi il tip
        lungo la direzione locale del filo di ~14px reali."""
        D = int(dist[ty, tx])
        sel = np.nonzero((dist >= D - 9) & (dist <= D - 4))
        if len(sel[0]) == 0: return float(tx), float(ty)
        v = np.array([tx - sel[1].mean(), ty - sel[0].mean()])
        n = float(np.linalg.norm(v))
        if n < 1: return float(tx), float(ty)
        return float(tx + v[0] / n * 7), float(ty + v[1] / n * 7)
    bx, by = tip_ext(by, bx, dist_a)
    ax, ay = tip_ext(ay, ax, dist_b)
    return (ax * 2 + x0, ay * 2 + y0), (bx * 2 + x0, by * 2 + y0)

def _geo_tips(comp):
    """Estremi geodetici di una maschera booleana (già downsample x2) + direzione locale.
    Ritorna [(x, y), (x, y)] in coordinate maschera, tip estesi di ~7px."""
    from collections import deque
    ys, xs = np.nonzero(comp)
    if len(xs) == 0: return None
    def far(sy, sx):
        dist = np.full(comp.shape, -1, np.int16)
        dq = deque([(sy, sx)]); dist[sy, sx] = 0
        best = (0, sy, sx)
        while dq:
            y, x = dq.popleft(); d = dist[y, x]
            if d > best[0]: best = (d, y, x)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    yy, xx = y + dy, x + dx
                    if 0 <= yy < comp.shape[0] and 0 <= xx < comp.shape[1] \
                       and comp[yy, xx] and dist[yy, xx] < 0:
                        dist[yy, xx] = d + 1; dq.append((yy, xx))
        return best, dist
    (_, ay, ax), _ = far(ys[0], xs[0])
    (_, by, bx), dist_a = far(ay, ax)
    _, dist_b = far(by, bx)
    def ext(ty, tx, dist):
        D = int(dist[ty, tx])
        sel = np.nonzero((dist >= D - 9) & (dist <= D - 4))
        if len(sel[0]) == 0: return float(tx), float(ty)
        v = np.array([tx - sel[1].mean(), ty - sel[0].mean()])
        n = float(np.linalg.norm(v))
        return (float(tx), float(ty)) if n < 1 else (float(tx + v[0]/n*7), float(ty + v[1]/n*7))
    bx, by = ext(by, bx, dist_a)
    ax, ay = ext(ay, ax, dist_b)
    return [(ax, ay), (bx, by)]

def mask_endpoints(pts, bridge_bands=()):
    """Continuità del componente: pin - metallo - corpo - metallo - pin.
    La sagoma del diff È il componente: i suoi estremi geodetici sono i pin, ovunque
    vadano (stessa riga, diagonale, rail). bridge_bands = fasce y dei SOLCHI della board
    (canale centrale, fasce blocco-rail): lì la gamba resta in ombra e la sagoma si
    spezza -> si ricuce verticalmente SOLO dentro le bande (fuori non cambia nulla)."""
    x0, y0 = int(pts[:, 0].min()), int(pts[:, 1].min())
    w, h = int(pts[:, 0].max()) - x0 + 1, int(pts[:, 1].max()) - y0 + 1
    m = np.zeros((h, w), np.uint8)
    m[(pts[:, 1] - y0).astype(int), (pts[:, 0] - x0).astype(int)] = 1
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    for ylo, yhi in bridge_bands:
        lo, hi = int(ylo) - y0 - 25, int(yhi) - y0 + 25
        if hi <= 0 or lo >= h: continue
        strip = slice(max(0, lo), min(h, hi))
        m[strip] = cv2.morphologyEx(m[strip], cv2.MORPH_CLOSE, np.ones((61, 5), np.uint8))
    tips = _geo_tips(m[::2, ::2].astype(bool))
    if tips is None: return None
    return [(tx * 2 + x0, ty * 2 + y0) for tx, ty in tips]

def colored_wire(img, pts, pad=60):
    """C'è un FILO COLORATO (jumper/cavetto) in questo blob? Il colore comanda sulla
    classificazione: se c'è, si insegue il filo, qualunque cosa dica il VLM.
    Ritorna (pts_colore, bbox) se trova un blob saturo ALLUNGATO, altrimenti None."""
    x0, y0 = max(0, int(pts[:, 0].min()) - pad), max(0, int(pts[:, 1].min()) - pad)
    sub = img[y0:int(pts[:, 1].max()) + pad, x0:int(pts[:, 0].max()) + pad]
    if sub.size == 0: return None
    hsv = cv2.cvtColor(sub, cv2.COLOR_BGR2HSV)
    m = ((hsv[:, :, 1] > 110) & (hsv[:, :, 2] > 60)).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    n, lbl, stats, _ = cv2.connectedComponentsWithStats(m)
    if n < 2: return None
    i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    if stats[i, cv2.CC_STAT_AREA] < 700: return None
    ys, xs = np.where(lbl == i)
    P = np.column_stack([xs, ys]).astype(np.float32)
    (_, (rw, rh), _) = cv2.minAreaRect(P)
    if min(rw, rh) < 1 or max(rw, rh) / min(rw, rh) < 3.2: return None   # compatto = non filo
    P[:, 0] += x0; P[:, 1] += y0
    return P, (int(P[:, 0].min()), int(P[:, 1].min()), int(P[:, 0].max()), int(P[:, 1].max()))

def body_holes(pts, pos, holes, thick=19):
    """Fori il cui centro cade nel corpo (parte spessa della sagoma; le gambe sottili
    spariscono con l'erosione). Sono occlusioni: mai candidabili come pin."""
    x0, y0 = int(pts[:, 0].min()), int(pts[:, 1].min())
    w, h = int(pts[:, 0].max()) - x0 + 1, int(pts[:, 1].max()) - y0 + 1
    m = np.zeros((h, w), np.uint8)
    m[(pts[:, 1] - y0).astype(int), (pts[:, 0] - x0).astype(int)] = 255
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    body = cv2.erode(m, np.ones((thick, thick), np.uint8))
    body = cv2.dilate(body, np.ones((thick + 4, thick + 4), np.uint8))
    out = set()
    for uv in holes:
        x, y = pos[uv]
        if 0 <= y - y0 < h and 0 <= x - x0 < w and body[y - y0, x - x0]:
            out.add(uv)
    return out

SHADOW_DROP, SHADOW_UNIFORM = 8.0, 1.6   # ombra: scurimento minimo e uniformità centro/anello

def shadow_holes(g0f, g1f, pos, anchors, pitch):
    """Ombra da scurimento: un foro nella
    falda d'ombra del corpo si scurisce QUANTO il suo intorno (calo uniforme del diff
    di luminanza); una gamba scurisce il foro molto più dell'anello attorno.
    Considera solo fori adiacenti alla zona spessa (anchors)."""
    out = set()
    ac = [pos[uv] for uv in anchors if uv in pos]
    if not ac: return out
    d = g1f - g0f
    for uv, (x, y) in pos.items():
        if uv in anchors: continue
        if not any(abs(x - axp) <= 1.6 * pitch and abs(y - ayp) <= 1.6 * pitch
                   for axp, ayp in ac): continue
        c = d[y - R:y + R, x - R:x + R]
        ring = d[y - 3 * R:y + 3 * R, x - 3 * R:x + 3 * R]
        if c.size == 0 or ring.size <= c.size: continue
        dc = -float(c.mean())
        dr = -(float(ring.sum()) - float(c.sum())) / (ring.size - c.size)
        if dc >= SHADOW_DROP and dr >= SHADOW_DROP and dc <= SHADOW_UNIFORM * dr:
            out.add(uv)
    return out

def zone_non_evidenza(g0f, g1f, pos, pts, pitch, prior=frozenset(), tips=None):
    """Funzione unica delle esclusioni: fori che non possono testimoniare
    (mai vicini-caldi, mai proposte del localizzatore, mai conferme rail/snap).
      'zona-spessa' = corpo del blob corrente (body_holes; nel referto MAI "corpo":
                      per un'ombra sarebbe falso)
      'pezzo-noto'  = fori golden + hull degli step precedenti già verificati
      'ombra'       = calo netto e uniforme di luminanza adiacente alla zona spessa
    tips = capi geodetici della sagoma: il NODO del capo di un filo (piega + ombra di
    contatto) supera lo spessore del corpo, ma È il pezzo -> i fori entro 1.3 passi
    da un tip restano evidenza (misurato: un capo-rail finiva in zona spessa).
    I fori ATTESI dello step si misurano SEMPRE, zone o no. Ritorna {uv: etichetta}."""
    zone = {}
    allh = list(pos.keys())
    thick = body_holes(pts, pos, allh) if pts is not None and len(pts) else set()
    if tips:
        thick = {uv for uv in thick
                 if all(np.hypot(pos[uv][0] - tx, pos[uv][1] - ty) > 1.3 * pitch
                        for tx, ty in tips)}
    for uv in thick: zone[uv] = "zona-spessa"
    for uv in prior:
        if uv in pos and uv not in zone: zone[uv] = "pezzo-noto"
    for uv in shadow_holes(g0f, g1f, pos, thick, pitch):
        if tips and any(np.hypot(pos[uv][0] - tx, pos[uv][1] - ty) <= 1.3 * pitch
                        for tx, ty in tips): continue
        zone.setdefault(uv, "ombra")
    return zone

def guaina_v2(img, tip, ax, pitch):
    """Guaina del cavetto. Una soglia fissa "molto nero" non separa (misurato sui
    capi reali: la guaina grigia/traslucida ha V 120-190 e la board bianca è pure
    de-saturata): guaina = blob de-saturato e scuro relativo al suo intorno
    (V < mediana finestra - 25), di forma allungata, vicino al tip della sagoma;
    l'allineamento all'asse del filo è un bonus, mai un gate (al capo-rail la
    guaina giace lungo la rail, perpendicolare all'arrivo). Calibrato su 3 guaine
    (3/3 trovate) e 2 controlli rigidi (nessun falso positivo).
    Ritorna (centro, asse)."""
    r = int(1.8 * pitch)
    x0, y0 = max(0, int(tip[0]) - r), max(0, int(tip[1]) - r)
    sub = img[y0:int(tip[1]) + r, x0:int(tip[0]) + r]
    if sub.size == 0: return None
    hsv = cv2.cvtColor(sub, cv2.COLOR_BGR2HSV)
    S, V = hsv[:, :, 1], hsv[:, :, 2]
    m = ((S < 90) & (V < float(np.median(V)) - 25)).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, lbl, stats, _ = cv2.connectedComponentsWithStats(m)
    best = None
    for i in range(1, n):
        a = int(stats[i, cv2.CC_STAT_AREA])
        if not 150 <= a <= 6000: continue                  # più di un foro, meno di un'ombra
        ys, xs = np.where(lbl == i)
        P = np.column_stack([xs, ys]).astype(np.float32)
        (cx, cy), (rw, rh), ang = cv2.minAreaRect(P)
        L, Wd = max(rw, rh), min(rw, rh)
        if Wd < 1 or L / Wd < 1.3: continue                # forma allungata, non macchia
        th = np.deg2rad(ang if rw >= rh else ang + 90)
        gax = np.array([np.cos(th), np.sin(th)])
        sc = a * (1.3 if ax is not None and abs(float(gax @ ax)) >= 0.5 else 1.0)
        if best is None or sc > best[0]:
            best = (sc, (float(cx) + x0, float(cy) + y0), gax)
    return (best[1], best[2]) if best else None

def bordo_capo(score, posx, tip, vdir, pitch, weak):
    """Deduzione di bordo per il pin sotto guaina: non si trova per intensità
    (misurato: foro coperto 0.47 > pin 0.37) ma per posizione. Fori lungo l'asse
    della guaina, eliminati i non-cambiati, pin = ultimo foro cambiato prima del
    confine muto nel verso di marcia (es. e13-e14-e15 | e16=0.005 -> pin=e15).
    Ritorna (pin, netto, S_oltre) o None; netto=False -> bordo sfumato, rosa +-1
    dichiarata dal chiamante."""
    per = np.array([-vdir[1], vdir[0]])
    cand = []
    for uv, p in posx.items():
        d = np.array([p[0] - tip[0], p[1] - tip[1]], float)
        t = float(d @ vdir)
        if abs(float(d @ per)) <= 0.6 * pitch and -2.6 * pitch <= t <= 2.6 * pitch:
            cand.append((t, uv))
    cand.sort()
    if not cand: return None
    ss = [score(uv) for _, uv in cand]
    hot = [i for i, s in enumerate(ss) if s >= weak]
    if not hot: return None
    i0 = hot[0]                              # run caldo contiguo nel verso di marcia
    while i0 + 1 < len(ss) and ss[i0 + 1] >= weak: i0 += 1
    oltre = ss[i0 + 1] if i0 + 1 < len(ss) else 0.0
    return cand[i0][1], oltre < 0.5 * weak, oltre

def vlm_pick_pair(img, pos, cands, cls):
    """Estremi del jumper: scelta multipla a COPPIA per il VLM. Ritorna [uv, uv] o None."""
    import re
    import read_holes as rh
    xs = [pos[c][0] for c in cands]; ys = [pos[c][1] for c in cands]
    x0, y0 = max(0, min(xs) - 80), max(0, min(ys) - 80)
    crop = img[y0:max(ys) + 80, x0:max(xs) + 80].copy()
    for i, c in enumerate(cands):
        p = (pos[c][0] - x0, pos[c][1] - y0)
        cv2.circle(crop, p, 11, (255, 0, 255), 2)
        cv2.putText(crop, str(i + 1), (p[0] - 6, p[1] - 16), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 0, 255), 2)
    scale = min(2.0, 1400 / max(crop.shape[:2]))
    if scale > 1: crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    url = "data:image/png;base64," + base64.b64encode(cv2.imencode(".png", crop)[1]).decode()
    msgs = [{"role": "system", "content": PICK2_SYS},
            {"role": "user", "content": [{"type": "text", "text": "Quali due numeri?"},
                                         {"type": "image_url", "image_url": {"url": url}}]}]
    try:
        raw, _ = rh.chat(msgs)
    except SystemExit:
        return None
    ns = [int(x) for x in re.findall(r"[1-9]", raw) if int(x) <= len(cands)]
    return [cands[ns[0] - 1], cands[ns[1] - 1]] if len(ns) >= 2 and ns[0] != ns[1] else None

def vlm_pick_hole(img, pos, cands, cls):
    """Disambigua il bordo: scelta multipla per il VLM (cerchi numerati, niente conteggi).
    Ritorna il foro scelto o None."""
    import re
    import read_holes as rh
    xs = [pos[c][0] for c in cands]; ys = [pos[c][1] for c in cands]
    x0, y0 = max(0, min(xs) - 80), max(0, min(ys) - 80)
    crop = img[y0:max(ys) + 80, x0:max(xs) + 80].copy()
    for i, c in enumerate(cands):
        p = (pos[c][0] - x0, pos[c][1] - y0)
        cv2.circle(crop, p, 15, (255, 0, 255), 2)     # largo: non deve coprire filo/gamba
        cv2.putText(crop, str(i + 1), (p[0] + 12, p[1] - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 0, 255), 2)
    crop = cv2.resize(crop, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
    url = "data:image/png;base64," + base64.b64encode(cv2.imencode(".png", crop)[1]).decode()
    msgs = [{"role": "system", "content": PICK_SYS.format(cls=cls)},
            {"role": "user", "content": [{"type": "text", "text": "Quale numero?"},
                                         {"type": "image_url", "image_url": {"url": url}}]}]
    try:
        raw, _ = rh.chat(msgs)
    except SystemExit:
        return None
    m = re.search(r"[1-9]", raw)
    if m and int(m.group()) <= len(cands): return cands[int(m.group()) - 1]
    return None

def crop_around(img, xy, w=300, h=230):
    x, y = xy; H, W = img.shape[:2]
    x0, y0 = max(0, x-w//2), max(0, y-h//2)
    return img[y0:min(H, y0+h), x0:min(W, x0+w)]

# ---- Verso dell'opto dalla serigrafia ------------------------------------------
# Un match NCC 0/180 sul crop intero non funziona: nel crop da +-4 passi la
# correlazione la fanno la griglia della breadboard e la sagoma del corpo, che non
# ruotano col pezzo; la scritta, unico segnale del verso, è grigio chiaro su grigio
# chiaro e non sposta l'NCC (misurato: opto capovolti letti dritti con 0.88 vs 0.33).
#
# Il verso si legge da dove sta l'inchiostro dentro il corpo: la serigrafia è tutta
# spostata verso un angolo (logo in alto a destra, sigla e tacca in basso a
# sinistra), quindi il baricentro dei pixel scuri cade fuori dal centro sempre dallo
# stesso lato. Girato il pezzo, il vettore si gira con lui. Confronto = coseno col
# vettore del golden. Misurato su 15 foto d'archivio (6 dritte, 9 capovolte): 15/15,
# dritti cos da +0.42 a +0.98, capovolti tutti sotto -0.99. Soglie nel mezzo, larghe
# e asimmetriche: per bocciare serve -0.5, sotto +0.25 di conferma non si dichiara
# niente.
OPTO_REF = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "campagna_out", "crops", "opto_D5_k5_D5 v.png")
OPTO_BODY = (112, 195, 92, 187)     # y0,y1,x0,x1 del corpo dentro il crop golden 288px
OPTO_COS_KO = -0.50                 # sotto = CAPOVOLTO (bocciante: soglia prudente)
OPTO_COS_OK = +0.25                 # sopra = dritto; in mezzo = verso non leggibile
OPTO_MAG_MIN = 0.05                 # inchiostro troppo centrato = niente da leggere
OPTO_LOC_MIN = 0.45                 # NCC di localizzazione del corpo sotto cui si rinuncia


def _opto_inchiostro(body, mrg=0.12):
    """Baricentro dei pixel scuri dentro il corpo, in frazione di lato e con lo zero
    al centro. `mrg` taglia i bordi: il contorno del corpo è il gradiente più forte
    dell'immagine e senza il taglio si mangia la scritta."""
    b = body.astype(np.float32)
    my, mx = int(b.shape[0] * mrg), int(b.shape[1] * mrg)
    c = b[my:b.shape[0] - my, mx:b.shape[1] - mx]
    if c.size == 0:
        return 0.0, 0.0
    ink = np.clip(cv2.GaussianBlur(c, (0, 0), 9.0) - c, 0, None)   # scuro vs illuminazione
    if ink.max() <= 0:
        return 0.0, 0.0
    ink[ink < ink.max() * 0.25] = 0                                # via il rumore di fondo
    tot = ink.sum()
    if tot <= 0:
        return 0.0, 0.0
    ys, xs = np.mgrid[0:c.shape[0], 0:c.shape[1]]
    return (float((ink * xs).sum() / tot / c.shape[1] - 0.5),
            float((ink * ys).sum() / tot / c.shape[0] - 0.5))


def verso_opto(gray, centro, pitch):
    """Verso dell'optoaccoppiatore. `gray` immagine in grigio, `centro` (x, y) del
    baricentro dei fori attesi, `pitch` passo in px.
    Ritorna ("dritto"|"capovolto"|None, cos): None = non leggibile, mai bocciante."""
    try:
        rimg = cv2.imread(OPTO_REF, cv2.IMREAD_GRAYSCALE)
        if rimg is None or gray is None or not pitch:
            return None, 0.0
        y0, y1, x0, x1 = OPTO_BODY
        ref = rimg[y0:y1, x0:x1]
        sc = pitch / (rimg.shape[0] / 8.0)          # crop golden = +-4 passi
        tpl = cv2.resize(ref, None, fx=sc, fy=sc)
        h, w = tpl.shape
        cx, cy = int(centro[0]), int(centro[1])
        ho = int(2.5 * pitch)
        scene = gray[max(0, cy - ho):cy + ho, max(0, cx - ho):cx + ho]
        if scene.shape[0] < h or scene.shape[1] < w:
            return None, 0.0
        r = cv2.matchTemplate(scene, tpl, cv2.TM_CCOEFF_NORMED)
        _, loc_ncc, _, loc = cv2.minMaxLoc(r)       # il corpo è simmetrico: si trova comunque
        if loc_ncc < OPTO_LOC_MIN:
            return None, 0.0
        roi = cv2.resize(scene[loc[1]:loc[1] + h, loc[0]:loc[0] + w],
                         (ref.shape[1], ref.shape[0]))
        gx, gy = _opto_inchiostro(ref)
        vx, vy = _opto_inchiostro(roi)
        mg, mv = float(np.hypot(gx, gy)), float(np.hypot(vx, vy))
        if mv < OPTO_MAG_MIN or mg <= 0:
            return None, 0.0
        cos = float((vx * gx + vy * gy) / (mv * mg))
        if cos <= OPTO_COS_KO:
            return "capovolto", cos
        if cos >= OPTO_COS_OK:
            return "dritto", cos
        return None, cos
    except Exception:
        return None, 0.0


W_NCC, SR_NCC, THR_S = 11, 4, 0.05          # patch NCC, raggio ricerca, soglia assoluta
SHADOW_MAX, SHADOW_RATIO = 0.14, 0.55       # coda d'ombra: debole in assoluto E decadimento dolce

def legscore(g0, g1, pos, u, v):
    """1 - max NCC del patch del foro tra prev e cur (ricerca +-SR px).
    Invariante a gain/offset: l'auto-esposizione e le ombre scalano la luminosità ma
    conservano la struttura (NCC alta); una gamba nel foro cambia la STRUTTURA (NCC bassa)."""
    if (u, v) not in pos: return 0.0
    x, y = pos[(u, v)]
    t = g0[y-W_NCC:y+W_NCC+1, x-W_NCC:x+W_NCC+1]
    big = g1[y-W_NCC-SR_NCC:y+W_NCC+SR_NCC+1, x-W_NCC-SR_NCC:x+W_NCC+SR_NCC+1]
    if t.size == 0 or big.shape[0] < t.shape[0] or big.shape[1] < t.shape[1]: return 0.0
    r = cv2.matchTemplate(big, t, cv2.TM_CCOEFF_NORMED)
    return 1.0 - float(r.max())

def _sil_of(pts, close=5):
    """Silhouette rasterizzata del blob (chiusa). Ritorna (mask, x0, y0)."""
    x0, y0 = int(pts[:, 0].min()), int(pts[:, 1].min())
    w, h = int(pts[:, 0].max()) - x0 + 1, int(pts[:, 1].max()) - y0 + 1
    m = np.zeros((h, w), np.uint8)
    m[(pts[:, 1] - y0).astype(int), (pts[:, 0] - x0).astype(int)] = 255
    return cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((close, close), np.uint8)), x0, y0

def attraversato(sil, so, p_hole, body_c, pitch, min_hits=3):
    """Test di attraversamento direzionale: un foro col
    filo che PROSEGUE oltre, sul lato OPPOSTO all'arrivo (via dal corpo), è attraversato,
    mai un ingresso; la continuazione VERSO il corpo non squalifica (la gamba continua
    sempre verso il corpo). Margine largo (0.5-1.2 passi oltre il foro): l'overshoot
    corto del capolinea (ombra della punta) non deve squalificare il foro vero.
    min_hits: 3 con la sagoma-diff (le ombre allungano la sagoma: severità minima);
    2 con la sagoma-colore dei fili (niente ombre: ci si fida)."""
    if sil is None or body_c is None: return False
    v = np.array([p_hole[0] - body_c[0], p_hole[1] - body_c[1]], float)
    n = float(np.linalg.norm(v))
    if n < 1: return False
    v /= n
    hit = 0
    for t in (0.5, 0.75, 1.0, 1.2):
        x = int(p_hole[0] + v[0] * t * pitch) - so[0]
        y = int(p_hole[1] + v[1] * t * pitch) - so[1]
        if 0 <= y < sil.shape[0] and 0 <= x < sil.shape[1] \
           and sil[max(0, y - 2):y + 3, max(0, x - 3):x + 4].any(): hit += 1
    return hit >= min_hits

def snap_capo(pos, holes, tip, pitch, score, avoid=(), sil=None, so=None, body_c=None,
              pref_row=None, boost=0.0, min_hits=3):
    """Snap del capo: il capolinea indica il quartiere
    (~1.5 passi), il foro lo sceglie il legscore pesato per vicinanza; un foro
    attraversato non è mai un ingresso; a parità di evidenza vince la riga del
    corpo (pref_row; mai per il diodo, corpo sospeso). Ritorna il foro o None."""
    best = None
    for uv in holes:
        if uv in avoid: continue
        d = np.hypot(pos[uv][0] - tip[0], pos[uv][1] - tip[1])
        if d > 1.5 * pitch: continue
        if attraversato(sil, so, pos[uv], body_c, pitch, min_hits): continue
        w = (score(uv) + boost) / (1 + d / pitch)
        if pref_row is not None and uv[0] == pref_row: w *= 1.1
        if best is None or w > best[0]: best = (w, uv)
    return best[1] if best else None

def fit_pins(g0, g1, pos, holes, cls, pts, img_cur=None, exclude=None, rows6=None,
             img_col=None, img_prev=None):
    """I pin veri sono le GAMBE, che entrano nei fori agli estremi: il foro cambia struttura
    -> legscore alto. Grammatica della classe (montaggio standard): 2-pin stessa riga,
    estremi del supporto; transistor stessa colonna, gambe u/u+2/u+4; jumper estremi
    lungo l'asse del blob. exclude: fori di zone_non_evidenza, trattati come il
    corpo (mai candidabili come pin). img_col/img_prev: coppia a colori cur/prev,
    indipendente dal VLM — alimenta sagoma-colore dei fili, delta-saturazione
    (gamba vs ombra) e cupola dei led."""
    npin = PINS[cls]
    if cls == "bottone":                             # template bottone: centro col che massimizza i 4 corner
        # Si smista sulla classe, non su npin: PINS['rgb'] vale 4 e l'rgb finirebbe qui
        # dentro, rendendo irraggiungibile il suo ramo.
        # Punteggio di classe = il legscore del piedino più debole. Niente due livelli
        # (geometrica dentro la colonna, poi min tra colonne): la geometrica lascia che
        # un foro forte mascheri il compagno vuoto (sqrt(0.45*0.01)=0.067, non zero) e i
        # candidati sbagliati sopravvivono. Col minimo crollano. Misurato su 5 bottoni +
        # 1 opto: stessi vincitori, margine sul secondo classificato da mediana x4.6 a
        # x20.2, peggior caso da x1.6 a x7.7. Semantica: tutti i piedini devono essere
        # entrati, quindi vale il peggiore.
        S = {uv: legscore(g0, g1, pos, *uv) for uv in holes}
        best = None
        for c in sorted({v for u, v in holes if not isinstance(u, str)}):
            pins = [(CE, c-1), (CE, c+1), (CF, c-1), (CF, c+1)]
            sc = min(S.get(p, 0.0) for p in pins)
            _tr("bottone-quad", pins, sc)
            if best is None or sc > best[0]: best = (sc, pins)
        return [p for p in best[1] if p in pos]
    S = {uv: legscore(g0, g1, pos, *uv) for uv in holes}
    def sup_line(line):
        """Supporto di una linea: run sopra soglia contenente il massimo, con ponte sui
        dip di UN foro (il filo scavalca un foro tra gamba e corpo). Ritorna (fori, score)."""
        vals = [S[uv] for uv in line]
        i = int(np.argmax(vals))
        lo = hi = i
        while lo > 0 and (vals[lo-1] >= THR_S or (lo > 1 and vals[lo-2] >= 2*THR_S)): lo -= 1
        while hi < len(vals)-1 and (vals[hi+1] >= THR_S or (hi < len(vals)-2 and vals[hi+2] >= 2*THR_S)): hi += 1
        sup = list(line[lo:hi+1])
        return sup, sum(S[uv] for uv in sup)

    def resolve_end(sup, side):
        """Bordo ambiguo (fori deboli <= SHADOW_MAX: gamba sottile o coda d'ombra?):
        chiede al VLM su scelta multipla. NB: il gate resta STRETTO apposta — mandare al
        VLM anche bordi con punteggi forti/piatti peggiora (rompe bordi giusti)."""
        idx = range(len(sup)) if side == 0 else range(len(sup)-1, -1, -1)
        cands = []
        for i in idx:
            cands.append(sup[i])
            if S[sup[i]] > SHADOW_MAX: break
        if len(cands) < 2: return sup[0 if side == 0 else -1]
        cands = cands[:3]
        pick = vlm_pick_hole(img_cur, pos, cands, cls) if img_cur is not None else None
        return pick if pick is not None else cands[0]  # fallback: estremo assoluto
    grid = [uv for uv in holes if not isinstance(uv[0], str)]   # le rail entrano solo per FREE2
    body = body_holes(pts, pos, holes) if pts is not None and len(pts) else set()   # fori sotto il corpo
    if exclude: body = body | set(exclude)                      # zone escluse
    def dsat(uv):
        """Delta saturazione al foro tra prev e cur: una gamba metallica nuda cambia
        poco la saturazione del patch, un corpo/ombra la cambia di più (misurato:
        gamba 13-17 vs corpo/ombra 18-26). None se mancano le immagini a colori."""
        if img_col is None or img_prev is None or uv not in pos: return None
        x, y = pos[uv]
        p0 = img_prev[y-W_NCC:y+W_NCC+1, x-W_NCC:x+W_NCC+1]
        p1 = img_col[y-W_NCC:y+W_NCC+1, x-W_NCC:x+W_NCC+1]
        if p0.size == 0 or p0.shape != p1.shape: return None
        s0 = cv2.cvtColor(p0, cv2.COLOR_BGR2HSV)[:, :, 1].astype(np.float32)
        s1 = cv2.cvtColor(p1, cv2.COLOR_BGR2HSV)[:, :, 1].astype(np.float32)
        return float(np.abs(s1 - s0).mean())
    rows = {u: sorted((uv for uv in grid if uv[0] == u), key=lambda t: t[1])
            for u in {u for u, _ in grid}}
    if npin == 3 and cls in ("trimmer", "potenziometro"):
        # Il trimmer non è 0-2-4 in linea: footprint a
        # TRIANGOLO (estremi c-1/c+1 su una riga, cursore in colonna c sulla riga
        # adiacente), ANCORATO AL CORPO: i pin stanno sotto il quadrato blu, il
        # segnale lì è corpo non gambe -> il triangolo si sceglie tra quelli
        # vicini al baricentro della parte spessa (deduzione, mai caccia libera).
        if pts is not None and len(pts):
            sil_t, tx0, ty0 = _sil_of(pts)
            er_t = cv2.erode(sil_t, np.ones((19, 19), np.uint8))
            ys_t, xs_t = np.nonzero(er_t)
            anc_t = ((float(xs_t.mean()) + tx0, float(ys_t.mean()) + ty0) if len(xs_t)
                     else (float(pts[:, 0].mean()), float(pts[:, 1].mean())))
        else:
            anc_t = None
        pt_ = abs(pos[(0, 1)][0] - pos[(0, 0)][0]) or 29
        best = None
        for (u, v) in grid:
            for du in (1, -1):
                tri = [(u, v - 1), (u, v + 1), (u + du, v)]
                if any(t not in S for t in tri): continue
                if len({t[0] <= 4 for t in tri}) > 1: continue   # mai a cavallo del canale
                if anc_t is not None and np.hypot(pos[(u, v)][0] - anc_t[0],
                                                  pos[(u, v)][1] - anc_t[1]) > 2.5 * pt_:
                    continue
                sc = sum(S[t] for t in tri)
                _tr("trimmer-tri", tri, sc)
                if best is None or sc > best[0]: best = (sc, tri)
        if best: return sorted(best[1], key=lambda uv: (ru(uv[0]), uv[1]))
        return sorted(grid, key=lambda uv: -S[uv])[:3]
    if npin == 3:
        # transistor: passo standard 0-2-4, orizzontale o verticale. Ancora = pin più
        # forte NON-corpo, poi si provano le 4 direzioni spaziali; vince quella con più
        # segnale sui 3 fori. Ammesse anche le terne compresse 0-2-3/0-1-3 (gamba
        # ripiegata sul foro adiacente);
        # gli ESTREMI della terna mai su fori-corpo; std vs compressa si decide col
        # delta-saturazione del foro conteso (gamba metallica = dSat basso).
        anchors = sorted((uv for uv in grid if uv not in body), key=lambda uv: -S[uv])[:4]
        best = bestc = None
        for (u, v) in anchors:
            for du, dv in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                for steps in ((0, 2, 4), (0, 2, 3), (0, 1, 3)):
                    tri = [(u + du * s, v + dv * s) for s in steps]
                    if any(t not in S for t in tri): continue
                    if du and len({t[0] <= 4 for t in tri}) > 1: continue   # mai a cavallo del canale
                    ends = (tri[0], tri[-1])
                    # Estremo sotto il corpo esclude solo se anche muto (< THR_S): un foro
                    # geometricamente "corpo" ma con segnale reale è una gamba vera vicina
                    # al bordo del corpo (misurato: S=0.18 dentro la dilatazione di
                    # body_holes, scartato a priori pur essendo il vero piedino). La sola
                    # geometria non deve battere un segnale reale.
                    if any(t in body and S[t] < THR_S for t in ends): continue
                    sc = sum(S[t] for t in tri)
                    _tr("transistor-tri", tri, sc)
                    if steps == (0, 2, 4):
                        if best is None or sc > best[0]: best = (sc, tri)
                    else:
                        if bestc is None or sc > bestc[0]: bestc = (sc, tri)
        pick = best or bestc
        if best and bestc:
            shared = set(best[1]) & set(bestc[1])
            if len(shared) == 2:                     # stesso ancoraggio, conteso solo l'estremo
                h_std = next(t for t in best[1] if t not in shared)
                h_cmp = next(t for t in bestc[1] if t not in shared)
                d_std, d_cmp = dsat(h_std), dsat(h_cmp)
                if d_std is not None and d_cmp is not None and d_cmp < d_std:
                    pick = bestc
                    print(f"[terna compressa: {name(*h_cmp)} (dSat {d_cmp:.0f}) batte "
                          f"{name(*h_std)} (dSat {d_std:.0f})] ", end="")
            # ancoraggi diversi o niente colore: resta la standard (status quo)
        if pick is None: return sorted(grid, key=lambda uv: -S[uv])[:3]
        if min(S[t] for t in pick[1]) < 0.05:        # terna con un foro muto: non inventare in silenzio
            top = sorted(grid, key=lambda uv: -S[uv])[:5]
            print(f"[terna incerta, segnale alto su: {', '.join(name(*uv) for uv in top)} - VERIFICA] ", end="")
        return sorted(pick[1], key=lambda uv: (ru(uv[0]), uv[1]))
    if cls == "rgb":                                 # led RGB: 4 gambe CONSECUTIVE stessa riga
        # Àncora sul piede pulito. La somma dei quattro perde quando la cupola copre
        # un foro appena fuori dalla quadrupla (foro-corpo a 0.95 + tre piedi batteva la
        # quadrupla vera 2.41 contro 1.68) e il pick esce slittato di una colonna.
        # Il colore non aiuta: la regola-cupola del led cerca pixel saturi, e un RGB
        # spento è bianco traslucido (frazione di saturi 0.000 su tutti i fori). Né
        # aiuta body_holes, che sulla cupola traslucida esce vuota, né dSat (14.9
        # sulla cupola contro 14.3-15.9 sulle gambe: indistinguibili).
        # Discriminante geometrico: un piede è un punto, un corpo sfonda nella riga
        # accanto. Misurato sull'archivio, il vicino di riga dei piedi veri: rgb max
        # 0.012, fotoresistenza 0.030, bottone 0.171, mentre sotto una cupola vale
        # 0.702. Si ancora al piede pulito più forte e si mappa in una direzione.
        #
        # Tarato su un caso: l'archivio ha una sola commessa con RGB e due fotogrammi.
        # Su uno questo ramo prende il golden esatto; sull'altro la cupola non sfonda
        # (vicino 0.005, pezzo dritto invece che coricato), nessun foro risulta corpo e
        # l'àncora ricadrebbe sulla cupola. Per questo il ramo decide solo quando trova
        # almeno un foro-corpo: senza, si torna alla somma, che è il comportamento
        # già misurato.
        corpo_rgb, puliti = set(), []
        for u, line in rows.items():
            for t in line:
                if S.get(t, 0.0) < THR_S: continue
                vic = [S[n] for n in ((u - 1, t[1]), (u + 1, t[1]))
                       if n in S and ru(n[0]) // 5 == ru(u) // 5]   # stessa metà board
                if vic and max(vic) >= THR_S: corpo_rgb.add(t)
                else: puliti.append(t)
        if corpo_rgb and puliti:
            anc = max(puliti, key=lambda t: S[t])
            best = None
            for off in range(4):                     # le 4 posizioni dell'ancora nella quadrupla
                quad = [(anc[0], anc[1] - off + i) for i in range(4)]
                if any(t not in S for t in quad): continue
                vivi = sum(1 for t in quad if S[t] >= THR_S)
                if vivi < 4: continue                # una quadrupla vera ha 4 fori accesi
                sc = (sum(1 for t in quad if t in puliti), sum(S[t] for t in quad))
                _tr("rgb-ancora", quad, sc[1])
                if best is None or sc > best[0]: best = (sc, quad)
            if best:
                print(f"[rgb: ancora su {name(*anc)}, corpo su "
                      f"{','.join(name(*t) for t in sorted(corpo_rgb))}] ", end="")
                return best[1]
        best = None
        for u, line in rows.items():
            for (_, v) in line:
                quad = [(u, v + i) for i in range(4)]
                if any(t not in S for t in quad): continue
                sc = sum(S[t] for t in quad)
                _tr("rgb-quad", quad, sc)
                if best is None or sc > best[0]: best = (sc, quad)
        if best: return best[1]
    if npin == 6:                                    # opto DIP-6: 3 colonne x 2 righe a cavallo del canale
        # Coppie di riga valide a cavallo del gap centrale: E<->G, F<->D (mai E<->F:
        # il passo dei piedini scavalca il canale con 1 riga di offset, non simmetrico
        # sul gap). rows6 (dal golden, se noto) resta un vincolo diretto; altrimenti si
        # provano entrambe le coppie, come il bottone prova tutte le colonne: il ramo
        # cieco funziona anche senza aggancio al golden.
        coppie = [rows6] if rows6 else [(CE, CG), (CF, CD)]
        best = None
        for r6a, r6b in coppie:
            for v in {v for _, v in grid}:
                six = [(r6a, v + i) for i in range(3)] + [(r6b, v + i) for i in range(3)]
                if any(t not in S for t in six): continue
                # Come il bottone: il punteggio è il piedino più debole dei sei. Il min tra
                # le colonne di una geometrica di coppia lascia passare una colonna mezza
                # vuota; col minimo secco si pretende che tutti e sei i fori siano occupati,
                # ed è la difesa che serve qui: una gamba che attraversa un foro senza
                # entrarci acceca un solo lato (misurato: riga a 1.1 di pura attraversata).
                # Effetto sull'opto del dataset: margine da x1.6 a x10.0.
                sc = min(S[t] for t in six)
                _tr("opto-six", six, sc)
                if best is None or sc > best[0]: best = (sc, six)
        if best: return sorted(best[1], key=lambda uv: (ru(uv[0]), uv[1]))
        return sorted(grid, key=lambda uv: -S[uv])[:6]
    best = None                                      # 2-pin: estremi del supporto sulla LINEA migliore
    for u, line in rows.items():
        if len(line) < 2: continue
        sup, sc = sup_line(line)
        if len(sup) >= 2: _tr("2pin-line", sup, sc)
        if len(sup) >= 2 and (best is None or sc > best[0]): best = (sc, sup)
    # Per i fili (FREE2) la sagoma è primaria: il colore non guida l'inseguimento,
    # certifica l'identità (blob_color in QC). I fili passano dal ramo B (estremi
    # geodetici della sagoma + snap con attraversamento/indietro); l'inseguitore a
    # colore resta come riserva dichiarata più sotto, quando la sagoma non dà due
    # capi. La sagoma del filo viene dal colore quando c'è: il diff-blob ingloba le
    # ombre (tips spostati, capi veri squalificati come corpo), il colore saturo del
    # filo le ombre non le ha.
    pts_geo = pts
    if cls in FREE2 and img_col is not None and pts is not None and len(pts):
        cw = colored_wire(img_col, pts)
        if cw is not None:
            pts_geo = cw[0]
            print("[sagoma dal COLORE del filo] ", end="")
    # --- 2-pin standard: doppio candidato ---
    # A) grammatica stessa-riga (ottima per componenti in griglia; mai per i fili ad arco)
    candA = None
    if cls not in FREE2 and best is not None:
        sup = best[1]
        candA = [resolve_end(sup, 0), resolve_end(sup, 1)]
        if candA[0] == candA[1]: candA = None
    # B) CONTINUITÀ della sagoma: pin-metallo-corpo-metallo-pin, estremi geodetici del
    #    blob -> copre gambe diagonali e verso le RAIL (ldr, resistenze al +/-)
    if candA and any(uv in body for uv in candA): candA = None   # mai pin sotto il corpo
    candB = None
    def gap_bands():
        """Fasce y dei solchi della board (dalla mappa): canale e-f, blocco-rail alto/basso."""
        try:
            ymed = lambda u: float(np.median([pos[(u, v)][1] for v in range(og.NCOLS) if (u, v) in pos]))
            rt = np.median([p[1] for k, p in pos.items() if isinstance(k[0], str) and k[0].endswith("t")])
            rb = np.median([p[1] for k, p in pos.items() if isinstance(k[0], str) and k[0].endswith("b")])
            return [(ymed(6), ymed(4)), (float(rt), ymed(10)), (ymed(0), float(rb))]
        except Exception:
            return []
    tips = mask_endpoints(pts_geo, gap_bands())
    pitch = abs(pos[(0, 1)][0] - pos[(0, 0)][0]) or 29
    # Sagoma rasterizzata + baricentro del corpo (parte spessa) per il test
    # di attraversamento e la preferenza di riga.
    sil = so = body_c = thick_m = None
    if pts_geo is not None and len(pts_geo):
        sil, sx0_, sy0_ = _sil_of(pts_geo)
        so = (sx0_, sy0_)
        er_ = cv2.erode(sil, np.ones((19, 19), np.uint8))
        ys_, xs_ = np.nonzero(er_)
        if len(xs_):
            thick_m = er_
            body_c = (float(xs_.mean()) + so[0], float(ys_.mean()) + so[1])
        else:
            body_c = (float(pts_geo[:, 0].mean()), float(pts_geo[:, 1].mean()))
    if pts_geo is not pts:                          # sagoma-colore -> corpo dal colore
        body = body_holes(pts_geo, pos, holes) | (set(exclude) if exclude else set())
    if tips:
        # Eccezione-tip (specchia zone_non_evidenza): il capo di un filo/gamba supera
        # lo spessore del corpo ma è il pezzo, quindi i fori entro 1.6 passi da un tip
        # restano candidabili (misurato: capi veri a 1.54 passi squalificati a torto
        # come corpo).
        body = {uv for uv in body
                if all(np.hypot(pos[uv][0] - tx, pos[uv][1] - ty) > 1.6 * pitch
                       for tx, ty in tips)}
    if pts_geo is not pts:
        # Filo colorato che attraversa un foro = occlusione, mai un pin (misurato:
        # fori attraversati caldi a 0.55-0.96 perché coperti dalla plastica di
        # passaggio). Un foro d'ingresso ha la plastica solo
        # dal lato del corpo (il filo si piega DENTRO il foro): si esclude SOLO se il
        # colore prosegue da entrambe le parti (risultante angolare bassa).
        cov_ = set()
        for uv in holes:
            x_, y_ = pos[uv]
            d_ = np.hypot(pts_geo[:, 0] - x_, pts_geo[:, 1] - y_)
            if int(np.sum(d_ <= 6)) < 10: continue
            ring_ = pts_geo[(d_ > 0.35 * pitch) & (d_ < 1.2 * pitch)]
            if len(ring_) < 10: continue
            ang_ = np.arctan2(ring_[:, 1] - y_, ring_[:, 0] - x_)
            if float(np.hypot(np.cos(ang_).mean(), np.sin(ang_).mean())) < 0.6:
                cov_.add(uv)
        if cov_:
            body = body | cov_
            print(f"[filo che attraversa {len(cov_)} fori: esclusi] ", end="")
    if cls == "led" and img_col is not None and pts is not None and len(pts):
        # Cupola colorata = corpo: il patch di un foro sotto la cupola è saturo
        # (S 0.7-0.8 = plastica, gambe vere a 0.12); le gambe sono metallo nudo,
        # mai sature. Esclusione dura, dopo i tip.
        hsv_ = cv2.cvtColor(img_col, cv2.COLOR_BGR2HSV)
        satm_ = (hsv_[:, :, 1] > 110) & (hsv_[:, :, 2] > 60)
        dome_ = set()
        for uv in holes:
            x_, y_ = pos[uv]
            p_ = satm_[y_-W_NCC:y_+W_NCC+1, x_-W_NCC:x_+W_NCC+1]
            if p_.size and float(p_.mean()) >= 0.30: dome_.add(uv)   # 0.30: bordo-cupola f36=0.41
        if dome_:
            body = body | dome_
            print(f"[cupola led: esclusi {len(dome_)} fori saturi] ", end="")
    # Riga del corpo = preferenza; mai per il diodo (corpo sospeso proiettato su una
    # riga diversa da quella dei pin, misurato) né per i fili (nessun corpo, l'arco
    # va dove vuole, rail comprese).
    pref_row = None
    if cls not in FREE2 and cls != "diodo" and thick_m is not None and grid:
        nb_ = min(grid, key=lambda uv: np.hypot(pos[uv][0] - body_c[0], pos[uv][1] - body_c[1]))
        pref_row = nb_[0]
    # Condensatore coricato: il diff-blob prende il corpo e non arriva alle gambe
    # sottili (misurato): il quartiere si ancora agli estremi del corpo
    # lungo il suo asse (estesi ~1 passo verso fuori), non ai capi geodetici.
    if cls in ("condensatore", "cap") and thick_m is not None:
        P_ = np.column_stack(np.nonzero(thick_m)[::-1]).astype(np.float32)
        (ccx_, ccy_), (rw_, rh_), ang_ = cv2.minAreaRect(P_)
        if min(rw_, rh_) >= 1 and max(rw_, rh_) / min(rw_, rh_) >= 1.8:
            th_ = np.deg2rad(ang_ if rw_ >= rh_ else ang_ + 90)
            ax_ = np.array([np.cos(th_), np.sin(th_)])
            c0_ = np.array([ccx_ + so[0], ccy_ + so[1]])
            ext_ = max(rw_, rh_) / 2 + 0.9 * pitch
            tips = [tuple(c0_ + ax_ * ext_), tuple(c0_ - ax_ * ext_)]
            print("[cap: quartiere ancorato agli estremi del corpo] ", end="")
    mh = 2 if pts_geo is not pts else 3      # sagoma-colore: attraversamento severo (no ombre)
    if tips and cls == "cavetto" and pts_geo is not pts and img_col is not None:
        # Guaina nera al capo del cavetto: la sagoma-colore si
        # ferma DOVE inizia la guaina, ma il pin sta al capo OPPOSTO della guaina ->
        # il tip avanza al di la' della guaina lungo il suo asse (verso via dal corpo).
        newt = []
        for tp in tips:
            ax_ = np.array([tp[0] - body_c[0], tp[1] - body_c[1]], float)
            n_ = float(np.linalg.norm(ax_))
            ax_ = ax_ / n_ if n_ >= 1 else None
            g_ = guaina_v2(img_col, tp, ax_, pitch)
            if g_ is not None:
                (gc_, ga_) = g_
                sgn_ = 1.0 if float((gc_[0] - body_c[0]) * ga_[0]
                                    + (gc_[1] - body_c[1]) * ga_[1]) >= 0 else -1.0
                tp = (gc_[0] + sgn_ * ga_[0] * 0.6 * pitch,
                      gc_[1] + sgn_ * ga_[1] * 0.6 * pitch)
                print("[tip oltre la guaina] ", end="")
            newt.append(tp)
        tips = newt
    if tips and cls == "jumper" and pts_geo is not pts:
        # Capo nudo del jumper: il colore finisce dove finisce la plastica, ma il
        # metallo nudo prosegue fino al foro (misurato: capi letti nel foro accanto o
        # sulla rail sbagliata). Il tip avanza lungo l'asse del filo finché il
        # punto è nella sagoma-DIFF (metallo = novità) ED è LUMINOSO (l'ombra è
        # scura, la board non sta nel diff). Mai oltre 2.5 passi.
        dsil_, dx0_, dy0_ = _sil_of(pts)
        hsvV_ = cv2.cvtColor(img_col, cv2.COLOR_BGR2HSV)[:, :, 2]
        newt = []
        for tp in tips:
            near_ = pts_geo[np.hypot(pts_geo[:, 0] - tp[0], pts_geo[:, 1] - tp[1]) < 1.5 * pitch]
            dirv_ = (np.array([tp[0] - near_[:, 0].mean(), tp[1] - near_[:, 1].mean()])
                     if len(near_) >= 5 else np.array([tp[0] - body_c[0], tp[1] - body_c[1]]))
            n_ = float(np.linalg.norm(dirv_))
            if n_ < 1:
                newt.append(tp); continue
            dirv_ = dirv_ / n_
            bt_, t_ = 0.0, 0.25
            while t_ <= 2.5:
                x_ = int(tp[0] + dirv_[0] * t_ * pitch); y_ = int(tp[1] + dirv_[1] * t_ * pitch)
                xs_, ys_ = x_ - dx0_, y_ - dy0_
                insil_ = (0 <= ys_ < dsil_.shape[0] and 0 <= xs_ < dsil_.shape[1]
                          and bool(dsil_[max(0, ys_ - 3):ys_ + 4, max(0, xs_ - 3):xs_ + 4].any()))
                pv_ = hsvV_[max(0, y_ - 3):y_ + 4, max(0, x_ - 3):x_ + 4]
                if insil_ and pv_.size and float(pv_.mean()) > 130:
                    bt_ = t_
                elif t_ - bt_ > 0.75:
                    break
                t_ += 0.25
            if bt_ > 0:
                tp = (tp[0] + dirv_[0] * bt_ * pitch, tp[1] + dirv_[1] * bt_ * pitch)
                print("[tip esteso sul metallo nudo] ", end="")
            newt.append(tp)
        tips = newt
    if tips:
        def on_sil(h):
            if sil is None: return False
            x_, y_ = pos[h][0] - so[0], pos[h][1] - so[1]
            return (0 <= y_ < sil.shape[0] and 0 <= x_ < sil.shape[1]
                    and bool(sil[max(0, y_ - 2):y_ + 3, max(0, x_ - 3):x_ + 4].any()))
        candB = []
        for i, tp in enumerate(tips):
            h = snap_capo(pos, holes, tp, pitch, lambda uv: S[uv], avoid=body,
                          sil=sil, so=so, body_c=body_c, pref_row=pref_row, min_hits=mh)
            sc = S.get(h, 0.0) if h is not None else 0.0
            if sc < 0.20 and len(tips) == 2:
                # Capo debole -> ricerca all'indietro
                # lungo il percorso (mai oltre l'apice): fori SULLA sagoma entro ~3 passi
                # dal capolinea, dal più vicino; salta corpo e attraversati; il primo
                # caldo vince. Avanti-al-tip = riserva, sempre dichiarata.
                def lato(hh):
                    return (np.hypot(pos[hh][0] - tp[0], pos[hh][1] - tp[1]) <
                            np.hypot(pos[hh][0] - tips[1 - i][0], pos[hh][1] - tips[1 - i][1]))
                back = sorted((hh for hh in holes
                               if hh not in body and hh != h and lato(hh) and on_sil(hh)
                               and np.hypot(pos[hh][0] - tp[0], pos[hh][1] - tp[1]) <= 3 * pitch
                               and not attraversato(sil, so, pos[hh], body_c, pitch, mh)),
                              key=lambda hh: np.hypot(pos[hh][0] - tp[0], pos[hh][1] - tp[1]))
                alt = next((hh for hh in back if S[hh] >= max(0.10, 1.6 * sc)), None)
                if alt is not None:
                    print(f"[capo arretrato lungo il filo: {name(*alt)}] ", end="")
                else:
                    fw = [hh for hh in holes
                          if hh not in body and hh != h and lato(hh) and not on_sil(hh)
                          and np.hypot(pos[hh][0] - tp[0], pos[hh][1] - tp[1]) <= 1.5 * pitch]
                    alt = max(fw, key=lambda hh: S[hh], default=None)
                    if alt is not None and S[alt] >= max(0.10, 1.6 * sc):
                        print(f"[capo avanti al tip: {name(*alt)} (riserva dichiarata)] ", end="")
                    else:
                        alt = None
                if alt is not None: h = alt
            candB.append(h)
        if None in candB or candB[0] == candB[1]: candB = None
    # la continuità della sagoma (B) è il principio primario;
    # la grammatica stessa-riga (A) è la riserva quando la sagoma non dà due capi validi
    ends = candB or candA
    if not ends and cls in FREE2 and img_cur is not None:
        # Riserve dichiarate dei fili quando la sagoma non dà due capi:
        # 1) inseguitore a COLORE (vecchia pipeline, ora riserva) 2) scelta multipla VLM
        we = wire_endpoints(img_cur, pts)
        if we:
            e2 = [snap_capo(pos, holes, p, pitch, lambda uv: S[uv], avoid=body,
                            sil=sil, so=so, body_c=body_c) for p in we]
            if None not in e2 and e2[0] != e2[1]:
                ends = e2
                print("[capi dall'inseguitore a colore (riserva dichiarata)] ", end="")
        if not ends:
            hot6 = sorted((uv for uv in holes if S[uv] >= max(1.6 * THR_S, 0.08)
                           and uv not in body), key=lambda uv: -S[uv])[:6]
            pk = vlm_pick_pair(img_cur, pos, hot6, cls) if len(hot6) >= 2 else None
            if pk:
                ends = pk
                print("[capi dal VLM (riserva dichiarata)] ", end="")
    if not ends:                                     # fallback: i 2 fori NON-corpo con score massimo
        return sorted((uv for uv in holes if uv not in body), key=lambda uv: -S[uv])[:2]
    # capo fuori dalla riga del corpo possibile ma sempre segnalato
    if pref_row is not None:
        for uv in ends:
            if not isinstance(uv[0], str) and uv[0] != pref_row:
                print(f"[capo {name(*uv)} fuori dalla riga del corpo"
                      f" ({U_ROW[pref_row]}): dichiarato] ", end="")
    if min(S.get(ends[0], 0), S.get(ends[1], 0)) < 0.05:
        print("[pin debole: VERIFICA] ", end="")
    return sorted(ends, key=lambda uv: (ru(uv[0]), uv[1]))

# ====================== MODALITÀ QC vs GOLDEN ======================
# Non si scopre: si VERIFICA. Il manuale dice classe+fori attesi per step; qui si controlla
# il segnale (legscore) sui fori ATTESI -> PASS / STIMATO / FAIL. Su FAIL si accende il
# "radar" (lettura cieca del diff) per spiegare la deviazione. Niente caccia, VLM quasi zero.

QC_THR, QC_WEAK = 0.08, 0.04     # foro atteso: >=THR occupato, <WEAK muto, in mezzo debole

# Soglie per classe, applicate al punteggio di classe (coppia/terna/cerchio), non
# al singolo foro, che sotto WEAK resta muto e mai promosso (regola dura). Classi
# assenti -> default QC_THR/QC_WEAK.
QC_CLS = {"resistenza": (0.07, 0.035), "led": (0.07, 0.035), "rgb": (0.07, 0.035),
          "bottone": (0.07, 0.035),
          "diodo": (0.055, 0.03), "transistor": (0.055, 0.03),
          "optoaccoppiatore": (0.055, 0.03),
          "fotoresistenza": (0.05, 0.025),
          "jumper": (0.08, 0.04), "cavetto": (0.08, 0.04)}

def cls_thr(cls):
    """(THR, WEAK) della classe per il punteggio di classe."""
    return QC_CLS.get(cls, (QC_THR, QC_WEAK))

def pair_score(ss):
    """Combinatore coppia/terna di colonna: media geometrica dei legscore (margine
    mediano 1.63 vs 1.50 del min, mai in disaccordo sul vincitore); un foro a zero
    azzera la coppia (mai promozione)."""
    ss = [max(float(s), 0.0) for s in ss]
    if not ss or min(ss) <= 0.0: return 0.0
    return float(np.exp(np.mean(np.log(ss))))

# ================= Classi da corpo (trimmer, buzzer, cap verticale) =================
# Sotto il corpo il legscore non dimostra nulla (misurato: coperti p50 0.11-0.71, cap
# verticale quasi muto p50 0.11): il punteggio di classe è la geometria del corpo —
# maschera blu satura (trimmer, area >= 2000 px), cerchio Hough a taglia nota (camera
# fissa -> raggio px costante: buzzer r~68-70, top del cap verticale r~40-42).
# Base dei pin = centro corpo + offset per-commessa misurato su una run di
# riferimento in posizione standard (il coefficiente radiale 0.03-0.14 è troppo
# sparso per un modello generale; il valore unico è solo ripiego dichiarato).
# Offset in passi (dx, dy) = attesi_golden - centro_corpo, con camera e board fissi.
OFF_CORPO = {("F3", "trimmer"): (0.65, -0.17),
             ("D1", "buzzer"): (1.61, -0.15),
             ("D4", "buzzer"): (0.02, -0.10),
             ("D4", "condensatore"): (2.54, 0.94)}
K_RADIALE = 0.08                 # ripiego per commesse non calibrate (misurato 0.03-0.14)
CORPO_TOL = 1.5                  # passi: base oltre -> corpo fuori posto = FAIL

def hough_taglia_nota(gray, rmin, rmax):
    """Cerchio Hough a taglia nota (agganciato 8/8 in campagna).
    Ritorna (x, y, r) del cerchio più vicino al centro del crop, o None."""
    c = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, dp=1, minDist=200,
                         param1=80, param2=30, minRadius=rmin, maxRadius=rmax)
    if c is None: return None
    h, w = gray.shape
    return min(c[0], key=lambda t: (t[0] - w / 2) ** 2 + (t[1] - h / 2) ** 2)

def corpo_base(cls, cid, img, bbox, pitch):
    """Aggancia il corpo del pezzo nella zona del blob e predice la base
    dei pin con l'offset per-commessa. Ritorna (metodo, base_pred, ripiego) o None
    (detector non agganciato). La finestra è ancorata al BLOB (posizione reale del
    pezzo, non all'atteso): un corpo fuori posto si aggancia dov'è."""
    cx, cy = (bbox[0] + bbox[2]) // 2, (bbox[1] + bbox[3]) // 2
    centro = met = raggio = None
    if cls == "trimmer":
        x0, y0 = max(0, bbox[0] - 40), max(0, bbox[1] - 40)
        sub = img[y0:bbox[3] + 40, x0:bbox[2] + 40]
        if sub.size:
            hsv = cv2.cvtColor(sub, cv2.COLOR_BGR2HSV)
            m = np.zeros(sub.shape[:2], np.uint8)
            for lo, hi in HUES["blu"]:
                m |= cv2.inRange(hsv, (lo, 100, 60), (hi, 255, 255))
            m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
            n, _, stats, cent = cv2.connectedComponentsWithStats(m)
            if n > 1:
                i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
                if stats[i, cv2.CC_STAT_AREA] >= 2000:
                    centro = (cent[i][0] + x0, cent[i][1] + y0)
                    met = f"maschera blu {int(stats[i, cv2.CC_STAT_AREA])}px"
    else:                                # buzzer / condensatore verticale: cerchio del top
        rmin, rmax = (55, 85) if cls == "buzzer" else (25, 55)
        half = int((8 if cls == "buzzer" else 6) * pitch)
        sub = cv2.cvtColor(img[max(0, cy - half):cy + half, max(0, cx - half):cx + half],
                           cv2.COLOR_BGR2GRAY)
        if sub.size:
            c = hough_taglia_nota(cv2.medianBlur(sub, 5), rmin, rmax)
            if c is not None:
                centro = (c[0] + cx - half, c[1] + cy - half)
                raggio = float(c[2])
                met = f"cerchio r={c[2]:.0f}px"
    if centro is None: return None
    off = OFF_CORPO.get((cid, cls))
    ripiego = off is None
    if ripiego:                          # k radiale: verso il centro immagine
        icx, icy = img.shape[1] / 2, img.shape[0] / 2
        off = (K_RADIALE * (icx - centro[0]) / pitch, K_RADIALE * (icy - centro[1]) / pitch)
    base = (centro[0] + off[0] * pitch, centro[1] + off[1] * pitch)
    # 4o elemento: centro e raggio del corpo, per il conteggio righe di
    # radar_operativa._righe_coperte. Serve una misura che non passi dall'offset
    # per-commessa qui sopra, tarato su una sola run e con errore paragonabile allo
    # spostamento da rilevare. I chiamanti esistenti spacchettano i primi tre elementi.
    return met, base, ripiego, {"centro": centro, "r": raggio}

# Bande delle resistenze: il kit monta resistenze a 5 bande, ultima = tolleranza
# marrone (una tabella a 4 bande+oro contava MISS anche le letture giuste).
# Verificati sul campo: R220, R10k. R10 col moltiplicatore oro: non verificato.
# Uso: solo segnala-mismatch/declassa, mai bocciante, mai fidarsi del valore letto.
BANDS = {"R10":  ["marrone", "nero", "nero", "oro", "marrone"],
         "R100": ["marrone", "nero", "nero", "nero", "marrone"],
         "R220": ["rosso", "rosso", "nero", "nero", "marrone"],
         "R330": ["arancione", "arancione", "nero", "nero", "marrone"],
         "R1k":  ["marrone", "nero", "nero", "marrone", "marrone"],
         "R2k":  ["rosso", "nero", "nero", "marrone", "marrone"],
         "R5k1": ["verde", "marrone", "nero", "marrone", "marrone"],
         "R10k": ["marrone", "nero", "nero", "rosso", "marrone"],
         "R100k": ["marrone", "nero", "nero", "arancione", "marrone"],
         "R1M":  ["marrone", "nero", "nero", "giallo", "marrone"]}
BAND_WORDS = {"nero", "marrone", "rosso", "arancione", "giallo", "verde", "blu", "viola", "oro"}
BANDS_SYS = """Foto ravvicinata di UNA resistenza su breadboard. Le resistenze di questo
kit hanno 5 BANDE colorate: leggile in ordine da un'estremita' all'altra (l'ultima
banda, la tolleranza, e' MARRONE e spesso un po' staccata dalle altre).
Rispondi SOLO coi colori separati da trattini, esempio: rosso-rosso-nero-nero-marrone.
Colori possibili: nero, marrone, rosso, arancione, giallo, verde, blu, viola, oro.
Se le bande non si leggono rispondi: illeggibile."""

def vlm_bands(crop):
    """Legge le bande della resistenza (non validata sul dataset: il chiamante la usa
    per declassare, mai per bocciare da sola)."""
    import re
    import read_holes as rh
    url = "data:image/png;base64," + base64.b64encode(cv2.imencode(".png", crop)[1]).decode()
    msgs = [{"role": "system", "content": BANDS_SYS},
            {"role": "user", "content": [{"type": "text", "text": "Che bande ha?"},
                                         {"type": "image_url", "image_url": {"url": url}}]}]
    try:
        raw, _ = rh.chat(msgs)
    except SystemExit:
        return None
    w = raw.strip().lower()
    if "illeg" in w: return None
    # l'oro non si scarta (nei codici a 5 bande è il moltiplicatore di R10)
    cols = [c for c in re.split(r"[-,;\s]+", w) if c in BAND_WORDS]
    return cols or None

def parse_hole(s):
    s = s.strip().lower()
    return (og.ROW_U[s[0]], int(s[1:]) - 1)


def _cerca_golden():
    """Il golden risalendo le cartelle: regge gli spostamenti dell'albero del progetto."""
    import os
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        for sotto in ("5-processo-e-manuale/manuale", "docs/manuale", "manuale"):
            p = os.path.join(d, *sotto.split("/"), "golden-data.json")
            if os.path.exists(p):
                return p
        d = os.path.dirname(d)
    return None

def load_golden(cid):
    import json, os
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (_cerca_golden(), os.path.join(here, "golden-data.json")):
        if p and os.path.exists(p):
            for c in json.load(open(p, encoding="utf-8")):
                if c["id"].lower() == cid.lower(): return c, p
            sys.exit(f"commessa '{cid}' assente in {p}")
    sys.exit("golden-data.json non trovato")

def rail_signs(step, cls):
    """Capi su rail = pin della classe non coperti da coords. Segno dal testo 'x -> +'.
    None = segno non dichiarato (vale qualunque rail)."""
    import re
    n = PINS.get(cls, 2) - len(step.get("coords", []))
    if n <= 0: return []
    signs = [("-" if s in "−-" else "+")
             for s in re.findall(r"(?:→|->)\s*([+−-])", step.get("holes", ""))]
    if not signs:
        # segno dal golden: forma "da − a −" del cavetto rail-rail
        m = re.search(r"\bda\s*([+−-])\s*a\s*([+−-])", step.get("holes", ""))
        if m: signs = [("-" if s in "−-" else "+") for s in m.groups()]
    return (signs + [None] * n)[:n]

def qc_main(cid, novlm):
    import os
    golden, gpath = load_golden(cid)
    build = golden["build"]
    nmax = max((int(f[:-5]) for f in os.listdir(".") if f.endswith(".jpeg") and f[:-5].isdigit()),
               default=0)
    nsteps = min(len(build), nmax)
    print(f"QC commessa {golden['id']} - {golden['name']}  (golden: {gpath})")
    # il golden è validato sul fisico
    if nmax != len(build):
        print(f"!! foto trovate={nmax}, step golden={len(build)}: controllo i primi {nsteps}\n")
    frame = make_frame()
    img0, _, pos0, rpos0, _ = frame(0)
    m0 = img0.copy()                       # mappa del run: qc_0_mappa.png
    for p in pos0.values(): cv2.circle(m0, p, 3, (0, 200, 0), -1)
    for uv_, p in rpos0.items():
        cv2.circle(m0, p, 3, (200, 80, 0) if uv_[0].startswith("-") else (0, 0, 220), -1)
    for r_, u_ in og.ROW_U.items():
        if (u_, 0) in pos0:
            cv2.putText(m0, r_, (pos0[(u_, 0)][0] - 30, pos0[(u_, 0)][1] + 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
    for uu in (0, 10):
        for c_ in [1] + list(range(5, og.NCOLS, 5)) + [og.NCOLS]:
            if (uu, c_ - 1) in pos0:
                x_, y_ = pos0[(uu, c_ - 1)]
                cv2.putText(m0, str(c_), (x_ - 8, y_ + (26 if uu == 0 else -16)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 80, 0), 1)
    cv2.imwrite("qc_0_mappa.png", m0)
    report = []
    satisfied = set()          # step golden già visti (anche fuori ordine)
    prior_zone = set()         # fori golden + hull degli step già processati
    for k in range(1, nsteps + 1):
        # la foto k si confronta con lo step k se ancora aperto, altrimenti col
        # primo step non ancora soddisfatto (un fuori-sequenza scala gli attesi)
        target = k if k not in satisfied else next((j for j in range(1, len(build) + 1)
                                                    if j not in satisfied), k)
        st = build[target - 1]
        cls = KIND2CLS.get(st["kind"], st["kind"])
        flex = "flessibile" in st.get("el", "").lower()
        exp = [parse_hole(h) for h in st.get("coords", [])]
        prev = frame(k - 1)[0]
        cur, _, pos, rpos, A = frame(k)
        prev = cv2.warpAffine(prev, A, (cur.shape[1], cur.shape[0]))
        posx = {**pos, **rpos}
        pitch = abs(pos[(0, 1)][0] - pos[(0, 0)][0]) or 29
        g0f = cv2.cvtColor(prev, cv2.COLOR_BGR2GRAY).astype(np.float32)
        g1f = cv2.cvtColor(cur, cv2.COLOR_BGR2GRAY).astype(np.float32)
        bl = find_blobs(cur, prev, board_mask(posx, cur.shape, grow=140))
        S = {uv: legscore(g0f, g1f, posx, *uv) for uv in exp if uv in posx}
        oks  = [uv for uv in exp if S.get(uv, 0) >= QC_THR]
        weak = [uv for uv in exp if QC_WEAK <= S.get(uv, 0) < QC_THR]
        miss = [uv for uv in exp if S.get(uv, 0) < QC_WEAK]
        note = []

        # Il blob del pezzo si sceglie una volta (il più vicino al baricentro dei fori
        # attesi) e si riusa per vicini/sagoma/identità/zone.
        bsel_pts = bsel_box = None
        if bl:
            expc0 = [posx[uv] for uv in exp if uv in posx]
            if expc0:
                ecx0 = sum(p[0] for p in expc0) / len(expc0)
                ecy0 = sum(p[1] for p in expc0) / len(expc0)
                bsel_pts, bsel_box = min(bl, key=lambda b: np.hypot(b[0][:, 0].mean() - ecx0,
                                                                    b[0][:, 1].mean() - ecy0))
            else:
                bsel_pts, bsel_box = bl[0]
        # capi geodetici subito (servono alla sagoma e alle zone: il nodo del capo è evidenza)
        sag_tips = None
        if bsel_pts is not None and len(bsel_pts):
            try:                                    # fasce dei solchi: lì la sagoma si ricuce
                ymed = lambda u: float(np.median([posx[(u, v)][1] for v in range(og.NCOLS)
                                                  if (u, v) in posx]))
                rt = float(np.median([p[1] for kk, p in rpos.items() if kk[0].endswith("t")]))
                rb = float(np.median([p[1] for kk, p in rpos.items() if kk[0].endswith("b")]))
                sag_bands = [(ymed(6), ymed(4)), (rt, ymed(10)), (ymed(0), rb)]
            except Exception:
                sag_bands = []
            sag_tips = mask_endpoints(bsel_pts, sag_bands)
        zone = zone_non_evidenza(g0f, g1f, posx, bsel_pts, pitch, prior_zone, tips=sag_tips)

        # Silhouette del blob + baricentro del corpo (parte spessa), usati dallo snap
        # dei capi con test di attraversamento, dal radar e dalle zone di classe.
        sil_b = sil_o = body_c2 = None
        if bsel_pts is not None and len(bsel_pts):
            sil_b, sx0b, sy0b = _sil_of(bsel_pts)
            sil_o = (sx0b, sy0b)
            er_b = cv2.erode(sil_b, np.ones((19, 19), np.uint8))
            ys_b, xs_b = np.nonzero(er_b)
            if len(xs_b):
                body_c2 = (float(xs_b.mean()) + sil_o[0], float(ys_b.mean()) + sil_o[1])
            else:
                body_c2 = (float(bsel_pts[:, 0].mean()), float(bsel_pts[:, 1].mean()))

        # Cupola del led dal colore atteso quando segmenta (riserva:
        # la zona spessa del diff, già in zone); i fori sotto la cupola non testimoniano.
        # Maschera EROSA (~9px) prima di etichettare: un foro solo lambito dal bordo
        # resta evidenza (il pin d'ingresso visibile non va inghiottito).
        if cls == "led" and bsel_pts is not None:
            exp_col_led = next((c for c in HUES if c in st.get("el", "").lower()), None)
            if exp_col_led:
                rm_ = np.zeros(cur.shape[:2], np.uint8)
                x0b_, y0b_, x1b_, y1b_ = bsel_box
                rm_[max(0, y0b_ - 40):y1b_ + 40, max(0, x0b_ - 40):x1b_ + 40] = 255
                cb_ = color_blob(cur, exp_col_led, rm_)
                if cb_ is not None:
                    cm_ = np.zeros(cur.shape[:2], np.uint8)
                    cm_[cb_[0][:, 1].astype(int), cb_[0][:, 0].astype(int)] = 255
                    cm_ = cv2.erode(cv2.morphologyEx(cm_, cv2.MORPH_CLOSE,
                                                     np.ones((5, 5), np.uint8)),
                                    np.ones((9, 9), np.uint8))
                    for uv_, p_ in posx.items():
                        if cm_[p_[1], p_[0]]: zone.setdefault(uv_, "cupola")

        # Testa della fotoresistenza = cerchietto — dischetto
        # elevato che l'erosione larga della zona spessa può mancare: fit circolare
        # sulla parte densa del blob, fori sotto il disco = non-evidenza.
        if cls == "fotoresistenza" and sil_b is not None:
            er9_ = cv2.erode(sil_b, np.ones((9, 9), np.uint8))
            n9_, l9_, st9_, _ = cv2.connectedComponentsWithStats(er9_)
            if n9_ > 1:
                i9_ = 1 + int(np.argmax(st9_[1:, cv2.CC_STAT_AREA]))
                ys9, xs9 = np.where(l9_ == i9_)
                (hcx, hcy), hr = cv2.minEnclosingCircle(
                    np.column_stack([xs9, ys9]).astype(np.float32))
                fill_ = st9_[i9_, cv2.CC_STAT_AREA] / max(1.0, np.pi * hr * hr)
                if fill_ >= 0.5 and 0.5 * pitch <= hr <= 2.0 * pitch:
                    for uv_, p_ in posx.items():
                        if np.hypot(p_[0] - (hcx + sil_o[0]),
                                    p_[1] - (hcy + sil_o[1])) <= hr + 0.35 * pitch:
                            zone.setdefault(uv_, "zona-testa")

        # Classi da corpo — trimmer/buzzer (HIDDEN) + cap verticale (capi
        # su righe diverse; il coricato resta famiglia 2-pin). Il detector di classe
        # aggancia il corpo nella zona del BLOB e predice la base dei pin con
        # l'offset per-commessa; il verdetto dedicato sta più sotto.
        capv7 = (cls == "condensatore" and exp and len({u for u, _ in exp}) > 1)
        corpo7 = None
        if (cls in HIDDEN or capv7) and bsel_box is not None:
            corpo7 = corpo_base(cls, golden["id"], cur, bsel_box, pitch)

        # Capo griglia del cavetto flessibile — la
        # guaina v2 (des-saturazione + forma lungo l'asse, mai la sola V) si cerca al
        # tip della sagoma più vicino agli attesi; se isolata, la DEDUZIONE DI BORDO
        # dice il pin (ultimo foro cambiato prima del confine muto). Il verdetto la
        # usa nel ramo FILI; qui si misura soltanto.
        guaina_b6 = None          # (bordo_capo result | None, tip) — None = non isolata
        if cls in FREE2 and flex and exp and bsel_pts is not None and sag_tips:
            ge6 = [uv for uv in exp if uv in pos]
            tp6 = (min(sag_tips, key=lambda t: np.hypot(t[0] - np.mean([pos[uv][0] for uv in ge6]),
                                                        t[1] - np.mean([pos[uv][1] for uv in ge6])))
                   if ge6 else None)
            if tp6 is not None:
                tgt6 = np.mean([pos[uv] for uv in ge6], axis=0)
                if np.hypot(tp6[0] - tgt6[0], tp6[1] - tgt6[1]) > 2.5 * pitch:
                    tp6 = None      # sagoma sporca: il tip non arriva all'atteso
                                    # (misurato: tip a ~10 passi dal golden) ->
                                    # niente deduzione da un tip inattendibile, resta rosa
            if tp6 is not None:
                near6 = bsel_pts[np.hypot(bsel_pts[:, 0] - tp6[0],
                                          bsel_pts[:, 1] - tp6[1]) < 2.5 * pitch]
                ax6 = None
                if len(near6) > 5:
                    v6 = np.array([tp6[0] - near6[:, 0].mean(), tp6[1] - near6[:, 1].mean()])
                    n6 = float(np.linalg.norm(v6))
                    if n6 > 1: ax6 = v6 / n6
                if ax6 is not None and guaina_v2(cur, tp6, ax6, pitch) is not None:
                    guaina_b6 = (bordo_capo(lambda uv: legscore(g0f, g1f, posx, *uv),
                                            posx, tp6, ax6, pitch, cls_thr(cls)[1]), tp6)

        # Bottone = 2 coppie di colonna (e,c)-(f,c) + quadrupla di conferma su tutte le
        # colonne della regione (fit libero, corpo = colonna dell'ipotesi).
        # Combinatore: media geometrica; soglie di classe sul punteggio di coppia; il
        # singolo foro sotto WEAK resta muto e mai promosso (regola dura).
        btn = None
        corpo_ip = set()      # fori corpo per ipotesi golden (colonna
                              # centrale del bottone, riga di mezzo dell'opto): il
                              # pezzo li copre per costruzione, il test del vicino non li usa
        if cls == "bottone" and bsel_pts is not None and len(exp) == 4:
            cols_b = sorted({v for _, v in exp})
            if len(cols_b) == 2:
                thrB, weakB = cls_thr(cls)
                pairs = {c: pair_score([S.get((u, c), 0.0) for u in (CE, CF)])
                         for c in cols_b}
                mute_b = [uv for uv in exp if S.get(uv, 0) < weakB]
                center_g = (cols_b[0] + cols_b[1]) // 2
                Q = {}
                for c in sorted({v for (u, v) in region_holes(posx, bsel_box, pad=80)
                                 if not isinstance(u, str)}):
                    quad = [(CE, c - 1), (CE, c + 1), (CF, c - 1), (CF, c + 1)]
                    if any(q not in posx for q in quad): continue
                    # stesso combinatore di fit_pins e della verifica ancorata: il
                    # punteggio è il piedino più debole dei quattro
                    Q[c] = min(legscore(g0f, g1f, posx, *q) for q in quad)
                btn = (pairs, mute_b, center_g, Q, thrB, weakB)
                corpo_ip = {(u, center_g) for u in (CE, CF)}

        # Opto = 3 coppie di colonna (r1,c)-(r2,c) con righe dal golden (mai
        # hardcoded e/f: alcune commesse usano e/g) + doppia terna di conferma:
        # per ogni colonna di partenza della regione, terna geometrica per riga e
        # min tra le due righe — la matematica del bottone ruotata.
        opt = None
        if cls == "optoaccoppiatore" and bsel_pts is not None and len(exp) == 6:
            rows_o = sorted({u for u, _ in exp}, key=ru)
            cols_o = sorted({v for _, v in exp})
            if len(rows_o) == 2 and len(cols_o) == 3 and cols_o[2] - cols_o[0] == 2:
                thrO, weakO = cls_thr(cls)
                pairsO = {c: pair_score([S.get((u, c), 0.0) for u in rows_o])
                          for c in cols_o}
                mute_o = [uv for uv in exp if S.get(uv, 0) < weakO]
                start_g = cols_o[0]
                T2 = {}
                for c in sorted({v for (u, v) in region_holes(posx, bsel_box, pad=80)
                                 if not isinstance(u, str)}):
                    if not all((u, c + i) in posx for u in rows_o for i in range(3)):
                        continue
                    # piedino più debole dei sei (il min per riga di una terna
                    # geometrica mascherava un foro vuoto)
                    T2[c] = min(legscore(g0f, g1f, posx, u, c + i)
                                for u in rows_o for i in range(3))
                opt = (pairsO, mute_o, start_g, T2, thrO, weakO, rows_o)
                mid_rows = [u for u in {u2 for u2, _ in posx if not isinstance(u2, str)}
                            if ru(rows_o[0]) < ru(u) < ru(rows_o[1])]
                corpo_ip = {(u, c) for u in mid_rows
                            for c in range(cols_o[0] - 1, cols_o[2] + 2)}

        # Famiglia 2-pin (resistenza, led, diodo, cap coricato, fotoresistenza)
        # — punteggio DI CLASSE = media geometrica dei capi in griglia, giudicato con le
        # soglie QC_CLS; il singolo foro sotto WEAK resta muto e MAI promosso (regola
        # dura). Foro atteso sotto cupola/testa/zona spessa del pezzo CORRENTE =
        # invisibile per costruzione (regola LED): mai FAIL né PASS da quel foro.
        # Il cap verticale (capi su righe diverse) resta alle classi da corpo.
        fam2 = None
        if cls in ("resistenza", "led", "diodo", "condensatore", "fotoresistenza") \
           and exp and bl and (cls != "condensatore" or len({u for u, _ in exp}) == 1):
            thr2, weak2 = cls_thr(cls)
            # occlusione SOLO da zona misurata di classe (cupola led, testa LDR): la
            # zona-spessa generica può essere ombra densa e non scusa mai un atteso
            occl2 = [uv for uv in exp if zone.get(uv) in ("cupola", "zona-testa")]
            capi2 = [uv for uv in exp if uv in posx and uv not in occl2]
            P2 = pair_score([S.get(uv, 0.0) for uv in capi2]) if capi2 else 0.0
            fam2 = (thr2, weak2, occl2, capi2, P2)
            oks  = [uv for uv in exp if S.get(uv, 0) >= thr2]
            weak = [uv for uv in exp if weak2 <= S.get(uv, 0) < thr2]
            miss = [uv for uv in exp if uv not in occl2 and S.get(uv, 0) < weak2]

        def near_hot(uv):
            """Off-by-one: se il foro atteso è muto ma un vicino è caldo, dillo.
            Vicini di colonna e di riga. Un vicino
            in zona non-evidenza TESTIMONIA comunque che il pezzo è lì vicino (e
            blocca il fuori-sequenza), ma la diagnosi '+-1' la fa solo un vicino
            pulito: il segnale in zona è corpo/filo/ombra, non una gamba certa
            (misurato: foro caldo per un filo sopra, gamba dichiarata nel vicino)."""
            u, v = uv
            cands = [(u, v + 1), (u, v - 1), (u + 1, v), (u - 1, v)]
            hot = [(legscore(g0f, g1f, posx, *c), c) for c in cands if c in posx]
            hot = [t for t in hot if t[0] >= QC_THR]
            return max(hot)[1] if hot else None

        # Un foro atteso non si certifica se un primo vicino segna molto di più: la
        # gamba probabilmente è là, il foro atteso è solo scavalcato dal filo (misurato:
        # atteso a 0.31 "occupato" con la gamba nel vicino). I 4 primi vicini bastano:
        # una gamba più lontana accende comunque il vicino intermedio. I fori sotto il
        # CORPO (parte spessa del blob) non fanno testo: occlusi per natura (cupola led).
        ambig = []
        if bsel_pts is not None and exp and cls not in HIDDEN:
            # pin invisibili: il test del vicino è solo rumore (il corpo scalda i fori
            # intorno). Il filtro è l'intera zona non-evidenza, non solo la
            # zona spessa del blob corrente.
            for uv in exp:
                s0 = S.get(uv, 0)
                if s0 < QC_WEAK: continue           # muto: ci pensa già near_hot
                # per bottone e opto il vicino caldo si valuta contro
                # la COPPIA di colonna, non contro il singolo foro
                if btn is not None: s0 = max(s0, btn[0].get(uv[1], 0.0))
                if opt is not None: s0 = max(s0, opt[0].get(uv[1], 0.0))
                u_, v_ = uv
                for nb in ((u_ + 1, v_), (u_ - 1, v_), (u_, v_ + 1), (u_, v_ - 1)):
                    if nb in exp or nb not in posx or nb in zone or nb in corpo_ip:
                        continue
                    sn = legscore(g0f, g1f, posx, *nb)
                    if sn >= QC_THR and sn >= 1.5 * s0:
                        ambig.append((uv, nb, s0, sn)); break

        # La sagoma: la forma intera del pezzo dal diff, calcolata
        # sui PIXEL (indipendente dai fori). Gli estremi geodetici sono dove le gambe si
        # tuffano nella board: distinguono "attraversato" (interno al percorso) da
        # "inserito" (terminale), l'unica cosa che il legscore non sa fare. Fa da
        # SPAREGGIO dichiarato quando il test del vicino contesta un foro atteso.
        sag_ends, sag_pts = [], None
        if bsel_pts is not None and PINS.get(cls, 0) == 2 and cls not in HIDDEN:
            sag_pts = bsel_pts                    # stesso blob e tips di sopra
            tips = sag_tips
            if tips:
                sx0, sy0 = int(sag_pts[:, 0].min()), int(sag_pts[:, 1].min())
                sx1, sy1 = int(sag_pts[:, 0].max()), int(sag_pts[:, 1].max())
                allh = region_holes(posx, (sx0, sy0, sx1, sy1), pad=60)
                allh += [uv for uv in rpos if uv not in allh
                         and sx0 - 40 <= rpos[uv][0] <= sx1 + 40]
                for tp in tips:
                    # quartiere ~1.5 passi + test di attraversamento (il foro col filo
                    # che prosegue oltre non è mai il capo); zone escluse
                    h5 = snap_capo(posx, allh, tp, pitch,
                                   lambda uv: legscore(g0f, g1f, posx, *uv),
                                   avoid=zone, sil=sil_b, so=sil_o, body_c=body_c2,
                                   boost=0.03)
                    if h5 is not None and h5 not in sag_ends: sag_ends.append(h5)
        if sag_ends:
            note.append("SAGOMA: termina su " + " e ".join(
                (name(*uv) if not isinstance(uv[0], str) else f"{uv[0]}{uv[1]}")
                for uv in sag_ends))
            keep_amb = []
            for uv, nb, s0, sn in ambig:
                if uv in sag_ends:
                    note.append(f"vicino {name(*nb)} caldo ma la sagoma termina su"
                                f" {name(*uv)}: ambiguita' risolta a favore dell'atteso")
                else:
                    keep_amb.append((uv, nb, s0, sn))
            ambig = keep_amb

        def rail_check(sign):
            """Miglior legscore sulle rail nella finestra x del componente, per segno.
            Ritorna (ok, best_score_per_segno, foro_migliore_del_segno_richiesto)."""
            xs = [posx[uv][0] for uv in exp if uv in posx]
            if not xs and bl: xs = [(bl[0][1][0] + bl[0][1][2]) / 2]
            x0 = (min(xs) - 6 * pitch) if xs else 0
            x1 = (max(xs) + 6 * pitch) if xs else 1e9
            best = {"+": (0.0, None), "-": (0.0, None)}
            for uv, p in rpos.items():
                # Mai confermare su fori di pezzi già montati; la zona spessa del pezzo
                # corrente invece resta (gamba sotto cupola/corpo: per l'NCC indistinguibile
                # dalla plastica -> decide la regola di classe, qui si dichiara soltanto)
                if zone.get(uv) == "pezzo-noto": continue
                if x0 <= p[0] <= x1:
                    s = legscore(g0f, g1f, posx, *uv)
                    if s > best[uv[0][0]][0]: best[uv[0][0]] = (s, uv)
            sc = {k: v[0] for k, v in best.items()}
            if sign is None:
                top = max(best.values())
                return top[0] >= QC_THR, sc, top[1]
            return sc[sign] >= QC_THR, sc, best[sign][1]

        def rail_capo_filo(sign, used):
            """Capo-rail del filo = nodo unico, la colonna non conta. Finestra x alla
            posizione del capo (tip della sagoma verso il blocco rail, mai tutta la
            campata del filo), segno dal golden, attraversamenti scontati (filo che
            prosegue oltre il foro = stazione dell'arco, mai capo), zone pezzo-noto
            escluse (una cupola sulla rail non è un capo). Se la sagoma non offre un
            tip verso le rail: finestra larga, dichiarata. Ritorna (ok, sc, hit, nota)."""
            try:
                rt6 = float(np.median([p[1] for kk, p in rpos.items() if kk[0].endswith("t")]))
                rb6 = float(np.median([p[1] for kk, p in rpos.items() if kk[0].endswith("b")]))
            except Exception:
                ok, best, hit = rail_check(sign)
                return ok, best, hit, None
            tps = [t for t in (sag_tips or []) if tuple(t) not in used
                   and min(abs(t[1] - rt6), abs(t[1] - rb6)) <= 3.5 * pitch]
            if not tps:
                ok, best, hit = rail_check(sign)
                return ok, best, hit, "capo non localizzato dalla sagoma: finestra larga dichiarata"
            tp = min(tps, key=lambda t: min(abs(t[1] - rt6), abs(t[1] - rb6)))
            used.add(tuple(tp))
            side = "t" if abs(tp[1] - rt6) <= abs(tp[1] - rb6) else "b"
            def stazione(p):
                """Stazione dell'arco lungo la rail: sagoma
                presente su ENTRAMBI i lati orizzontali del foro — il test radiale
                (attraversato) non la vede perché lì il filo corre parallelo."""
                if sil_b is None: return False
                lati = 0
                for sx6 in (-1, 1):
                    for t6 in (0.7, 1.0, 1.3):
                        x6 = int(p[0] + sx6 * t6 * pitch) - sil_o[0]
                        y6 = int(p[1]) - sil_o[1]
                        if 0 <= y6 < sil_b.shape[0] and 0 <= x6 < sil_b.shape[1] \
                           and sil_b[max(0, y6 - 3):y6 + 4, max(0, x6 - 3):x6 + 4].any():
                            lati += 1
                            break
                return lati == 2
            best = {"+": (0.0, None), "-": (0.0, None)}
            besta = {"+": (0.0, None), "-": (0.0, None)}   # attraversati/stazioni (riserva)
            for uv, p in rpos.items():
                if not uv[0].endswith(side): continue
                if zone.get(uv) == "pezzo-noto": continue
                if abs(p[0] - tp[0]) > 2.5 * pitch: continue
                s = legscore(g0f, g1f, posx, *uv)
                d = (besta if attraversato(sil_b, sil_o, p, body_c2, pitch)
                     or stazione(p) else best)
                if s > d[uv[0][0]][0]: d[uv[0][0]] = (s, uv)
            need = max(v[0] for v in best.values()) if sign is None else best[sign][0]
            if need < QC_THR:
                # niente capo pulito nella finestra del tip: prima la finestra larga
                # (il capo vero può stare lungo la guaina fuori finestra, o il tip può
                # essere un ramo d'ombra: entrambi misurati), poi — ultima riserva — il
                # migliore attraversato, dichiarato.
                okw, scw, hitw = rail_check(sign)
                if okw:
                    return okw, scw, hitw, ("capo fuori dalla finestra del tip:"
                                            " finestra larga dichiarata")
                ricambio = (max(v[0] for v in besta.values()) if sign is None
                            else besta[sign][0])
                if ricambio >= QC_THR:
                    sc = {k: besta[k][0] for k in besta}
                    if sign is None:
                        top = max(besta.values())
                        return True, sc, top[1], ("il filo prosegue oltre il foro:"
                                                  " capo lungo la guaina/arco, dichiarato")
                    return True, sc, besta[sign][1], ("il filo prosegue oltre il foro:"
                                                      " capo lungo la guaina/arco, dichiarato")
            sc = {k: best[k][0] for k in best}
            if sign is None:
                top = max(best.values())
                return top[0] >= QC_THR, sc, top[1], None
            return sc[sign] >= QC_THR, sc, best[sign][1], None

        rails_bad, rail_hits = False, []          # le rail verificate si vedono
        used_tips6 = set()
        for sign in rail_signs(st, cls):
            if cls in FREE2 and bsel_pts is not None:
                ok, best, hit, xn6 = rail_capo_filo(sign, used_tips6)
                if xn6: note.append(xn6)
            else:
                ok, best, hit = rail_check(sign)
            if ok:
                rail_hits.append((hit, True))
                note.append(f"rail {sign or '?'}: {best[hit[0][0]]:.2f} su {hit[0]}{hit[1]} ok"
                            + (f" (segnale nella {zone.get(hit).upper()} del pezzo: gamba o"
                               " plastica, non distinguibile)"
                               if zone.get(hit) in ("zona-spessa", "cupola", "zona-testa")
                               else ""))
                if sign and best["+" if sign == "-" else "-"] > 1.5 * best[sign]:
                    note.append(f"rail {sign} ok ma il segno opposto e' piu' caldo: VERIFICA etichette")
            else:
                opp = "+" if sign == "-" else "-"
                if hit: rail_hits.append((hit, False))
                if sign and best[opp] >= QC_THR:
                    note.append(f"rail attesa {sign} MUTA, ma {opp} caldo ({best[opp]:.2f}):"
                                " capo su rail sbagliata O etichetta +/- invertita")
                else:
                    note.append(f"nessun segnale sulle rail (atteso {sign or 'qualunque'})")
                rails_bad = True

        # Verifica identità del pezzo (classe VLM, colore OpenCV, bande resistenza).
        # Classe/colore sbagliati = FAIL; bande discordanti = declassa (lettura non validata).
        import re as _re
        id_fail, id_soft = [], []
        letto, col_letto = None, None
        if novlm:
            note.append("identita' NON verificata (--novlm)")
        elif bl:
            # crop ancorato alla zona attesa dal golden: con un urto il
            # blob più grosso può essere il pezzo VECCHIO mosso, non il nuovo -> si
            # sceglie il blob più vicino al baricentro dei fori attesi e si ritaglia
            # attorno a zona attesa + blob.
            ipts, ibox = bsel_pts, bsel_box       # stesso blob scelto sopra
            expc = [posx[uv] for uv in exp if uv in posx]
            if expc:
                x0c = min(ibox[0], min(p[0] for p in expc)); x1c = max(ibox[2], max(p[0] for p in expc))
                y0c = min(ibox[1], min(p[1] for p in expc)); y1c = max(ibox[3], max(p[1] for p in expc))
            else:
                x0c, y0c, x1c, y1c = ibox
            crop = cur[max(0, y0c - 90):y1c + 90, max(0, x0c - 90):x1c + 90]
            letto = vlm_classify(crop)
            okset = {cls} | ({"jumper", "cavetto"} if cls in FREE2 else set())
            if letto is None:
                note.append("classe: VLM non risponde (quota?)")
            elif letto in okset:
                note.append(f"classe letta: {letto} = attesa")
            else:
                id_fail.append(f"classe letta '{letto}' != attesa '{cls}'")
            col_letto = blob_color(cur, ipts)
            if st["kind"] in ("led", "jumper"):        # colore atteso dal testo del golden
                exp_col = next((c for c in HUES if c in st.get("el", "").lower()), None)
                if exp_col:
                    col = col_letto
                    # hue adiacenti indistinguibili E senza ambiguità nel kit (inventario:
                    # niente pezzi arancioni; blu e azzurro non convivono)
                    same = col == exp_col or {col, exp_col} in ({"giallo", "arancione"},
                                                                {"blu", "azzurro"})
                    if not col: note.append(f"colore atteso {exp_col}: non determinabile dal blob")
                    elif same: note.append(f"colore letto: {col} = atteso ({exp_col})")
                    elif st["kind"] == "led":
                        # il colore del led conferma,
                        # mai boccia da solo (il giallo accende anche la maschera rossa)
                        id_soft.append(f"colore letto '{col}' != atteso '{exp_col}'"
                                       " (colore led mai bocciante da solo)")
                    else: id_fail.append(f"colore letto '{col}' != atteso '{exp_col}'")
            m = _re.match(r"(R\d+[kM]?\d*)", st.get("el", ""))
            if m and cls == "resistenza" and m.group(1) in BANDS:
                attese = BANDS[m.group(1)]
                zoom = cv2.resize(crop, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
                got = vlm_bands(zoom)
                if got is None:
                    note.append(f"bande {m.group(1)} ({'-'.join(attese)}): illeggibili")
                elif got == attese or got == attese[::-1]:
                    note.append(f"bande lette {'-'.join(got)} = {m.group(1)}")
                else:
                    id_soft.append(f"bande lette {'-'.join(got)}, attese {'-'.join(attese)}"
                                   f" ({m.group(1)}): VALORE DA VERIFICARE")

        if cls in HIDDEN or capv7:
            # Verdetto da corpo (trimmer, buzzer, cap verticale): il
            # legscore sotto il corpo non dimostra nulla (cap verticale: coperti
            # QUASI MUTI, p50 0.11 -> il cerchio-top è la via unica). Verdetto
            # massimo = STIMATO dichiarato; base predetta fuori zona = FAIL coi
            # numeri ("dove non è" pieno).
            expc7 = [posx[uv] for uv in exp if uv in posx]
            if corpo7 is not None and expc7:
                met7, base7, rip7 = corpo7[:3]
                gcx7 = sum(p[0] for p in expc7) / len(expc7)
                gcy7 = sum(p[1] for p in expc7) / len(expc7)
                res7 = float(np.hypot(base7[0] - gcx7, base7[1] - gcy7)) / pitch
                if rip7:
                    note.append("offset corpo->pin NON calibrato per questa commessa:"
                                f" k radiale {K_RADIALE} (ripiego dichiarato)")
                if res7 <= CORPO_TOL:
                    verdict = "STIMATO"
                    note.append(f"corpo agganciato ({met7}): base pin a {res7:.2f}"
                                " passi dall'atteso"
                                + (" — possibile +-1, verifica operatore"
                                   if res7 >= 0.7 else "")
                                + "; fori sotto il corpo: verifica operatore")
                else:
                    verdict = "FAIL"
                    # footprint reale dedotto: fori più vicini ai pin predetti
                    # (base + geometria golden), dichiarato coi numeri
                    fp7 = []
                    for uv in exp:
                        if uv not in posx: continue
                        px7 = (base7[0] + posx[uv][0] - gcx7,
                               base7[1] + posx[uv][1] - gcy7)
                        nh7 = min((h for h in posx if not isinstance(h[0], str)),
                                  key=lambda h: np.hypot(posx[h][0] - px7[0],
                                                         posx[h][1] - px7[1]))
                        if nh7 not in fp7: fp7.append(nh7)
                    note.append(f"corpo agganciato ({met7}) ma base pin a {res7:.2f}"
                                f" passi dall'atteso (> {CORPO_TOL}): pezzo FUORI"
                                " POSTO — footprint piu' vicino ai pin predetti: "
                                + " ".join(name(*h) for h in fp7))
            else:
                # detector di classe NON agganciato: dichiarato; resta la verifica
                # di zona del blob (comportamento storico, ripiego)
                zx = [p[0] for p in expc7]
                zy = [p[1] for p in expc7]
                m = 2.5 * pitch
                body_ok = any(zx and min(zx) - m <= pts[:, 0].mean() <= max(zx) + m
                              and min(zy) - m <= pts[:, 1].mean() <= max(zy) + m
                              for pts, _ in bl)
                if bl:
                    note.append("corpo di classe NON agganciato"
                                " (maschera/cerchio): ripiego sulla zona del blob")
                if body_ok:
                    verdict = "STIMATO"
                    note.append("corpo in posizione; fori sotto il corpo: verifica operatore")
                else:
                    verdict = "FAIL"
                    note.append("corpo NON nella zona attesa")
        elif not bl:
            verdict = "FAIL"; note.append("nessun cambiamento rilevato nel diff")
        elif btn is not None:
            # verdetto bottone dalle coppie + quadrupla (mai dal singolo foro)
            pairsB, mute_b, center_g, Q, thrB, weakB = btn
            qg = Q.get(center_g, 0.0)
            qbest = max(Q, key=Q.get) if Q else None
            if qbest is not None and qbest != center_g and Q[qbest] >= max(thrB, 1.5 * qg):
                verdict = "FAIL"
                note.append(f"QUADRUPLA: centro migliore col {qbest+1} (Q={Q[qbest]:.2f})"
                            f" vs golden col {center_g+1} (Q={qg:.2f}): bottone fuori"
                            f" posto di {qbest-center_g:+d} colonne (pin {qbest}·{qbest+2})")
            elif mute_b:
                verdict = "FAIL"
                for uv in mute_b:
                    nb = near_hot(uv)
                    lab = zone.get(nb) if nb else None
                    note.append(f"{name(*uv)} muto ({S.get(uv, 0):.2f})"
                                + ((f", vicino {name(*nb)} caldo"
                                    + (f" ma {lab} (corpo/filo sopra, non gamba certa)"
                                       if lab else ": probabile +-1")) if nb else ""))
            elif any(p < thrB for p in pairsB.values()):
                verdict = "STIMATO"
                for c, p in pairsB.items():
                    if p < thrB: note.append(f"coppia col {c+1} debole "
                                             f"({p:.2f} < {thrB}, fori "
                                             + " ".join(f"{name(u, c)}={S.get((u, c), 0):.2f}"
                                                        for u in (CE, CF)) + ")")
            else:
                verdict = "PASS"
            note.append("coppie col " + " · ".join(f"{c+1}:{p:.2f}" for c, p in pairsB.items())
                        + (f" | quadrupla: centro golden col {center_g+1} confermato"
                           f" (Q={qg:.2f}"
                           + (f", margine {qg/max(Q[c] for c in Q if c != center_g):.1f}x"
                              if len(Q) > 1 and max(Q[c] for c in Q if c != center_g) > 0 else "")
                           + ")" if qbest == center_g else ""))
            if ambig and any(sn >= 2 * s0 for _, _, s0, sn in ambig):
                verdict = "FAIL"
            elif ambig and verdict == "PASS":
                verdict = "STIMATO"
        elif opt is not None:
            # verdetto opto dalle 3 coppie + doppia terna (mai dal singolo foro)
            pairsO, mute_o, start_g, T2, thrO, weakO, rows_o = opt
            tg = T2.get(start_g, 0.0)
            tbest = max(T2, key=T2.get) if T2 else None
            if tbest is not None and tbest != start_g and T2[tbest] >= max(thrO, 1.5 * tg):
                verdict = "FAIL"
                note.append(f"DOPPIA TERNA: partenza migliore col {tbest+1}"
                            f" (T={T2[tbest]:.2f}) vs golden col {start_g+1} (T={tg:.2f}):"
                            f" opto fuori posto di {tbest-start_g:+d} colonne"
                            f" (colonne {tbest+1}-{tbest+3})")
            elif mute_o:
                verdict = "FAIL"
                for uv in mute_o:
                    nb = near_hot(uv)
                    lab = zone.get(nb) if nb else None
                    note.append(f"{name(*uv)} muto ({S.get(uv, 0):.2f})"
                                + ((f", vicino {name(*nb)} caldo"
                                    + (f" ma {lab} (corpo/filo sopra, non gamba certa)"
                                       if lab else ": probabile +-1")) if nb else ""))
            elif any(p < thrO for p in pairsO.values()):
                verdict = "STIMATO"
                for c, p in pairsO.items():
                    if p < thrO: note.append(f"coppia col {c+1} debole ({p:.2f} < {thrO}, fori "
                                             + " ".join(f"{name(u, c)}={S.get((u, c), 0):.2f}"
                                                        for u in rows_o) + ")")
            else:
                verdict = "PASS"
            note.append("coppie col " + " · ".join(f"{c+1}:{p:.2f}" for c, p in pairsO.items())
                        + (f" | doppia terna: partenza golden col {start_g+1} confermata"
                           f" (T={tg:.2f})" if tbest == start_g else ""))
            if ambig and any(sn >= 2 * s0 for _, _, s0, sn in ambig):
                verdict = "FAIL"
            elif ambig and verdict == "PASS":
                verdict = "STIMATO"
        elif fam2 is not None:
            # Verdetto famiglia 2-pin dalla coppia dei capi (soglie di classe);
            # il muto resta FAIL mai promosso; l'atteso sotto cupola/testa/zona spessa
            # del pezzo corrente è invisibile per costruzione -> STIMATO dichiarato;
            # LDR: mai FAIL dalla sola rail muta (misurato: gamba corta nel bus, 0.02-0.06).
            thr2, weak2, occl2, capi2, P2 = fam2
            ldr_rail_only = cls == "fotoresistenza" and rails_bad and not miss
            if miss:
                verdict = "FAIL"
                for uv in miss:
                    nb = near_hot(uv)
                    lab = zone.get(nb) if nb else None
                    note.append(f"{name(*uv)} muto ({S.get(uv, 0):.2f})"
                                + ((f", vicino {name(*nb)} caldo"
                                    + (f" ma {lab} (corpo/filo sopra, non gamba certa)"
                                       if lab else ": probabile +-1")) if nb else ""))
            elif rails_bad and not ldr_rail_only:
                verdict = "FAIL"
            else:
                verdict = "PASS"
                if occl2:
                    verdict = "STIMATO"
                    for uv in occl2:
                        note.append(f"{name(*uv)} atteso sotto la {zone.get(uv)} del pezzo"
                                    f" (S={S.get(uv, 0):.2f}): invisibile per costruzione,"
                                    " mai FAIL ne' PASS da questo foro — verifica operatore")
                if ldr_rail_only:
                    verdict = "STIMATO"
                    note.append("rail attesa muta ma classe LDR (gamba corta dritta nel"
                                " bus): la conferma rail spesso non si misura — mai FAIL"
                                " dalla sola rail muta, STIMATO dichiarato")
                if capi2 and P2 < thr2:
                    verdict = "STIMATO"
                    note.append(f"coppia capi debole ({P2:.2f} < {thr2}, "
                                + " ".join(f"{name(*uv)}={S.get(uv, 0):.2f}"
                                           for uv in capi2) + ")")
            if capi2:
                note.append("capi " + " ".join(f"{name(*uv)}={S.get(uv, 0):.2f}"
                                               for uv in capi2)
                            + f" · coppia {P2:.2f} (soglia {cls} {thr2})")
            if ambig and any(sn >= 2 * s0 for _, _, s0, sn in ambig):
                verdict = "FAIL"
            elif ambig and verdict == "PASS":
                verdict = "STIMATO"
        elif cls in FREE2:
            # Verdetto fili (jumper rigido / cavetto flessibile): capi
            # griglia per-foro (QC_CLS filo = soglie default), capo-rail CONFERMABILE
            # (nodo unico, già verificato sopra con finestra al capo e attraversamenti
            # scontati); per il flessibile decide la guaina con la deduzione di bordo:
            # conferma il capo, o dichiara la rosa +-1.
            if miss:
                verdict = "FAIL"
                for uv in miss:
                    nb = near_hot(uv)
                    lab = zone.get(nb) if nb else None
                    note.append(f"{name(*uv)} muto ({S.get(uv, 0):.2f})"
                                + ((f", vicino {name(*nb)} caldo"
                                    + (f" ma {lab} (corpo/filo sopra, non gamba certa)"
                                       if lab else ": probabile +-1")) if nb else ""))
            elif rails_bad:
                verdict = "FAIL"
            else:
                verdict = "PASS"
                if weak:
                    verdict = "STIMATO"
                    for uv in weak: note.append(f"{name(*uv)} debole ({S[uv]:.2f})")
                if flex and exp:
                    if guaina_b6 is not None and guaina_b6[0] is not None:
                        pin6, netto6, oltre6 = guaina_b6[0]
                        nm6 = (name(*pin6) if not isinstance(pin6[0], str)
                               else f"{pin6[0]}{pin6[1]}")
                        if pin6 in exp and netto6:
                            note.append(f"capo sotto guaina CONFERMATO dalla deduzione"
                                        f" di bordo: {nm6} = atteso (oltre il bordo"
                                        f" S={oltre6:.2f} muto)")
                        elif pin6 in exp:
                            if verdict == "PASS": verdict = "STIMATO"
                            note.append(f"bordo sfumato oltre {nm6} (S={oltre6:.2f}):"
                                        " rosa +-1 lungo l'asse della guaina, dichiarata")
                        else:
                            if verdict == "PASS": verdict = "STIMATO"
                            note.append(f"deduzione di bordo: ultimo foro cambiato {nm6},"
                                        f" atteso {' '.join(name(*uv) for uv in exp)}:"
                                        " rosa dichiarata (guaina piu' larga del foro)")
                    else:
                        if verdict == "PASS": verdict = "STIMATO"
                        note.append("guaina non isolata: capo sotto guaina, +-1 possibile")
            if ambig and any(sn >= 2 * s0 for _, _, s0, sn in ambig):
                verdict = "FAIL"
            elif ambig and verdict == "PASS":
                verdict = "STIMATO"
        elif miss or rails_bad:
            verdict = "FAIL"
            for uv in miss:
                nb = near_hot(uv)
                lab = zone.get(nb) if nb else None
                note.append(f"{name(*uv)} muto ({S.get(uv, 0):.2f})"
                            + ((f", vicino {name(*nb)} caldo"
                                + (f" ma {lab} (corpo/filo sopra, non gamba certa)"
                                   if lab else ": probabile +-1")) if nb else ""))
        elif any(sn >= 2 * s0 for _, _, s0, sn in ambig):
            verdict = "FAIL"
        elif ambig or weak or flex:
            verdict = "STIMATO"
            if flex: note.append("cavetto flessibile: capo sotto guaina, +-1 possibile")
            for uv in weak: note.append(f"{name(*uv)} debole ({S[uv]:.2f})")
        else:
            verdict = "PASS"

        if opt is not None and bl:
            # Verso dell'opto dalla serigrafia (verso_opto: baricentro dell'inchiostro).
            expc_o = [posx[uv] for uv in exp if uv in posx]
            if expc_o:
                cxo = sum(p[0] for p in expc_o) / len(expc_o)
                cyo = sum(p[1] for p in expc_o) / len(expc_o)
                verso_o, cos_o = verso_opto(cv2.cvtColor(cur, cv2.COLOR_BGR2GRAY),
                                            (cxo, cyo), pitch)
                if verso_o == "dritto":
                    note.append(f"verso opto: montato dritto (cos {cos_o:+.2f})")
                elif verso_o == "capovolto":
                    # KO POLARITA (allineato a radar_operativa): verdetto dedicato, non
                    # FAIL. Il FAIL fa partire la ricerca fuori-sequenza, che su un pezzo
                    # giusto nei fori giusti, solo girato, andrebbe a cercargli un altro
                    # step golden e lo etichetterebbe FUORI-SEQ. Con un verdetto suo la
                    # guardia `verdict == "FAIL"` non scatta.
                    verdict = "KO POLARITA"
                    note.append("verso opto: CAPOVOLTO, la serigrafia e' girata di 180"
                                f" gradi (cos {cos_o:+.2f}) - GIRA IL PEZZO")
                else:
                    note.append(f"verso opto: non leggibile (cos {cos_o:+.2f})")
            else:
                note.append("verso opto: fori non disponibili")

        if cls == "diodo" and bsel_pts is not None and len(exp) == 2:
            # Banda argentata del diodo (catodo) dal profilo di luminanza lungo l'asse
            # del corpo (misurato: fette 44-95, salto a ~172 verso il catodo). Mai
            # bloccante: discordante = declassa (id_soft), illeggibile = dichiarato.
            # Corpo = componente SCURA più grande dentro la sagoma (il corpo nero; mai
            # l'erosione del blob intero: gambe lucide e ombre ingannano il profilo).
            # Verso = capo nella DIREZIONE della fetta chiara lungo l'asse (max/min t):
            # il corpo SOSPESO sta asimmetrico tra i capi, la distanza euclidea inganna.
            try:
                banda_msg = "banda del diodo: illeggibile dal profilo di luminanza (dichiarato)"
                silD, dx0, dy0 = _sil_of(bsel_pts)
                darkD = ((g1f[dy0:dy0 + silD.shape[0], dx0:dx0 + silD.shape[1]] < 115)
                         .astype(np.uint8) * 255) & silD
                darkD = cv2.morphologyEx(darkD, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
                nD, lD, stD, _ = cv2.connectedComponentsWithStats(darkD)
                iD = (1 + int(np.argmax(stD[1:, cv2.CC_STAT_AREA]))) if nD > 1 else 0
                if iD and stD[iD, cv2.CC_STAT_AREA] >= 300:
                    ysD, xsD = np.where(lD == iD)
                    PD = np.column_stack([xsD, ysD]).astype(np.float32)
                    (rcx, rcy), (rwD, rhD), angD = cv2.minAreaRect(PD)
                    LD, WD = max(rwD, rhD), min(rwD, rhD)
                    if WD >= 1 and LD / WD >= 1.8:
                        thD = np.deg2rad(angD if rwD >= rhD else angD + 90)
                        axD = np.array([np.cos(thD), np.sin(thD)])
                        tdk = (PD - [rcx, rcy]) @ axD
                        t0D, t1D = tdk.min() - 0.4 * pitch, tdk.max() + 0.4 * pitch
                        ysS, xsS = np.nonzero(silD)
                        PS = np.column_stack([xsS, ysS]).astype(np.float32)
                        tS = (PS - [rcx, rcy]) @ axD
                        ppS = (PS - [rcx, rcy]) @ np.array([-axD[1], axD[0]])
                        selS = (tS >= t0D) & (tS <= t1D) & (np.abs(ppS) <= WD / 2 + 2)
                        tS, lumS = tS[selS], g1f[ysS[selS] + dy0, xsS[selS] + dx0]
                        nbin = 11
                        eD = np.linspace(t0D, t1D, nbin + 1)
                        cntD, fs = [], []
                        for b_ in range(nbin):
                            sb = (tS >= eD[b_]) & (tS <= eD[b_ + 1])
                            cntD.append(int(sb.sum()))
                            fs.append(float(lumS[sb].mean()) if sb.sum() else np.nan)
                        medc = np.median([c_ for c_ in cntD if c_]) if any(cntD) else 0
                        valid = [b_ for b_ in range(nbin) if cntD[b_] >= 0.4 * medc]
                        if len(valid) >= 5:
                            fsv = [fs[b_] for b_ in valid]
                            medD = float(np.median(fsv))
                            pi_ = int(np.argmax(fsv))
                            if medD > 0 and fsv[pi_] >= 1.35 * medD \
                               and (pi_ <= 2 or pi_ >= len(valid) - 3):
                                texp = {uv: float((np.array(posx[uv], float)
                                                   - [dx0, dy0] - [rcx, rcy]) @ axD)
                                        for uv in exp if uv in posx}
                                verso = (max(texp, key=texp.get)
                                         if pi_ >= len(valid) - 3 else min(texp, key=texp.get))
                                mcat = _re.search(r"catodo\s+([a-j]\d+)", st.get("holes", ""))
                                cat = parse_hole(mcat.group(1)) if mcat else None
                                if cat is None:
                                    banda_msg = (f"banda chiara del diodo verso {name(*verso)}"
                                                 " (golden senza catodo dichiarato)")
                                elif verso == cat:
                                    banda_msg = (f"banda chiara del diodo verso {name(*verso)}"
                                                 " = catodo atteso")
                                else:
                                    banda_msg = None
                                    id_soft.append(f"banda chiara del diodo verso"
                                                   f" {name(*verso)}, catodo atteso"
                                                   f" {name(*cat)}: POLARITA' DA VERIFICARE"
                                                   " (lettura mai bocciante)")
                if banda_msg: note.append(banda_msg)
            except Exception:
                note.append("banda del diodo: lettura non riuscita (dichiarato)")

        for uv, nb, s0, sn in ambig:                     # test del vicino: sempre dichiarato
            note.append(f"{name(*uv)} AMBIGUO: vicino {name(*nb)}={sn:.2f} vs atteso {s0:.2f}"
                        f" — gamba forse in {name(*nb)}")

        if id_fail:                                      # identità sbagliata = FAIL
            verdict = "FAIL"
            note.extend(id_fail)
        elif id_soft:
            if verdict == "PASS": verdict = "STIMATO"
            note.extend(id_soft)

        # Fuori sequenza. Su FAIL il diff si confronta con gli step
        # golden non ancora soddisfatti: classe letta + colore + legscore sui LORO fori.
        # Match forte = l'operatore ha montato un altro passo -> si DICHIARA (mai in
        # silenzio) e quello step viene marcato; l'atteso resta aperto per le foto dopo.
        def quick_match(st2):
            cls2 = KIND2CLS.get(st2["kind"], st2["kind"])
            ok2 = {cls2} | ({"jumper", "cavetto"} if cls2 in FREE2 else set())
            if letto and letto not in ok2: return 0.0
            if st2["kind"] in ("led", "jumper"):
                ec2 = next((c for c in HUES if c in st2.get("el", "").lower()), None)
                if ec2 and col_letto and col_letto != ec2 and \
                   {col_letto, ec2} not in ({"giallo", "arancione"}, {"blu", "azzurro"}):
                    return 0.0
            exp2 = [parse_hole(h) for h in st2.get("coords", [])]
            if cls2 in HIDDEN:
                zx = [posx[uv][0] for uv in exp2 if uv in posx]
                zy = [posx[uv][1] for uv in exp2 if uv in posx]
                mm = 2.5 * pitch
                hit = any(zx and min(zx) - mm <= p[:, 0].mean() <= max(zx) + mm
                          and min(zy) - mm <= p[:, 1].mean() <= max(zy) + mm for p, _ in bl)
                return 0.5 if hit else 0.0
            ss = [legscore(g0f, g1f, posx, *uv) for uv in exp2 if uv in posx]
            if ss and min(ss) < QC_WEAK: return 0.0
            base = float(np.mean(ss)) if ss else 0.0
            for sg in rail_signs(st2, cls2):
                xs2 = [posx[uv][0] for uv in exp2 if uv in posx] or \
                      ([(bl[0][1][0] + bl[0][1][2]) / 2] if bl else [])
                if not xs2: return 0.0
                bestr = 0.0
                for uvr, pr in rpos.items():
                    if min(xs2) - 6 * pitch <= pr[0] <= max(xs2) + 6 * pitch \
                       and (sg is None or uvr[0][0] == sg):
                        bestr = max(bestr, legscore(g0f, g1f, posx, *uvr))
                if bestr < QC_THR: return 0.0
                base = max(base, 0.8 * bestr)
            return base

        # precedenza: se il pezzo È della classe giusta e c'è evidenza di +-1 (vicino
        # caldo/ambiguo), NON è un fuori-sequenza: è lo step atteso storto di un foro.
        seq_eligible = bool(id_fail) or (miss and not any(near_hot(uv) for uv in miss)
                                         and not ambig)
        if verdict == "FAIL" and bl and seq_eligible:
            cands_ = [(quick_match(build[j - 1]), j) for j in range(1, len(build) + 1)
                      if j != target and j not in satisfied]
            cands_ = [t for t in cands_ if t[0] >= QC_THR]
            # uno step con fori GRIGLIA è più specifico di uno solo-rail (che matcha
            # qualunque cavetto dello stesso colore): a parità vince il più specifico
            grid_c = [t for t in cands_ if build[t[1] - 1].get("coords")]
            if grid_c: cands_ = grid_c
            if cands_:
                sc_, j_ = max(cands_)
                verdict = "FUORI-SEQ"
                note.insert(0, f"il pezzo di questa foto corrisponde allo step {j_}"
                            f" ({build[j_ - 1]['el']}, match {sc_:.2f}): montaggio FUORI"
                            f" SEQUENZA; lo step {target} resta in attesa")
                satisfied.add(j_)

        # Radar sempre acceso: lettura cieca del diff di questo step, indipendente dal
        # golden — cosa vede il VLM + fori caldi con punteggio. Mai lista nuda: ogni
        # foro caldo etichettato
        # (zona-spessa / pezzo-noto / ombra / attraversato / vuoto).
        # sil_b/sil_o già costruiti sopra, riusati qui per il radar

        def radar_label(uv):
            lab = zone.get(uv)
            if lab in ("zona-spessa", "pezzo-noto", "cupola", "zona-testa"): return lab
            if sil_b is not None and uv not in sag_ends:
                x_, y_ = posx[uv][0] - sil_o[0], posx[uv][1] - sil_o[1]
                if 0 <= y_ < sil_b.shape[0] and 0 <= x_ < sil_b.shape[1] and sil_b[y_, x_]:
                    return "attraversato"       # sul filo: più informativo di 'ombra'
            return lab or "vuoto"

        radar_hot = []
        if bl:
            hh = region_holes(posx, bl[0][1], pad=80)
            hh += [uv for uv in rpos if uv not in hh
                   and bl[0][1][0] - 40 <= rpos[uv][0] <= bl[0][1][2] + 40]
            seen = set()
            for s, uv in sorted(((legscore(g0f, g1f, posx, *uv), uv) for uv in hh), reverse=True):
                n_ = name(*uv) if not isinstance(uv[0], str) else f"{uv[0]}{uv[1]}"
                if s >= QC_WEAK and n_ not in seen:
                    seen.add(n_); radar_hot.append((n_, s, uv))
                if len(radar_hot) >= 6: break

        over = cur.copy()
        for p in posx.values():                           # la MAPPA, in ogni overlay QC
            cv2.circle(over, p, 3, (0, 200, 0), -1)
        for uu in (0, 10):                                # etichette riga/colonna ai bordi
            for c_ in [1] + list(range(5, og.NCOLS, 5)) + [og.NCOLS]:
                if (uu, c_ - 1) in pos:
                    x_, y_ = pos[(uu, c_ - 1)]
                    cv2.putText(over, str(c_), (x_ - 8, y_ + (26 if uu == 0 else -16)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 80, 0), 1)
        for r_, u_ in og.ROW_U.items():
            if (u_, 0) in pos:
                x_, y_ = pos[(u_, 0)]
                cv2.putText(over, r_, (x_ - 30, y_ + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
        if sag_pts is not None:                           # la sagoma intera, evidenziata
            pp = sag_pts.astype(int)
            over[pp[:, 1], pp[:, 0]] = (over[pp[:, 1], pp[:, 0]] * 0.35
                                        + np.array((230, 200, 0)) * 0.65).astype(np.uint8)
            for uv in sag_ends:
                cv2.drawMarker(over, posx[uv], (230, 200, 0), cv2.MARKER_TILTED_CROSS, 24, 3)
        if corpo7 is not None:                            # base pin predetta dal corpo
            cv2.drawMarker(over, (int(corpo7[1][0]), int(corpo7[1][1])),
                           (0, 255, 255), cv2.MARKER_CROSS, 28, 3)
        for _, s, uv in radar_hot:                        # radar: fori caldi (magenta)
            cv2.circle(over, posx[uv], 6, (255, 0, 255), 2)
        for uv in exp:
            if uv not in posx: continue
            c = (0, 200, 0) if uv in oks else (0, 160, 255) if uv in weak else (0, 0, 255)
            cv2.circle(over, posx[uv], 12, c, 2)
            cv2.putText(over, name(*uv), (posx[uv][0] + 8, posx[uv][1] - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, c, 2)
        for uv, ok in rail_hits:                          # capo-rail verificato, visibile
            c = (0, 200, 0) if ok else (0, 0, 255)
            cv2.circle(over, posx[uv], 12, c, 2)
            cv2.putText(over, f"{uv[0]}{uv[1]}", (posx[uv][0] + 8, posx[uv][1] - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, c, 2)
        vcol = ((0, 200, 0) if verdict == "PASS" else
                (0, 160, 255) if verdict == "STIMATO" else (0, 0, 255))
        cv2.putText(over, f"foto {k} step {target} ATTESO: {st['el']} -> {verdict}", (40, 80),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, vcol, 3)
        if letto or col_letto:
            cv2.putText(over, f"radar: {letto or '?'} {col_letto or ''}", (40, 125),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 0, 255), 2)
        cv2.imwrite(f"qc_{k}.png", over)

        exp_txt = " ".join(name(*uv) for uv in exp) or "(solo rail)"
        rl = rail_signs(st, cls)
        if rl: exp_txt += "  + rail " + "/".join(s or "?" for s in rl)
        print(f"\nfoto {k} — step golden {target}")
        print(f"  ATTESO (golden): {st['el']} [{cls}] su {exp_txt}")
        print(f"  MISURATO fori attesi: "
              + (" ".join(f"{name(*uv)}={S.get(uv, 0):.2f}" for uv in exp) or "-"))
        # Doppio binario: accanto alla misura NCC grezza per-foro, il punteggio di
        # classe (coppia/terna/quadrupla/cerchio) con le sue soglie: entrambi nel
        # referto, sempre. Per transistor e rgb il punteggio è informativo (verdetto
        # ancora per-foro); per i fili il punteggio di classe è il per-foro.
        thr8, weak8 = cls_thr(cls)
        if btn is not None:
            bin8 = ("coppie di colonna " + " ".join(f"col {c+1}={p:.2f}"
                                                    for c, p in sorted(btn[0].items()))
                    + f" · quadrupla golden Q={btn[3].get(btn[2], 0.0):.2f}"
                    + f" (soglie bottone {thr8}/{weak8})")
        elif opt is not None:
            bin8 = ("coppie di colonna " + " ".join(f"col {c+1}={p:.2f}"
                                                    for c, p in sorted(opt[0].items()))
                    + f" · doppia terna golden T={opt[3].get(opt[2], 0.0):.2f}"
                    + f" (soglie optoaccoppiatore {thr8}/{weak8})")
        elif fam2 is not None:
            bin8 = (f"coppia capi (media geometrica) = {fam2[4]:.2f}"
                    + f" (soglie {cls} {thr8}/{weak8})")
        elif cls in HIDDEN or capv7:
            if corpo7 is not None:
                expc8 = [posx[uv] for uv in exp if uv in posx]
                if expc8:
                    g8x = sum(p[0] for p in expc8) / len(expc8)
                    g8y = sum(p[1] for p in expc8) / len(expc8)
                    r8 = float(np.hypot(corpo7[1][0] - g8x, corpo7[1][1] - g8y)) / pitch
                    bin8 = (f"geometria del corpo ({corpo7[0]}): residuo base-attesi"
                            f" {r8:.2f} passi (tolleranza {CORPO_TOL})")
                else:
                    bin8 = f"geometria del corpo ({corpo7[0]})"
            else:
                bin8 = "geometria del corpo: detector NON agganciato (ripiego zona-blob)"
        elif cls == "transistor" and exp:
            bin8 = (f"terna (media geometrica) = "
                    f"{pair_score([S.get(uv, 0.0) for uv in exp]):.2f}"
                    f" (soglie transistor {thr8}/{weak8}; informativo, verdetto per-foro)")
        elif cls == "rgb" and exp:
            bin8 = (f"quadrupla (min) = {min(S.get(uv, 0.0) for uv in exp):.2f}"
                    f" (soglie rgb {thr8}/{weak8}; informativo, verdetto per-foro)")
        elif cls in FREE2:
            bin8 = (f"fili: giudizio per-foro (soglie {thr8}/{weak8}),"
                    " capo-rail = conferma di nodo")
        else:
            bin8 = None
        if bin8: print(f"  PUNTEGGIO DI CLASSE: {bin8}")
        print(f"  RADAR (cieco, senza golden): Gemini vede '{letto or '?'}'"
              + (f" {col_letto}" if col_letto else "")
              + " | fori caldi: "
              + (", ".join(f"{n_}={s:.2f} [{radar_label(uv)}]"
                           for n_, s, uv in radar_hot) or "nessuno"))
        # geometria della classe: il radar propone una lettura completa
        # con la grammatica del pezzo (es. bottone = 4 pin speculari): DEDOTTA, non misurata
        if bl and cls in PINS:
            try:
                hh2 = region_holes(posx, bl[0][1], pad=80)
                hh2 += [uv for uv in rpos if uv not in hh2
                        and bl[0][1][0] - 40 <= rpos[uv][0] <= bl[0][1][2] + 40]
                prop = fit_pins(g0f, g1f, posx, hh2, cls, bl[0][0], None, exclude=zone,
                                rows6=opt[6] if opt is not None else None)
                print(f"  RADAR propone (geometria '{cls}', dedotto non misurato): "
                      + " ".join((name(*uv) if not isinstance(uv[0], str)
                                  else f"{uv[0]}{uv[1]}") for uv in prop))
            except Exception:
                pass
        print(f"  VERDETTO: {verdict}")
        for n_ in note: print(f"    - {n_}")
        if verdict != "FUORI-SEQ": satisfied.add(target)
        # Lo step processato diventa "pezzo noto" per i successivi (fori golden dello
        # step montato + fori sulla silhouette del suo blob — mai l'hull convesso: su un
        # blob sparso reclamerebbe mezze rail, misurato)
        ph = build[j_ - 1] if verdict == "FUORI-SEQ" else st
        for h in ph.get("coords", []):
            uv_ = parse_hole(h)
            if uv_ in posx: prior_zone.add(uv_)
        if sil_b is not None:
            silp = cv2.dilate(sil_b, np.ones((9, 9), np.uint8))
            for uv_, p_ in posx.items():
                x_, y_ = p_[0] - sil_o[0], p_[1] - sil_o[1]
                if 0 <= y_ < silp.shape[0] and 0 <= x_ < silp.shape[1] and silp[y_, x_]:
                    prior_zone.add(uv_)
        report.append(verdict)

    # bilancio finale: TUTTI i fori attesi devono essere ancora occupati in foto N vs 0
    # (becca i pezzi saltati via da un urto negli step successivi)
    img0, gray0, *_ = frame(0)
    curN, gN, posN, rposN, _ = frame(nsteps)
    A0 = register(gray0, gN)
    g00 = cv2.cvtColor(cv2.warpAffine(img0, A0, (curN.shape[1], curN.shape[0])),
                       cv2.COLOR_BGR2GRAY).astype(np.float32)
    g11 = gN.astype(np.float32)
    posxN = {**posN, **rposN}
    lost = []
    for k in range(1, nsteps + 1):
        for h in build[k - 1].get("coords", []):
            uv = parse_hole(h)
            if uv in posxN and legscore(g00, g11, posxN, *uv) < QC_WEAK:
                lost.append(f"step {k} {h}")
    print(f"\nesito: {report.count('PASS')} PASS, {report.count('STIMATO')} STIMATO,"
          f" {report.count('FAIL')} FAIL, {report.count('FUORI-SEQ')} FUORI-SEQUENZA"
          f" su {nsteps} foto")
    unsat = [j for j in range(1, nsteps + 1) if j not in satisfied]
    if unsat:
        print("step golden MAI rilevati:",
              ", ".join(f"{j} ({build[j - 1]['el']})" for j in unsat))
    if lost:
        print("bilancio finale (foto N vs 0), fori attesi MUTI:", "; ".join(lost))
    else:
        print("bilancio finale: tutti i fori attesi risultano occupati nella foto finale")

def register(g0, g1):
    """Affine g0->g1 da feature ORB + RANSAC: indipendente da fori/etichette."""
    orb = cv2.ORB_create(2000)
    k1, d1 = orb.detectAndCompute(g0, None)
    k2, d2 = orb.detectAndCompute(g1, None)
    m = sorted(cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(d1, d2),
               key=lambda x: x.distance)[:400]
    A, _ = cv2.estimateAffinePartial2D(np.float32([k1[i.queryIdx].pt for i in m]),
                                       np.float32([k2[i.trainIdx].pt for i in m]),
                                       method=cv2.RANSAC, ransacReprojThreshold=3)
    return A

FORCEMAP = False        # --forcemap: prosegui anche con mappa degradata (sconsigliato)
DESKEW_MIN = 0.25       # gradi: sotto questa inclinazione non si raddrizza

def board_rect_mask(img):
    """Maschera della sola board (sfondo nero + aloni di lampada esclusi).
    La plastica è bianca: V alta, S bassa. Si prendono le strisce bianche grandi
    (i 4 blocchi), il minAreaRect della loro unione + margine. Calcolata UNA volta
    sulla foto 0 e riusata identica su tutte le foto: fuori-board diventa nero
    IDENTICO ovunque -> il diff lì è zero per costruzione."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    white = ((hsv[:, :, 1] < 70) & (hsv[:, :, 2] > 120)).astype(np.uint8)
    white = cv2.morphologyEx(white, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
    n, lbl, stats, _ = cv2.connectedComponentsWithStats(white)
    if n < 2: return None
    amax = stats[1:, cv2.CC_STAT_AREA].max()
    keep = [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] > 0.05 * amax]
    ys, xs = np.where(np.isin(lbl, keep))
    (cx, cy), (w, h), ang = cv2.minAreaRect(np.column_stack([xs, ys]).astype(np.float32))
    box = cv2.boxPoints(((cx, cy), (w + 100, h + 100), ang))
    mask = np.zeros(img.shape[:2], np.uint8)
    cv2.fillConvexPoly(mask, box.astype(np.int32), 255)
    return mask

def marker_angle(markers):
    """Angolo (gradi) della direzione-colonne della board rispetto all'orizzonte foto.
    Fit affine ai soli quattro angoli (build_model li mette in testa alla lista).

    Non su tutti i marker: la lista ne contiene 19-66, non 10, perché
    detect_yellow_markers raccoglie anche i falsi blob gialli sulle rail (segni di
    penna, righe rosse/blu stampate) e build_model li etichetta riga j/a a una colonna
    dedotta, mentre stanno ~80 px fuori dalla riga vera. Il fit li insegue e storce la
    direzione-colonne. Misurato su 8 scatti 0 con board fisicamente dritta:
        pendenza vera righe a/j   -0.08 .. +0.14 gradi
        fit sui 4 angoli          +0.01 .. +0.24 gradi
        fit su tutti i marker     +0.10 .. +1.81 gradi   <- sopra 0.25 scatta il deskew
    Con il fit su tutti i marker la foto 0 veniva ruotata di 1.3-2.7 gradi pur essendo
    dritta, e la mappa ricostruita perdeva le rail (24-43 fori su 50). Gli angoli sono
    gli unici marker con riga e colonna certe."""
    markers = markers[:4]
    A = np.array([[v, u, 1.0] for u, v, x, y in markers])
    X = np.array([x for _, _, x, _ in markers], float)
    Y = np.array([y for _, _, _, y in markers], float)
    cx = np.linalg.lstsq(A, X, rcond=None)[0]
    cy = np.linalg.lstsq(A, Y, rcond=None)[0]
    return float(np.degrees(np.arctan2(cy[0], cx[0])))

def map_health(model, markers):
    """Segnali oggettivi di mappa rotta:
    - rail incomplete / marker persi (guasto tipico da board storta);
    - righe griglia non rettilinee (guasto da relabel: punti che saltano tra righe
      fisiche diverse, misurato: residui 55-68 px)."""
    probs = []
    for nm in ("+b", "-b", "+t", "-t"):
        n = len(model.get("rails", {}).get(nm, []))
        if n == 50: continue
        if n == 51:      # un blob extra in linea (segno/adesivo nel solco): avviso, non blocco
            xs = sorted(x for c, x, y in model["rails"][nm])
            print(f"[mappa: rail {nm} con 1 blob EXTRA (x estremi {xs[0]:.0f}-{xs[-1]:.0f})"
                  " - probabile segno/adesivo nel solco: verifiche rail li' NON affidabili]")
        else:
            probs.append(f"rail {nm}: {n}/50 fori")
    # Questo conteggio non è un segnale affidabile: `markers` comprende gli intermedi,
    # e fra quelli finiscono i falsi blob gialli delle rail (19-66 marker dove i veri
    # sono 10). Non ha mai prodotto un falso OK da solo; il guasto che nasceva da quei
    # blob era l'angolo del deskew, gestito in marker_angle. Il filtro alla fonte
    # (dentro detect_yellow_markers) resta da fare: v. nota lì.
    # Soglia 4: la board può avere i soli 4 marker d'angolo (gli intermedi non sono
    # ancore: fit, omografia e deskew usano solo gli angoli).
    if len(markers) < 4: probs.append(f"solo {len(markers)} marker trovati")
    pos = og.positions(model)
    pitch = abs(pos[(0, 1)][0] - pos[(0, 0)][0]) or 29
    for u in sorted({u for u, v in pos}):
        pts_ = sorted((v, p) for (uu, v), p in pos.items() if uu == u)
        xs = np.array([p[0] for _, p in pts_], float)
        ys = np.array([p[1] for _, p in pts_], float)
        res = np.polyval(np.polyfit(xs, ys, 1), xs) - ys
        bad = [(v + 1, abs(r)) for (v, _), r in zip(pts_, res) if abs(r) > 0.4 * pitch]
        if not bad: continue
        if len(bad) > 6 or any(c < 56 for c, _ in bad):
            probs.append(f"riga {U_ROW[u]} non rettilinea (col {', '.join(str(c) for c, _ in bad)})")
        else:   # solo bordo destro estremo, fori interpolati: avviso, non blocco
            print(f"[mappa: riga {U_ROW[u]} imprecisa su col {', '.join(str(c) for c, _ in bad)}"
                  " (bordo interpolato) - verdetti li' NON affidabili]")
    # Scala delle righe. Uno slittamento coerente (riga fisica
    # saltata -> blocco su di uno, 'è nel canale) lascia righe dritte e rail piene:
    # si vede solo dai GAP tra le mediane y (1 passo in blocco, ~3 passi nel canale).
    med = {u: float(np.median([p[1] for (uu, v), p in pos.items() if uu == u]))
           for u in sorted({u for u, v in pos})}
    if len(med) == 10:
        pv = (med[0] - med[10]) / 11.0
        if pv <= 4:
            probs.append("righe verticalmente collassate/invertite")
        else:
            LADDER = [(0, 1, 1), (1, 2, 1), (2, 3, 1), (3, 4, 1), (4, 6, 3),
                      (6, 7, 1), (7, 8, 1), (8, 9, 1), (9, 10, 1)]
            for u1, u2, steps in LADDER:
                gap = (med[u1] - med[u2]) / pv
                if not steps * 0.65 <= gap <= steps * 1.35:
                    probs.append(f"gap righe {U_ROW[u1]}->{U_ROW[u2]}: {gap:.1f} passi (attesi {steps})")
    return probs

def make_frame():
    cache = {}
    m0 = {"mask": None}
    mkc = {}                     # anti-alias: corner marker per foto (None = non rilevati)
    def _corners(img):
        try:
            ck, _ = bm.detect_yellow_markers(img)
            return {(u, v): (float(x), float(y)) for u, v, x, y in ck}
        except BaseException:            # detect_yellow_markers esce con sys.exit
            return None
    def frame(k):
        """(img, gray, pos, A) della foto k. La mappa si costruisce UNA volta sulla board
        vuota (foto 0) e si PROPAGA: registrazione ORB (la board si muove quando inserisci
        un componente) + ri-aggancio fine di ogni centro. Le etichette non si ricalcolano mai."""
        if k in cache: return cache[k]
        img = cv2.imread(f"{k}.jpeg")
        if img is None: sys.exit(f"manca {k}.jpeg")
        if k == 0 and m0["mask"] is None:
            m0["mask"] = board_rect_mask(img)
            if m0["mask"] is None: print("[P9: board non isolabile, foto intera]")
        if k > 0: frame(k - 1)                       # garantisce la maschera della foto 0
        if m0["mask"] is not None:
            img = cv2.bitwise_and(img, img, mask=m0["mask"])
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        if k == 0:
            model, markers = bm.build_model(img, verbose=False)
            ang = marker_angle(markers)
            if abs(ang) > DESKEW_MIN:
                # board storta -> si raddrizza la foto 0 e si ricostruisce la mappa.
                # (detect_rails lavora per bande orizzontali: ~1 grado basta a mischiare
                # le due file di una rail). Le foto 1..N restano com'erano: la
                # registrazione ORB assorbe la rotazione residua tra foto.
                M = cv2.getRotationMatrix2D((img.shape[1] / 2, img.shape[0] / 2), ang, 1.0)
                img = cv2.warpAffine(img, M, (img.shape[1], img.shape[0]), flags=cv2.INTER_CUBIC)
                g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                model, markers = bm.build_model(img, verbose=False)
                res = marker_angle(markers)
                print(f"[deskew foto 0: {ang:+.2f} gradi dai marker, residuo {res:+.2f}]")
            probs = map_health(model, markers)
            if probs:
                msg = "MAPPA DEGRADATA: " + "; ".join(probs)
                if FORCEMAP:
                    print(f"!! {msg} (--forcemap: continuo, risultati NON affidabili)")
                else:
                    sys.exit(f"!! {msg}\n!! run interrotto: verdetti inaffidabili con mappa"
                             " rotta. Rifare la foto 0 (board dritta) o --forcemap.")
            pos = og.positions(model)
            rpos = {(nm, int(c)): (int(x), int(y))               # fori rail: chiave ('+b', col)
                    for nm, line in model.get("rails", {}).items() for c, x, y in line}
            # Maschera simmetrica: il rettangolo di board_rect_mask dipende dalla luce
            # (sotto, al buio, chiudeva a ~3 passi dalla rail; sopra, con lampada alta,
            # restava aperto fino a 21-25 passi, misurato su 4 commesse). A mappa fatta
            # la maschera si stringe simmetrica: 3.2 passi oltre le rail, ancorata alla
            # board e non alla luce. Con deskew attivo il bordo banda sulle foto k>0 può
            # slittare di ~13 px (rotazione assorbita da ORB): irrilevante dentro un
            # margine di ~96 px.
            try:
                yt = float(np.median([p[1] for uv, p in rpos.items() if uv[0].endswith("t")]))
                yb = float(np.median([p[1] for uv, p in rpos.items() if uv[0].endswith("b")]))
                pv = abs(pos[(0, 1)][0] - pos[(0, 0)][0]) or 29
                y0c, y1c = max(0, int(yt - 3.2 * pv)), min(img.shape[0], int(yb + 3.2 * pv))
                if m0["mask"] is not None and yb > yt:
                    clamp = np.zeros(img.shape[:2], np.uint8)
                    clamp[y0c:y1c, :] = 255
                    m0["mask"] = m0["mask"] & clamp
                    img = cv2.bitwise_and(img, img, mask=clamp)
                    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                    print(f"[P9-sym: banda utile y {y0c}-{y1c} (rail +-3.2 passi)]")
            except Exception:
                print("[P9-sym: rail non disponibili, maschera P9 invariata]")
            mkc[0] = _corners(img)
            A = None
        else:
            _, gp, pos_p, rpos_p, _ = frame(k - 1)
            A = register(gp, g)
            def T(p):
                return (A[0,0]*p[0] + A[0,1]*p[1] + A[0,2], A[1,0]*p[0] + A[1,1]*p[1] + A[1,2])
            # Anti-alias (misurato: drift di +0.95 passi su una serie di 4 foto):
            # sulla texture periodica dei fori l'ORB può agganciare il consenso spostato
            # di un passo intero; il refine (+-8px) lo cementa sul foro sbagliato e TUTTE
            # le etichette slittano di una colonna. I marker gialli d'angolo sono ancore
            # ASSOLUTE (indipendenti da fori/etichette): se il residuo mediano marker vs
            # trasformazione supera mezzo passo, si corregge la traslazione di A.
            mk_p, mk_k = mkc.get(k - 1), _corners(img)
            mkc[k] = mk_k
            if mk_p and mk_k:
                common = set(mk_p) & set(mk_k)
                if len(common) >= 3:
                    dxa = float(np.median([mk_k[c][0] - T(mk_p[c])[0] for c in common]))
                    dya = float(np.median([mk_k[c][1] - T(mk_p[c])[1] for c in common]))
                    pv_ = abs(pos_p[(0, 1)][0] - pos_p[(0, 0)][0]) or 29
                    if max(abs(dxa), abs(dya)) > 0.5 * pv_:
                        # La sola traslazione non basta: se l'ORB ha agganciato il
                        # consenso sbagliato anche rotazione e
                        # scala sono sospette, e il diff prev-vs-cur resta sregistrato
                        # (misurato: blob da 101k px con 5 soli fori caldi — la firma
                        # della sregistrazione, non di un corpo estraneo). I marker sono
                        # ancore assolute: con >=3 comuni si RICOSTRUISCE l'intera
                        # similitudine da loro e si butta la stima ORB.
                        src = np.float32([mk_p[c] for c in sorted(common)])
                        dst = np.float32([mk_k[c] for c in sorted(common)])
                        A_mk, _ = cv2.estimateAffinePartial2D(src.reshape(-1, 1, 2),
                                                              dst.reshape(-1, 1, 2))
                        if A_mk is not None:
                            A = A_mk.astype(A.dtype)
                            print(f"[anti-alias foto {k}: registrazione ORB slittata di "
                                  f"({dxa:+.1f},{dya:+.1f})px ~{max(abs(dxa), abs(dya))/pv_:.2f}"
                                  " passi - trasformazione RIFATTA dai marker]")
                        else:
                            print(f"[anti-alias foto {k}: registrazione ORB slittata di "
                                  f"({dxa:+.1f},{dya:+.1f})px ~{max(abs(dxa), abs(dya))/pv_:.2f}"
                                  " passi - corretta coi marker]")
                            A[0, 2] += dxa; A[1, 2] += dya
            pos, rpos = {}, {}
            for src, dst in ((pos_p, pos), (rpos_p, rpos)):
                for uv, p in src.items():
                    q = T(p)
                    rx, ry, ok = bm.refine(g, q[0], q[1], 8)
                    dst[uv] = (int(rx), int(ry)) if ok else (int(round(q[0])), int(round(q[1])))
        cache[k] = (img, g, pos, rpos, A)
        return cache[k]
    return frame

def main():
    global FORCEMAP
    args = sys.argv[1:]
    novlm = "--novlm" in args
    FORCEMAP = "--forcemap" in args
    args = [a for a in args if a not in ("--novlm", "--forcemap")]
    if "--qc" in args:
        i = args.index("--qc")
        if i + 1 >= len(args): sys.exit("uso: python differential.py --qc <ID commessa> [--novlm]")
        return qc_main(args[i + 1], novlm)
    import os
    nmax = max(int(f[:-5]) for f in os.listdir(".") if f.endswith(".jpeg") and f[:-5].isdigit())
    steps = [int(args[0])] if args and args[0].isdigit() else list(range(1, nmax + 1))
    force = args[1].lower() if len(args) > 1 else None       # forza la classe (salta il VLM)

    frame = make_frame()

    state = []                    # componenti già piazzati: {"cls", "holes"} (etichette stabili tra foto)
    for k in steps:
        prev = frame(k - 1)[0]
        cur, _, pos, rpos, A = frame(k)
        prev = cv2.warpAffine(prev, A, (cur.shape[1], cur.shape[0]))
        posx = {**pos, **rpos}
        bmask = board_mask(posx, cur.shape, grow=140)  # RAIL incluse + margine largo: il cavetto
                                                       # flessibile SCAVALCA il bordo board (visto)
        def save_overlay(fori=(), note=""):
            """L'overlay diff_K.png esiste per OGNI foto, anche se non rileva nulla."""
            over = cur.copy()
            for p in posx.values():
                cv2.circle(over, p, 3, (0, 200, 0), -1)
            for uv in fori:
                cv2.circle(over, posx[uv], 12, (255, 0, 255), 2)
                cv2.putText(over, name(*uv), (posx[uv][0]+8, posx[uv][1]-8),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 0, 255), 2)
            if note:
                cv2.putText(over, note, (40, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 0, 255), 3)
            cv2.imwrite(f"diff_{k}.png", over)
        bl = find_blobs(cur, prev, bmask)
        print(f"\nstep {k-1}->{k}:", end=" ")
        if not bl:
            print("nessun componente rilevato")
            save_overlay(note="NESSUN COMPONENTE RILEVATO - VERIFICA"); continue
        g0f = cv2.cvtColor(prev, cv2.COLOR_BGR2GRAY).astype(np.float32)
        g1f = cv2.cvtColor(cur, cv2.COLOR_BGR2GRAY).astype(np.float32)

        # STATO commessa: un blob con >=2 pin di un componente già piazzato DENTRO (punti
        # del blob, non bbox) = URTO/spostamento, non un componente nuovo -> si salta con
        # avviso (il suo diff contiene vecchia E nuova posizione: non ci si fitta sopra).
        # Il nuovo è il primo blob che non tocca niente di noto.
        from scipy.spatial import cKDTree
        def touched(pts):
            tree = cKDTree(pts[::7])
            sample = pts[::25]
            out = []
            for c in state:
                centers = [posx[uv] for uv in c["holes"] if uv in posx]
                if sum(1 for p in centers if tree.query(p)[0] < 12) >= 2:
                    out.append(c); continue
                if c.get("hull") is not None and len(sample):   # urto per SAGOMA (es. cavetto:
                    inside = sum(1 for p in sample              # 2 soli pin, uno sotto guaina)
                                 if cv2.pointPolygonTest(c["hull"], (float(p[0]), float(p[1])), False) >= 0)
                    if inside >= max(4, 0.15 * len(sample)): out.append(c)
            return out
        def valid_blob(pts, bbox):
            """Un CAMBIO DI LUCE (ombra proiettata, striscia) cambia la luminosità ma non
            la struttura: legscore ~0 ovunque. Un componente vero ha fori che segnano."""
            hh = region_holes(posx, bbox, pad=40)
            return any(legscore(g0f, g1f, posx, *uv) >= 0.10 for uv in hh)
        newb, bumped = None, []
        for pts, bbox in bl:
            t = touched(pts)
            if t:
                bumped += [c for c in t if c not in bumped]
            elif newb is None and valid_blob(pts, bbox):
                newb = (pts, bbox)
        for comp in bumped:
            # URTATO: i pin restano nei loro fori (si è mossa solo la parte che sporge).
            # Le coordinate REGISTRATE sono più affidabili di qualunque ricalcolo sul diff.
            print(f"[urtato: {comp['cls']} {comp.get('col','')} {[name(*uv) for uv in comp['holes']]} - coordinate mantenute]", end=" ")

        def analyze(pts, bbox, cls=None, col=None):
            """Classifica (se serve) e localizza il componente di UN blob."""
            holes = region_holes(posx, bbox, pad=80)   # le gambe sporgono oltre il corpo
            # le rail nella fascia x del blob entrano SEMPRE (un jumper può tuffarcisi
            # anche se il diff del filo non arriva visivamente fin la'); pesano solo lì
            holes += [uv for uv in rpos if uv not in holes and bbox[0]-40 <= rpos[uv][0] <= bbox[2]+40]
            # Il colore certifica — un filo saturo allungato
            # = jumper/cavetto qualunque cosa dica il classificatore — ma l'inseguimento
            # lo guida la SAGOMA del diff (vede anche le guaine des-saturate).
            if cls is None and not force:
                wb = colored_wire(cur, pts)
                if wb is not None:
                    col = blob_color(cur, wb[0])
                    fori = fit_pins(g0f, g1f, posx, holes, "jumper", pts, None if novlm else cur)
                    fori.sort(key=lambda uv: (ru(uv[0]), uv[1]))
                    return "jumper", col, fori
            if cls is None:
                S = sorted(((occ(g0f, g1f, posx, *uv), uv) for uv in holes),
                           key=lambda t: t[0], reverse=True)
                if force: cls, col = force, ""
                elif novlm: return None, None, [uv for _, uv in S[:6]]
                else:
                    # al classificatore si mostra il componente intero (inquadratura
                    # dal blob, non dal foro più occluso che tagliava mezzo corpo)
                    cx, cy = int(pts[:, 0].mean()), int(pts[:, 1].mean())
                    hw = max(170, (bbox[2] - bbox[0]) // 2 + 80)
                    hh = max(130, (bbox[3] - bbox[1]) // 2 + 80)
                    cls = vlm_classify(cur[max(0, cy - hh):cy + hh, max(0, cx - hw):cx + hw])
                    if not cls: return None, None, [uv for _, uv in S[:6]]
                    col = blob_color(cur, pts)
            fori = fit_pins(g0f, g1f, posx, holes, cls, pts, None if novlm else cur)
            fori.sort(key=lambda uv: (ru(uv[0]), uv[1]))
            return cls, col, fori

        if bumped:
            # PROTOCOLLO "COSA È ARRIVATO": il VLM lo dice col registro come contesto;
            # il vecchio si esclude GEOMETRICAMENTE (zone dei componenti registrati),
            # così due jumper dello stesso colore in step diversi non si confondono.
            acls, acol = (force, "") if force else ((None, None) if novlm else
                                                    vlm_arrived(cur, [b[1] for b in bl], state))
            excl = np.zeros(bmask.shape[:2], np.uint8)
            for c in state:
                P = [posx[uv] for uv in c["holes"] if uv in posx]
                for p in P: cv2.circle(excl, p, 28, 255, -1)
                if len(P) >= 2:
                    if c["cls"] in FREE2: cv2.line(excl, P[0], P[-1], 255, 55)
                    else: cv2.fillConvexPoly(excl, cv2.convexHull(np.array(P, np.int32)), 255)
                if c.get("hull") is not None:                 # sagoma registrata: esclusa comunque
                    cv2.fillConvexPoly(excl, c["hull"], 255)
            excl = cv2.dilate(excl, np.ones((41, 41), np.uint8))
            hunt = bmask & ~excl
            cand = color_blob(cur, acol, hunt) if acol else None    # 1) punta il COLORE indicato
            if cand is None:                                        # 2) diff residuo fuori dalle zone note
                bl2 = [b for b in find_blobs(cur, prev, hunt)
                       if not touched(b[0]) and valid_blob(*b)]
                cand = bl2[0] if bl2 else newb
            if cand is None:
                print(f"VLM dice: arrivato {acls or '?'} {acol or ''} ma non lo isolo: !! VERIFICA MANUALE")
                save_overlay(note="NUOVO NON ISOLATO - VERIFICA MANUALE"); continue
            srcpts = cand[0]
            cls, col, fori = analyze(cand[0], cand[1], acls, acol)
        elif newb is None:
            print("solo cambi di luce/ombre nel diff, nessun componente")
            save_overlay(note="SOLO CAMBI DI LUCE - VERIFICA"); continue
        else:
            srcpts = newb[0]
            cls, col, fori = analyze(*newb)

        print(f"VLM dice: {(cls or '?') + (' ' + col if col else '')}", end="")
        if not cls:
            print(f"  | candidati: {', '.join(name(*uv) for uv in fori)}")
            save_overlay(note="CLASSE NON DETERMINATA (quota VLM?)"); continue
        print(f"  ->  {cls} su {[name(*uv) for uv in fori]}")
        # nel registro entra anche la SAGOMA del blob: pure con la classe sbagliata,
        # l'AREA del componente resta esclusa dalle cacce degli step successivi
        state.append({"cls": cls, "col": col or "", "holes": fori,
                      "hull": cv2.convexHull(srcpts[::9].astype(np.int32))})
        save_overlay(fori)

if __name__ == "__main__":
    main()
