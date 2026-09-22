"""radar_panels.py — disegna la finestra diagnostica del radar dal dizionario-risultato.
DISEGNA E NON CALCOLA: legge solo da result (v. radar_analyze.py).

Disposizione:
  riga 1  = BOARD a striscia orizzontale piena, ritagliata (mappa + heatmap legscore + pin)
  riga 2  = tre pannelli grandi affiancati:
              [pixel cambiati]  [NCC foro: prima/dopo/diff grigio/diff CALORE]  [geometria]
  riga 3  = barra (classe, colore, pin, soglie, note)

Tutte le scritte passano da _text(): contorno nero -> RISALTO su sfondo chiaro e scuro.
"""
import numpy as np
import cv2
import differential as df
import overlay_grid as og

FONT = cv2.FONT_HERSHEY_SIMPLEX
W_NCC = df.W_NCC        # 11: mezzo lato del patch NCC (23x23)
PANEL_H = 760           # altezza dei tre pannelli di riga 2


def _text(img, s, org, scale, color, thick=2):
    """Testo con contorno nero: risalta su qualsiasi sfondo (board chiara o pannello scuro)."""
    cv2.putText(img, s, org, FONT, scale, (0, 0, 0), thick + 3, cv2.LINE_AA)
    cv2.putText(img, s, org, FONT, scale, color, thick, cv2.LINE_AA)


def _heat(v, thr):
    """Colore BGR freddo->caldo per un legscore v rispetto alla soglia thr."""
    t = max(0.0, min(1.0, v / (2 * thr) if thr else 0))
    return (int(255 * (1 - t)), 0, int(255 * t))       # blu -> rosso


def _title(panel, text):
    """Titolo grande in cima a un pannello, su fascia scura per leggibilità."""
    cv2.rectangle(panel, (0, 0), (panel.shape[1], 38), (45, 45, 45), -1)
    _text(panel, text, (12, 28), 0.74, (255, 255, 255), 2)


def _board_strip(res, rec=None):
    """Riga 1: la board con i disegni, ritagliata al suo bounding box (via il nero).
    Gerarchia a strati: misure (cerchietti), golden atteso (quadrato bianco), pin
    promossi (verde)."""
    over = res["img"].copy()
    posx = {**res["pos"], **res["rpos"]}
    if rec and rec.get("atteso"):              # GOLDEN: dove il pezzo DEVE stare
        for nome in rec["atteso"].get("coords", []):
            try:
                q = df.parse_hole(nome)
            except Exception:
                continue
            if q in posx:
                x_, y_ = posx[q]
                cv2.rectangle(over, (x_ - 16, y_ - 16), (x_ + 16, y_ + 16),
                              (255, 255, 255), 3)
    for p in res["pos"].values():
        cv2.circle(over, p, 2, (0, 160, 0), -1)
    thr = res["cls_thr"][1] or df.QC_WEAK
    if res["blob_pts"] is not None:
        pp = res["blob_pts"].astype(int)
        over[pp[:, 1], pp[:, 0]] = (0, 220, 220)
    # Numeri al posto dei simboli: il valore NCC scritto su ogni foro della regione,
    # giallo se >= 0.10, grigio sotto. Mostra la misura, non la conclusione: è la
    # vista con cui un errore di riga si legge al volo. Disegnati dopo il blob,
    # altrimenti il giallo copre proprio i numeri della riga calda.
    for uv, s in res["legscore"].items():
        if uv not in posx:
            continue
        t = f"{s:.2f}".lstrip("0") or ".00"
        col = (60, 230, 255) if s >= 0.10 else (185, 185, 185)
        _text(over, t, (posx[uv][0] - 14, posx[uv][1] - 9), 0.42, col, 1)
    for uv in res["pins"]:
        if uv in posx:
            cv2.circle(over, posx[uv], 13, (0, 255, 0), 3)
            _text(over, df.name(*uv), (posx[uv][0] + 12, posx[uv][1] - 13), 0.85, (0, 255, 0), 2)
    # LE NOSTRE etichette (righe a-j, colonne) allineate alla mappa: così si leggono
    # gli STESSI nomi dei fori dei pannelli, senza affidarsi alla serigrafia della board
    for r_, u_ in og.ROW_U.items():
        if (u_, 0) in res["pos"]:
            x_, y_ = res["pos"][(u_, 0)]
            _text(over, r_, (x_ - 50, y_ + 9), 1.0, (255, 150, 0), 3)
    for uu in (0, 10):
        for c_ in [1] + list(range(5, og.NCOLS, 5)) + [og.NCOLS]:
            if (uu, c_ - 1) in res["pos"]:
                x_, y_ = res["pos"][(uu, c_ - 1)]
                _text(over, str(c_), (x_ - 10, y_ + (38 if uu == 0 else -22)), 0.62, (255, 150, 0), 2)
    # ritaglio al bounding box dei fori (via i grandi margini neri), con un po' di pad
    xs = [p[0] for p in posx.values()]; ys = [p[1] for p in posx.values()]
    pad = 60
    x0 = max(0, min(xs) - pad); x1 = min(over.shape[1], max(xs) + pad)
    y0 = max(0, min(ys) - pad); y1 = min(over.shape[0], max(ys) + pad)
    return over[y0:y1, x0:x1]


def _pixels_panel(res, w, h):
    """[pixel cambiati] il ritaglio REALE del pezzo con i pixel del blob evidenziati:
    mostra QUALI pixel il sistema ha visto cambiare (più leggibile della sola sagoma)."""
    panel = np.zeros((h, w, 3), np.uint8)
    if res["blob_pts"] is not None and res["blob_box"] is not None:
        x0, y0, x1, y1 = res["blob_box"]
        pad = 40
        cx0 = max(0, x0 - pad); cy0 = max(0, y0 - pad)
        crop = res["img"][cy0:min(res["img"].shape[0], y1 + pad),
                          cx0:min(res["img"].shape[1], x1 + pad)].copy()
        pp = res["blob_pts"].astype(int)
        lx = pp[:, 0] - cx0; ly = pp[:, 1] - cy0
        ok = (lx >= 0) & (ly >= 0) & (lx < crop.shape[1]) & (ly < crop.shape[0])
        crop[ly[ok], lx[ok]] = (crop[ly[ok], lx[ok]] * 0.35
                                + np.array((0, 230, 230)) * 0.65).astype(np.uint8)
        s = min((w - 8) / max(crop.shape[1], 1), (h - 48) / max(crop.shape[0], 1))
        r = cv2.resize(crop, (int(crop.shape[1] * s), int(crop.shape[0] * s)))
        panel[42:42 + r.shape[0], 4:4 + r.shape[1]] = r
    _title(panel, "pixel cambiati")
    return panel


FILIFORMI = {"resistenza", "condensatore", "diodo", "fotoresistenza",
             "transistor", "jumper", "cavetto"}


def _sagoma_panel(res, rec, w, h):
    """[sagoma+spina]: per i filiformi il riquadro mostra la sagoma-diff con la
    spina e le X sui capi. Per le altre classi: ripiego sui pixel cambiati."""
    try:
        cls_att = (rec or {}).get("atteso", {}).get("cls")
        if cls_att not in FILIFORMI:
            raise ValueError
        import filo as fl
        import telemetria as tm
        m = fl.sagoma_diff(res)
        mk2 = cv2.dilate(m, np.ones((5, 5), np.uint8))
        c2 = fl.capi(mk2)
        vis = res["img"].copy()
        cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        cv2.drawContours(vis, cs, -1, (255, 255, 255), 1)
        sp = tm._spina(mk2, c2)
        if sp:
            cv2.polylines(vis, [np.array(sp, np.int32)], False, (255, 200, 0), 3)
        for q in c2:
            for s1, s2 in ((-1, -1), (1, -1)):
                cv2.line(vis, (q[0] - 9 * s1, q[1] - 9 * s2),
                         (q[0] + 9 * s1, q[1] + 9 * s2), (60, 230, 255), 3)
        ys, xs = np.nonzero(m)
        g = 60
        crop = vis[max(0, ys.min() - g):ys.max() + g, max(0, xs.min() - g):xs.max() + g]
        panel = np.zeros((h, w, 3), np.uint8)
        s = min((w - 8) / max(crop.shape[1], 1), (h - 48) / max(crop.shape[0], 1))
        r = cv2.resize(crop, (int(crop.shape[1] * s), int(crop.shape[0] * s)))
        panel[42:42 + r.shape[0], 4:4 + r.shape[1]] = r
        _title(panel, "sagoma + spina (X = capi)")
        return panel
    except Exception:
        return _pixels_panel(res, w, h)


def _ncc_tile(patch, w, h, title, heat=False):
    """Un riquadro NCC ingrandito (grigio o mappa di calore) con titolo grande."""
    tile = np.zeros((h, w, 3), np.uint8)
    p = np.clip(patch, 0, 255).astype(np.uint8)
    if heat:
        # normalizza sul proprio max per far RISALTARE la differenza, poi colormap JET
        mx = float(patch.max()) or 1.0
        norm = np.clip(patch / mx * 255, 0, 255).astype(np.uint8)
        img = cv2.applyColorMap(cv2.resize(norm, (w - 8, h - 42),
                                           interpolation=cv2.INTER_NEAREST), cv2.COLORMAP_JET)
    else:
        g = cv2.resize(p, (w - 8, h - 42), interpolation=cv2.INTER_NEAREST)
        img = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
    tile[38:38 + img.shape[0], 4:4 + img.shape[1]] = img
    cv2.rectangle(tile, (0, 0), (w, 34), (45, 45, 45), -1)
    _text(tile, title, (8, 25), 0.66, (255, 255, 255), 2)
    return tile


def _ncc_panel(res, sel_hole, w, h):
    """[NCC] 2x2: prima | dopo (sopra), diff grigio | diff CALORE (sotto), del foro selezionato."""
    panel = np.zeros((h, w, 3), np.uint8)
    posx = {**res["pos"], **res["rpos"]}
    if sel_hole is None or sel_hole not in posx:
        _title(panel, "NCC del foro")
        _text(panel, "seleziona un foro", (14, h // 2), 0.72, (170, 170, 170), 2)
        _text(panel, "(frecce <- ->  o click)", (14, h // 2 + 38), 0.66, (170, 170, 170), 2)
        return panel
    x, y = posx[sel_hole]
    g0 = cv2.cvtColor(res["prev"], cv2.COLOR_BGR2GRAY).astype(np.float32)
    g1 = cv2.cvtColor(res["img"], cv2.COLOR_BGR2GRAY).astype(np.float32)
    a = g0[y - W_NCC:y + W_NCC + 1, x - W_NCC:x + W_NCC + 1]
    b = g1[y - W_NCC:y + W_NCC + 1, x - W_NCC:x + W_NCC + 1]
    if not (a.size and b.size and a.shape == b.shape):
        _title(panel, "NCC del foro")
        _text(panel, "foro sul bordo", (14, h // 2), 0.72, (170, 170, 170), 2)
        return panel
    d = np.abs(a - b)
    tw, th = w // 2, (h - 78) // 2                     # 78 = spazio per le due righe di formula
    grid = np.zeros((th * 2, tw * 2, 3), np.uint8)
    grid[0:th, 0:tw] = _ncc_tile(a, tw, th, "prima")
    grid[0:th, tw:tw * 2] = _ncc_tile(b, tw, th, "dopo")
    grid[th:th * 2, 0:tw] = _ncc_tile(d, tw, th, "diff")
    grid[th:th * 2, tw:tw * 2] = _ncc_tile(d, tw, th, "diff CALORE", heat=True)
    panel[0:grid.shape[0], 0:grid.shape[1]] = grid
    s = res["legscore"].get(sel_hole, 0.0)
    cv2.rectangle(panel, (0, h - 74), (w, h), (30, 30, 30), -1)
    _text(panel, f"{df.name(*sel_hole)}   legscore = 1 - max NCC = {s:.3f}",
          (12, h - 42), 0.72, (0, 210, 255), 2)
    _text(panel, "NCC = correlazione struttura prima/dopo (patch 23px, ricerca +-4px)",
          (12, h - 14), 0.48, (170, 210, 230), 1)
    return panel


def _geomean(vals):
    """Media geometrica; un valore <=0 azzera tutto (regola pair_score del sistema)."""
    vals = [max(float(v), 0.0) for v in vals]
    if not vals or min(vals) <= 0.0:
        return 0.0
    return float(np.exp(np.mean(np.log(vals))))


def _colpairs(cand, legs):
    """Raggruppa i fori del candidato per COLONNA -> [(col, [fori], geomean(e,f)), ...].
    È la scomposizione reale del punteggio bottone: coppia (e,c)-(f,c)."""
    cols = {}
    for uv in cand:
        cols.setdefault(uv[1], []).append(uv)
    return [(v, hs, _geomean([legs.get(h, 0.0) for h in hs])) for v, hs in sorted(cols.items())]


def _formula2(kind):
    """Le due righe di formula (vero per costruzione dal ramo di fit_pins)."""
    if kind == "bottone-quad":
        return ("1) foro:  legscore = 1 - max NCC",
                "2) bottone: MIN dei 4 fori (vale il piedino piu' debole)")
    if kind == "2pin-line":
        return ("1) foro:  legscore = 1 - max NCC",
                "2) punteggio = somma dei legscore del supporto sulla riga")
    if kind == "opto-six":
        return ("1) foro:  legscore = 1 - max NCC",
                "2) opto: MIN dei 6 fori (vale il piedino piu' debole)")
    if kind == "rgb-quad":
        return ("1) foro:  legscore = 1 - max NCC", "2) punteggio = somma dei 4 legscore in fila")
    if kind in ("trimmer-tri", "transistor-tri"):
        return ("1) foro:  legscore = 1 - max NCC", "2) punteggio = somma dei 3 legscore")
    return ("1) foro:  legscore = 1 - max NCC", "2) punteggio = combinazione dei legscore")


def _trace_panel(res, w, h):
    """[geometria] classifica dei candidati da TRACE: le due formule, vincitore in verde
    con SCOMPOSIZIONE per colonna (bottone) o per foro (resto), distacco a barre."""
    panel = np.zeros((h, w, 3), np.uint8)
    _title(panel, "geometria: candidati")
    rows = sorted(res["trace"], key=lambda t: -t[2])[:7]
    if not rows:
        _text(panel, "classe non letta", (14, 100), 0.82, (150, 150, 150), 2)
        _text(panel, "(senza classe niente proposta pin)", (14, 138), 0.6, (150, 150, 150), 2)
        return panel
    kind = rows[0][0]
    f1, f2 = _formula2(kind)
    _text(panel, f1, (12, 62), 0.58, (0, 215, 255), 2)
    _text(panel, f2, (12, 90), 0.5, (0, 215, 255), 1)
    best = rows[0][2] or 1.0
    y = 138
    for i, (k_, cand, sc) in enumerate(rows):
        col = (0, 255, 0) if i == 0 else (215, 215, 215)
        label = ",".join(df.name(*uv) for uv in cand)
        _text(panel, f"{sc:.3f}", (14, y), 0.92, col, 2)
        _text(panel, label, (140, y), 0.64, col, 2)
        bw = int((w - 28) * min(1.0, sc / best))
        cv2.rectangle(panel, (14, y + 12), (14 + bw, y + 24),
                      (0, 180, 0) if i == 0 else (90, 90, 90), -1)
        if i == 0:                                     # scomposizione del vincitore
            legs = res["legscore"]
            if kind in ("bottone-quad", "opto-six"):   # entrambi: MIN sulle coppie di colonna
                parts = [f"col{v + 1}: geom({df.name(*hs[0])} {legs.get(hs[0], 0):.2f},"
                         f" {df.name(*hs[1])} {legs.get(hs[1], 0):.2f}) = {gm:.2f}"
                         for v, hs, gm in _colpairs(cand, legs) if len(hs) == 2]
                for j, p in enumerate(parts):
                    _text(panel, p, (20, y + 52 + j * 30), 0.52, (0, 225, 130), 1)
                y += 52 + 30 * len(parts) + 18
            else:
                bd = "  ".join(f"{df.name(*uv)}:{legs.get(uv, 0.0):.2f}" for uv in cand)
                _text(panel, bd, (20, y + 52), 0.52, (0, 225, 130), 1)
                y += 90
        else:
            y += 56
    return panel


def render_riepilogo(cid, nome, records):
    """Tabella di fine commessa (modalità operativa): una riga per step, verdetto
    colorato (verde OK / giallo DEBOLE-NON VERIFICABILE / rosso KO)."""
    h = 84 + 34 * max(1, len(records)) + 16
    img = np.zeros((h, 920, 3), np.uint8)
    _text(img, f"RIEPILOGO {cid} - {nome}", (14, 40), 0.85, (255, 255, 255), 2)
    _text(img, "referto certificato a posteriori: run_qc.py (comando nel terminale)",
          (14, 66), 0.5, (150, 150, 150), 1)
    y = 104
    for r in records:
        vd = r.get("verdetto", "?")
        col = ((0, 0, 255) if vd.startswith("KO") else
               (0, 200, 255) if vd in ("DEBOLE", "NON VERIFICABILE", "FUORI GOLDEN")
               else (0, 220, 0))
        el = r["atteso"]["el"] if r.get("atteso") else "(fuori golden)"
        _text(img, f"step {r.get('step') or '-'}: {el}", (14, y), 0.6, (230, 230, 230), 1)
        _text(img, vd, (640, y), 0.6, col, 2)
        y += 34
    return img


def _fit_h(img, h):
    s = h / img.shape[0]
    return cv2.resize(img, (max(1, int(img.shape[1] * s)), h))


def _tile_box(img, w, h, title):
    """Letterbox di una vista dentro (w,h) con fascia titolo."""
    box = np.zeros((h, w, 3), np.uint8)
    s = min((w - 8) / max(img.shape[1], 1), (h - 46) / max(img.shape[0], 1))
    r = cv2.resize(img, (max(1, int(img.shape[1] * s)), max(1, int(img.shape[0] * s))))
    box[42:42 + r.shape[0], 4:4 + r.shape[1]] = r
    cv2.rectangle(box, (0, 0), (w, 36), (45, 45, 45), -1)
    _text(box, title, (10, 26), 0.66, (255, 255, 255), 2)
    return box


def render(res, sel_hole=None, oper_txt=None, rec=None):
    """Pannello diagnostico: fascia VERDETTO + le stesse viste dell'Excel — blob
    (diff | foto pulita), NCC-D — più NCC-A con i valori sui fori (vive solo qui,
    per correggere le X)."""
    import telemetria as tm
    posx = {**res["pos"], **res["rpos"]}
    thr, weak = res.get("cls_thr") or (df.QC_WEAK, df.QC_WEAK)
    att_uv = []
    for hn in ((rec or {}).get("atteso", {}) or {}).get("coords", []):
        try:
            att_uv.append(df.parse_hole(hn))
        except Exception:
            pass
    tiles = []
    try:                                             # blob: solo il diff (la prova
        x0, y0, x1, y1 = [int(v) for v in res["blob_box"]]  # fotografica sta nella vista D)
        d_ = cv2.convertScaleAbs(cv2.absdiff(res["img"], res["prev"]), alpha=2.2)
        g = 80
        H, W = d_.shape[:2]
        zy0, zy1 = max(0, y0 - g), min(H, y1 + g)
        zx0, zx1 = max(0, x0 - g), min(W, x1 + g)
        tiles.append(("blob (diff)", _fit_h(d_[zy0:zy1, zx0:zx1], 430)))
    except Exception:
        pass
    for nome, fn in (("NCC-D", tm.Telemetria._vista_d),
                     ("NCC-A: valori sui fori", tm.Telemetria._vista_a)):
        try:
            im = fn(None, res, rec or {}, att_uv, posx, thr, weak, df)
            if im is not None:
                tiles.append((nome, im))
        except Exception:
            pass
    if not tiles:
        tiles.append(("foto", res["img"]))
    TW, TH = 640, 560
    row = np.hstack([_tile_box(im, TW, TH, t) for t, im in tiles])
    W = row.shape[1]
    if rec and rec.get("verdetto"):            # fascia verdetto
        vd = rec["verdetto"]
        col = ((0, 0, 255) if vd.startswith("KO") else
               (0, 200, 255) if vd in ("OK?", "DEBOLE", "NON VERIFICABILE", "FUORI GOLDEN")
               else (0, 220, 0))
        head = np.zeros((64, W, 3), np.uint8)
        cv2.rectangle(head, (0, 0), (14, 64), col, -1)
        _text(head, vd, (30, 44), 1.25, col, 3)
        att = rec.get("atteso") or {}
        _text(head, f"step {rec.get('step') or '-'}: {att.get('el', '')}"
                    f"  [{att.get('holes', '')}]".replace("→", "->"),
              (500, 42), 0.72, (235, 235, 235), 2)
    else:
        head = np.zeros((64, W, 3), np.uint8)
        _text(head, f"foto {res['k']}", (30, 44), 0.9, (235, 235, 235), 2)
    bar = np.zeros((44, W, 3), np.uint8)
    txt = (f"foto {res['k']}   classe={res['cls'] or '?'}   "
           f"pin={','.join(df.name(*uv) for uv in res['pins']) or '-'}   "
           f"soglie={res['cls_thr']}   {res['note']}")
    _text(bar, txt[:170], (12, 30), 0.6, (220, 220, 220), 1)
    return np.vstack([head, row, bar])
