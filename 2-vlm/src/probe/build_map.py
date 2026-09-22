"""
build_map.py — mappa "buco per buco": rileva il centro esatto di ogni foro sulla
board vuota e salva la posizione di tutti i 630 fori (10 righe x 63) (tabella diretta, zero interpolazione).

Parte dalla calibrazione attuale (poly/homography) come guida: per ogni foro logico
predice il pixel, poi RAFFINA agganciandosi al centroide del foro scuro più vicino.

Uso: python build_map.py board_vuota2.jpg.jpeg [--win 13] [--show]
Salva la tabella in calib.json. Poi:  python overlay_grid.py IMG --reuse
"""
import sys, json, cv2
import numpy as np
import overlay_grid as og

def refine(gray, x, y, win):
    x0, y0 = int(x) - win, int(y) - win
    patch = gray[max(0,y0):y0+2*win, max(0,x0):x0+2*win]
    if patch.size == 0 or patch.shape[0] < 2*win or patch.shape[1] < 2*win:
        return x, y, False
    if patch.mean() < 80:                               # fuori board (sfondo scuro): niente aggancio
        return x, y, False
    _, th = cv2.threshold(patch, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    n, lbl, stats, cent = cv2.connectedComponentsWithStats(th)
    best, bd = None, 1e9
    for i in range(1, n):
        area = stats[i, cv2.CC_STAT_AREA]
        w, h = stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]
        if area < 25:                                   # rumore
            continue
        # scarta i NUMERI stampati: sono sottili/allungati o poco pieni. I fori sono quadrati pieni.
        ar = w / max(h, 1)
        if ar < 0.55 or ar > 1.8:                       # non quadrato -> tratto di cifra
            continue
        if area / (w * h) < 0.45:                       # poco pieno -> glifo cavo
            continue
        cx, cy = cent[i]
        d = (cx - win)**2 + (cy - win)**2               # componente più vicina al centro predetto
        if d < bd:
            bd, best = d, (x0 + cx, y0 + cy)
    if best and bd < (win*0.8)**2:                      # accetta solo se vicino alla predizione
        return best[0], best[1], True
    return x, y, False

def detect_yellow_markers(img):
    """Rileva i marker gialli su righe j (alto) e a (basso). NON assume le colonne dei
    marker intermedi (un marker piazzato una colonna più in là ancorerebbe la mappa
    sul foro sbagliato): etichetta SOLO gli angoli (estremi x di ogni riga, col 1 e 63).
    Ritorna (corners = [(u, v, x, y)] x4, middles = [(u, x, y)])."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (18, 70, 80), (38, 255, 255))     # giallo; legno/sfondo (S~20-25) esclusi dal
                                                              # filtro dimensione sotto. Soglie S/V basse
                                                              # perché i marker slavati dall'upscale si perdono
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))  # ricompatta frammenti
    n, lbl, stats, cent = cv2.connectedComponentsWithStats(mask)
    pts = [tuple(cent[i]) for i in range(1, n)
           if 80 <= stats[i, cv2.CC_STAT_AREA] <= 3000            # marker piccolo (scarta rumore E blob enormi)
           and stats[i, cv2.CC_STAT_WIDTH] < 90 and stats[i, cv2.CC_STAT_HEIGHT] < 90]
    # i marker veri stanno su DUE righe allineate: un blob senza compagno alla stessa
    # quota y è rumore (capelli/graffi gialli sul piano), fuori.
    pts = [p for p in pts if sum(1 for q in pts if q is not p and abs(q[1] - p[1]) < 30) >= 1]
    # Limite noto: questo filtro non basta. Segni di penna e righe rosse/blu stampate
    # sulle rail passano il colore e si fanno compagnia a vicenda (misurati 19-66 blob
    # accettati dove i marker veri sono 10). Finiscono in `middles` con colonna dedotta,
    # ~80 px fuori dalla riga vera. Conseguenze gestite a valle: l'angolo di deskew usa
    # i soli 4 angoli (marker_angle) e il check `len(markers) < 8` di map_health non
    # scatta mai. Rischio residuo mai osservato: un blob spurio più esterno in x del
    # marker di col 1 o 63 diventerebbe àncora d'angolo. Un filtro alla fonte richiede
    # un ancoraggio robusto alle due righe vere: nei casi peggiori gli spuri sono 50
    # contro 10, quindi mediana e consenso semplice scelgono la rail, non la riga.
    if len(pts) < 4:
        sys.exit(f"Trovati solo {len(pts)} marker gialli validi (min 4). Regola luce/soglia.")
    ymid = (min(p[1] for p in pts) + max(p[1] for p in pts)) / 2
    corners, middles = [], []
    for r, row in (("j", [p for p in pts if p[1] < ymid]), ("a", [p for p in pts if p[1] >= ymid])):
        row.sort(key=lambda p: p[0])
        corners += [(*og.ideal(r, 1), *row[0]), (*og.ideal(r, og.NCOLS), *row[-1])]
        middles += [(og.ideal(r, 1)[0], x, y) for x, y in row[1:-1]]
    return corners, middles

def markers_corners(markers):
    d = {(u, v): (x, y) for u, v, x, y in markers}
    return [d[og.ideal("a", 1)], d[og.ideal("j", 1)], d[og.ideal("a", og.NCOLS)], d[og.ideal("j", og.NCOLS)]]

def snap_rail(pts):
    """Aggancia i blob di una linea di alimentazione al TEMPLATE rigido della rail:
    10 gruppi x 5 fori, passo p, gap di gruppo = 2p. Dedup, riempie i mancanti,
    scarta gli spuri (scarabocchi/stampa). Ritorna sempre 50 (x, y)."""
    pts = sorted(pts)
    xs = np.array([p[0] for p in pts], float)
    ys = np.array([p[1] for p in pts], float)
    ki = [0]                                          # dedup: blob a <13px = stesso foro
    for i in range(1, len(xs)):
        if xs[i] - xs[ki[-1]] >= 13: ki.append(i)
    xs, ys = xs[ki], ys[ki]
    d = np.diff(xs)
    p = float(np.median(d[(d > 18) & (d < 40)]))      # passo intra-gruppo
    cy = np.polyfit(xs, ys, 1)                        # y della linea (leggera pendenza)
    groups = np.split(np.arange(len(xs)), np.where(d > 40)[0] + 1)
    if len(groups) != 10:                             # struttura rotta: rendi quel che c'è
        return [(float(x), float(y)) for x, y in zip(xs, ys)]
    out = []
    slots = np.arange(5) * p
    for g in groups:                                  # snap LOCALE: 5 slot per gruppo, deriva ~0
        gx = xs[g]
        best = None
        for bx in gx:                                 # ancoraggio: ogni blob su ogni slot
            for s in slots:
                sh = bx - s
                err = sum(min(float(np.abs(gx - (sh + sl)).min()), 15.0) for sl in slots)
                if best is None or err < best[0]: best = (err, sh)
        for sl in slots:
            t = best[1] + sl
            j = int(np.abs(gx - t).argmin())
            if abs(gx[j] - t) <= 6: out.append((float(gx[j]), float(ys[g][j])))
            else: out.append((float(t), float(np.polyval(cy, t))))   # foro coperto: dallo schema
    return out

def detect_rails(gray, pos):
    """Rileva i fori delle 4 righe di alimentazione (2 sotto, 2 sopra i blocchi).
    Convenzione: basso esterna='+b', interna='-b';
    alto interna='+t', esterna='-t'.
    Ritorna {nome: [[col, x, y], ...]} con col = colonna della griglia principale più vicina."""
    ya = float(np.median([y for (u, v), (x, y) in pos.items() if u == 0]))
    yj = float(np.median([y for (u, v), (x, y) in pos.items() if u == 10]))
    pv = (ya - yj) / 11.0                               # passo verticale medio (10 righe + gap)
    xs = [x for (u, v), (x, y) in pos.items()]
    x0, x1 = max(0, int(min(xs)) - 25), min(gray.shape[1], int(max(xs)) + 25)
    bands = {"b": (int(ya + 1.3*pv), int(ya + 5.6*pv)), "t": (int(yj - 5.6*pv), int(yj - 1.3*pv))}
    rails = {}
    for side, (yl, yh) in bands.items():
        sub = gray[max(0, yl):yh, x0:x1]
        th = cv2.threshold(sub, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
        n, lbl, stats, cent = cv2.connectedComponentsWithStats(th)
        blobs = []
        for i in range(1, n):
            a, w, h = stats[i, cv2.CC_STAT_AREA], stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]
            if a < 25 or a > 600: continue
            if not 0.55 <= w / max(h, 1) <= 1.8: continue   # non quadrato -> stampa/righe colorate
            if a / (w * h) < 0.45: continue
            blobs.append((cent[i][0] + x0, cent[i][1] + max(0, yl)))
        if len(blobs) < 20: continue
        # due linee = i due centri y con più consenso (i blob spuri non hanno ~50 compagni)
        ys = np.array([b[1] for b in blobs])
        def consenso(cand):
            best = None
            for y in cand:
                k = int((np.abs(cand - y) < 10).sum())
                if best is None or k > best[0]: best = (k, y)
            return best[1]
        c1 = consenso(ys)
        rest = ys[np.abs(ys - c1) >= 12]
        if len(rest) < 20: continue
        c2 = consenso(rest)
        hic, loc = max(c1, c2), min(c1, c2)
        hi = sorted((b for b in blobs if abs(b[1] - hic) < 12), key=lambda b: b[0])  # linea più in basso
        lo = sorted((b for b in blobs if abs(b[1] - loc) < 12), key=lambda b: b[0])
        # basso: esterna (y max) = '+', interna = '-'. alto: interna (y max) = '+', esterna = '-'.
        pair = {"+b": hi, "-b": lo} if side == "b" else {"+t": hi, "-t": lo}
        ref_u = 0 if side == "b" else 10                  # colonna: dal foro più vicino di riga a / j
        refx = [(x, v + 1) for (u, v), (x, y) in pos.items() if u == ref_u]
        def col_of(x):
            return min(refx, key=lambda t: abs(t[0] - x))[1]
        for name, line in pair.items():
            rails[name] = [[col_of(x), float(x), float(y)] for x, y in snap_rail(line)]
    return rails

def build_model(img, win=18, hsm=10.0, verbose=True):
    """Costruisce la mappa RBF ancorata ai marker gialli su questa foto. Ritorna
    (model, markers). Non salva niente. Usato sia da main() sia dal differenziale
    (una mappa per foto).

    win=18: la guida iniziale (omografia sui 4 angoli) sbaglia fino a 13-15 px sul
    blocco alto quando la board non è centrata nel fotogramma (distorsione +
    parallasse dei marker, che sporgono dal piano). Con win=14 la soglia 0.8*14=11.2 px
    non agganciava niente e il fit estrapolava righe fuori di 1-2 fori. 0.8*18=14.4 px
    resta sotto il mezzo passo (15 px): non può agganciare il foro sbagliato.
    Regressione su 35 scatti 0: 2 KO->OK, gli altri invariati."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    corners, middles = detect_yellow_markers(img)
    guide = og.homography(markers_corners(corners))
    deg_uv = [2, 4]

    def fit(uv, xy, d):
        A = np.array([og.monomials(u, v, d) for u, v in uv])
        X = np.array([p[0] for p in xy]); Y = np.array([p[1] for p in xy])
        cx = np.linalg.lstsq(A, X, rcond=None)[0]; cy = np.linalg.lstsq(A, Y, rcond=None)[0]
        return cx, cy, np.sqrt((A@cx - X)**2 + (A@cy - Y)**2)

    def fit_block(uv, xy, d):
        uv, xy = np.array(uv, float), np.array(xy, float)
        for _ in range(3):
            cx, cy, res = fit(uv, xy, d)
            keep = res < max(3.0, 2.5*np.median(res))
            if keep.all(): break
            uv, xy = uv[keep], xy[keep]
        return {"cx": cx.tolist(), "cy": cy.tolist()}

    def detect(guide, w):
        uv, xy = [], []
        for r in og.ROWS:
            for c in range(1, og.NCOLS+1):
                u, v = og.ideal(r, c)
                rx, ry, ok = refine(gray, *og.project(guide, u, v), w)
                if ok: uv.append((u, v)); xy.append((rx, ry))
        return uv, xy

    def build_guide(uv, xy):
        muv = [(u, v) for u, v, x, y in corners] * 5
        mxy = [(x, y) for u, v, x, y in corners] * 5
        uv, xy = list(uv) + muv, list(xy) + mxy
        bot = [(p, q) for p, q in zip(uv, xy) if p[0] <= 4]
        top = [(p, q) for p, q in zip(uv, xy) if p[0] >= 6]
        return {"type": "polyblock", "degree": deg_uv,
                "bottom": fit_block([b[0] for b in bot], [b[1] for b in bot], deg_uv),
                "top": fit_block([t[0] for t in top], [t[1] for t in top], deg_uv)}

    for i, w in enumerate([win, max(9, win-5), max(9, win-5)]):
        uv, xy = detect(guide, w)
        if verbose: print(f"  pass {i+1} (win {w}): {len(uv)}/{10*og.NCOLS} fori")
        guide = build_guide(uv, xy)

    # RECUPERO dai BORDI dei gap: dove la mappa globale deriva (collina centrale) la
    # predizione cade tra due fori e refine rifiuta. Interpolare a metà gap è ambiguo
    # (mezzo passo -> foro sbagliato): estrapolo di UN passo dal vicino agganciato, con
    # passo locale stimato sul posto. Iterativo: il gap si chiude dai bordi verso dentro.
    det = {tuple(p): tuple(q) for p, q in zip(uv, xy)}
    mset = {(u, v) for u, v, x, y in corners}
    hull = cv2.convexHull(np.array([(x, y) for u, v, x, y in corners], np.float32))
    def on_board(x, y):                                 # il recupero non deve uscire dalla board
        return cv2.pointPolygonTest(hull, (float(x), float(y)), True) > -40
    def prune(det):
        """Butta i punti incoerenti coi vicini di riga (leave-one-out LOCALE: la collina
        globale non c'entra, localmente la riga è quasi lineare). Basta UNA predizione
        coerente per salvare il punto; si pota il peggiore alla volta."""
        def P(u, v): return np.array(det[(u, v)], float) if (u, v) in det else None
        for r in og.ROWS:
            u = og.ROW_U[r]
            while True:
                worst = None
                for v in [v for uu, v in det if uu == u]:
                    preds = []
                    l1, l2, r1, r2 = P(u, v-1), P(u, v-2), P(u, v+1), P(u, v+2)
                    if l1 is not None and r1 is not None: preds.append((l1 + r1) / 2)
                    if l1 is not None and l2 is not None: preds.append(2*l1 - l2)
                    if r1 is not None and r2 is not None: preds.append(2*r1 - r2)
                    if not preds: continue
                    e = min(float(np.hypot(*(P(u, v) - p))) for p in preds)
                    if e > 9 and (worst is None or e > worst[0]): worst = (e, v)
                if worst is None: break
                det.pop((u, worst[1]))
    prune(det)
    def relabel_rows(det):
        """Ri-etichetta le righe per scala rigida (analogo verticale di relabel). La
        board ha 10 righe a passo fisso + canale ~3 passi, ancorate alle righe marker
        a e j. Se una riga fisica non viene vista (luce), il poly compensa e tutto il
        blocco slitta di una riga (b saltata -> c/d/e su di uno, 'è finita nel
        canale). Qui ogni punto prende l'etichetta della quota attesa più vicina;
        oltre mezzo passo dalla scala = fantasma, fuori."""
        cd = {(u, v): (x, y) for u, v, x, y in corners}
        a1, a63 = cd[og.ideal("a", 1)], cd[og.ideal("a", og.NCOLS)]
        j1, j63 = cd[og.ideal("j", 1)], cd[og.ideal("j", og.NCOLS)]
        def ya(x):
            t = (x - a1[0]) / max(a63[0] - a1[0], 1e-6)
            return a1[1] + t * (a63[1] - a1[1])
        def yj(x):
            t = (x - j1[0]) / max(j63[0] - j1[0], 1e-6)
            return j1[1] + t * (j63[1] - j1[1])
        OFF = {u: (u if u <= 4 else u + 1) for u in og.ROW_U.values()}  # e->f = 3 passi (canale)
        out, moved = {}, 0
        for (u, v), (x, y) in det.items():
            pv = (ya(x) - yj(x)) / 11.0
            best = min(OFF, key=lambda uu: abs((ya(x) - OFF[uu] * pv) - y))
            err = abs((ya(x) - OFF[best] * pv) - y)
            if err > 0.5 * pv: continue
            if best != u: moved += 1
            key = (best, v)
            prev_err = abs((ya(out[key][0]) - OFF[best] * pv) - out[key][1]) if key in out else 1e9
            if err < prev_err: out[key] = (x, y)
        if verbose and moved: print(f"  relabel righe: {moved} punti spostati sulla scala rigida")
        return out
    fixed_rows = relabel_rows(det)
    det.clear(); det.update(fixed_rows)
    def relabel(det):
        """Ri-etichetta per CONTEGGIO FISICO. Il guide polinomiale può convergere con
        un'etichetta inserita + una saltata (compensate: tutto il mezzo slitta di 1).
        Qui: righe a/j = camminata foro-per-foro tra i marker d'angolo (passo misurato,
        EMA); riferimento x per colonna = poly sui due; ogni det prende la colonna del
        riferimento più vicino. Inserzioni/salti impossibili per costruzione."""
        cornerd = {(u, v): (x, y) for u, v, x, y in corners}
        pairs = []                                    # (colonna 1-based, x) dalle righe ancorate
        for u in (0, 10):
            xs = sorted(x for (uu, v), (x, y) in det.items() if uu == u)
            if len(xs) < 20: continue
            x1, x63 = cornerd[(u, 0)][0], cornerd[(u, og.NCOLS - 1)][0]
            pl = (x63 - x1) / (og.NCOLS - 1)
            merged = []
            for x in xs:
                if not merged or x - merged[-1] >= 0.55 * pl: merged.append(x)
            # etichetta del PRIMO det: distanza dal marker sx (lo sticker copre col 1 e
            # può essere storto/piegato di mezzo passo: mai usarlo come punto esatto)
            c = 1 + round((merged[0] - x1) / pl)
            got = {c: merged[0]}
            xprev, pcur = merged[0], pl
            for x in merged[1:]:
                dc = max(1, round((x - xprev) / pcur))
                c += dc
                got[c] = x
                pcur = 0.7 * pcur + 0.3 * (x - xprev) / dc
                xprev = x
            r_pred = og.NCOLS - round((x63 - xprev) / pcur)   # verifica indipendente sul marker dx
            if c != r_pred or c > og.NCOLS:
                if verbose: print(f"  relabel: riga u={u} incoerente (walk={c}, dal marker dx={r_pred}), scartata")
                continue
            pairs += [(k, x) for k, x in got.items()]
        if not pairs: return dict(det)                # MAI lo stesso oggetto (il chiamante fa clear)
        co = np.polyfit([k for k, x in pairs], [x for k, x in pairs], 3)
        colx = {k: float(np.polyval(co, k)) for k in range(1, og.NCOLS + 1)}
        pitch = (colx[og.NCOLS] - colx[1]) / (og.NCOLS - 1)
        out = {}
        for (u, v), (x, y) in det.items():
            k = min(colx, key=lambda kk: abs(colx[kk] - x))
            if abs(colx[k] - x) > 0.45 * pitch: continue   # fuori griglia: fantasma
            key = (u, k - 1)
            if key not in out or abs(colx[k] - out[key][0]) > abs(colx[k] - x):
                out[key] = (x, y)
        return out
    def recover():
        for _ in range(40):
            added = 0
            for r in og.ROWS:
                u = og.ROW_U[r]
                for c in range(1, og.NCOLS+1):
                    key = (u, c-1)
                    if key in det or key in mset: continue
                    preds = []
                    for d in (-1, +1):                  # ancora adiacente per lato
                        a = (u, c-1+d)
                        if a not in det: continue
                        s = next(((u, c-1+d*k) for k in (2, 3, 4) if (u, c-1+d*k) in det), None)
                        if s is None: continue
                        pa, ps = np.array(det[a], float), np.array(det[s], float)
                        step = (pa - ps) / (a[1] - s[1])    # vettore per +1 colonna
                        preds.append(pa + step * ((c-1) - a[1]))
                    if not preds: continue
                    pr = np.mean(preds, axis=0)
                    if not on_board(*pr): continue
                    rx, ry, ok = refine(gray, pr[0], pr[1], 10)
                    if ok: det[key] = (rx, ry); added += 1
            if not added: break
    # prima recupera (det denso, salti ciechi corti), POI ri-etichetta contando,
    # poi recupera di nuovo con le etichette giuste.
    recover()
    prune(det)
    fixed = relabel(det)
    det.clear(); det.update(fixed)
    recover()
    prune(det)
    def recover_vert():
        """Riga fisica sfuggita alla detection: dopo il relabel
        righe resta VUOTA e il recupero orizzontale non può seminarla. Qui si predice
        ogni suo foro dal punto medio dei vicini VERTICALI dello stesso blocco e si
        raffina sul posto."""
        added = 0
        for u in og.ROW_U.values():
            for c in range(1, og.NCOLS + 1):
                key = (u, c - 1)
                if key in det or key in mset: continue
                up, dn = (u + 1, c - 1), (u - 1, c - 1)
                same_blk = lambda a, b: (a[0] <= 4) == (b[0] <= 4)
                if up not in det or dn not in det: continue
                if not (same_blk(up, key) and same_blk(dn, key)): continue
                pu, pd = np.array(det[up], float), np.array(det[dn], float)
                pr = (pu + pd) / 2
                rx, ry, ok = refine(gray, pr[0], pr[1], 10)
                det[key] = (rx, ry) if ok else (float(pr[0]), float(pr[1]))
                added += 1
        if verbose and added: print(f"  recupero verticale: {added} fori")
    recover_vert()
    recover()
    # colonna dei marker INTERMEDI dedotta a mappa piena (det più vicino + passo locale):
    # nessuna assunzione su dove sono stati incollati.
    markers = list(corners)
    for u, x, y in middles:
        row = [(v, det[(u, v)]) for uu, v in det if uu == u]
        if not row: continue
        vn, pn = min(row, key=lambda t: (t[1][0]-x)**2 + (t[1][1]-y)**2)
        steps = [abs((pn[0]-q[0]) / (vn-v2)) for v2, q in row if 1 <= abs(v2-vn) <= 3]
        if not steps: continue
        vm = vn + round((x - pn[0]) / float(np.median(steps)))
        if 0 <= vm < og.NCOLS: markers.append((u, int(vm), x, y))
    mset = {(u, v) for u, v, x, y in markers}
    det = {k: p for k, p in det.items() if k not in mset}   # il foro sotto il marker è del marker
    if verbose: print(f"  recupero: {len(det)}/{10*og.NCOLS} fori (+{len(mset)} marker, mancanti {10*og.NCOLS-len(det)-len(mset)})")

    # i marker NON entrano come ancore: lo sticker non è centrato sul foro che copre
    # (visto a1 sbilenco di ~13px). I loro fori li interpola la RBF dai det vicini.
    ctrl = [(p, q, hsm) for p, q in det.items()]
    def block(pred):
        P = [p for p in ctrl if pred(p[0][0])]
        return {"uv": [list(p[0]) for p in P], "xy": [list(p[1]) for p in P], "smooth": [p[2] for p in P]}
    model = {"type": "rbf", "bottom": block(lambda u: u <= 4), "top": block(lambda u: u >= 6)}
    # tabella diretta: dove il centro è MISURATO si usa quello (zero interpolazione);
    # la RBF resta solo per i fori mancanti.
    model["table"] = {f"{u},{v}": [float(x), float(y)] for (u, v), (x, y) in det.items()}
    # fori mai rilevati ai bordi: la RBF estrapola e deriva fuori board; meglio
    # l'omografia dai 4 marker d'angolo, esatta proprio agli angoli.
    hg = og.homography(markers_corners(corners))
    for (u, v), (x, y) in og.positions(model).items():
        if (u, v) not in det and (u, v) not in mset and not on_board(x, y):
            model["table"][f"{u},{v}"] = [float(p) for p in og.project(hg, u, v)]
    model["rails"] = detect_rails(gray, og.positions(model))
    if verbose: print("  rail:", {k: len(v) for k, v in model["rails"].items()})
    return model, markers

def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    path = sys.argv[1]
    win = int(sys.argv[sys.argv.index("--win")+1]) if "--win" in sys.argv else 14
    img = cv2.imread(path)
    if img is None:
        sys.exit(f"Non leggo {path}")
    model, markers = build_model(img, win)
    json.dump(model, open(og.CALIB, "w"))
    print(f"salvato RBF ({len(markers)} marker) — {len(model['bottom']['uv'])} basso, {len(model['top']['uv'])} alto")

if __name__ == "__main__":
    main()
