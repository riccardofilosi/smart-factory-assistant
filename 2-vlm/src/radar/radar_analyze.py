"""radar_analyze.py — dallo scatto validato N (già su disco come N.jpeg nella cwd)
al dizionario-risultato del radar. Usa i primitivi di differential.py in sola
lettura, più il gancio TRACE. Non disegna nulla (v. radar_panels.py)."""
import os
import numpy as np
import cv2
import differential as df
import cosa as co

BLOB_MIN_AREA = 400      # px del blob sotto cui è rumore / scena mossa
BLOB_MIN_FILL = 0.05     # area/bbox minima: sotto è un blob sparso (board urtata)

# ---- guardie a monte ---------------------------------------------------------
# Due guasti diversi, due controlli diversi. Un frame che ne fallisce anche uno solo
# non si analizza e, soprattutto, non diventa il riferimento del confronto successivo:
# un frame rotto preso come riferimento compromette tutti quelli che seguono.
ALLIN_MIN = 30.0         # contrasto foro/plastica sotto cui la mappa non è più allineata.
                         # Misurato su 323 frame: allineati 125-142, slittati -1.3..6.1,
                         # nessun campione fra 10 e 60. La soglia sta nel vuoto.
DIST_AREA = 80000        # px del blob sopra cui c'è un corpo estraneo (la mano dell'operatore).
                         # Misurato su 232 frame analizzati: componenti reali mediana ~8.000,
                         # massimo legittimo 66.176; mano più piccola 98.478. 80.000 sta nel
                         # vuoto: +21% sul massimo legittimo, -19% sulla mano.
DIST_FORI = 130          # fori cambiati su 630 sopra cui il diff non è attribuibile a un pezzo.
                         # Il componente più largo misurato (jumper lungo) ne cambia 77.


def allineamento(img, pos):
    """Contrasto fra la plastica (a metà strada fra due fori) e il centro del foro.
    Il foro è un quadratino nero: se la mappa è allineata i centri cadono nel nero e il
    contrasto è alto; se il frame è slittato i centri finiscono sulla plastica e il
    contrasto crolla. Controllo su un solo frame: non serve il precedente."""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    H, W = g.shape
    dentro, fra = [], []
    for (u, v), (x, y) in pos.items():
        if not (6 <= x < W - 6 and 6 <= y < H - 6):
            continue
        dentro.append(g[y-3:y+4, x-3:x+4].mean())
        q = pos.get((u, v + 1))
        if q is None:
            continue
        mx, my = (x + q[0]) // 2, (y + q[1]) // 2
        if 6 <= mx < W - 6 and 6 <= my < H - 6:
            fra.append(g[my-3:my+4, mx-3:mx+4].mean())
    if not dentro or not fra:
        return None
    return float(np.median(fra) - np.median(dentro))

class Session:
    """Tiene vivo il closure frame() per tutta la sessione. La cwd deve essere la
    cartella-run (dove stanno 0.jpeg, 1.jpeg, ...). frame(0) costruisce la mappa."""
    def __init__(self):
        self.frame = df.make_frame()
        img0, _, pos0, rpos0, _ = self.frame(0)
        self.pos0, self.rpos0 = pos0, rpos0
        self.pitch = abs(pos0[(0, 1)][0] - pos0[(0, 0)][0]) or 29.0
        self.registry = []          # componenti rilevati nella sessione, in ordine.
                                    # Serve al riconoscitore contestuale (cosa c'è già
                                    # sulla board).

    def analyze(self, k, novlm=False, force_cls=None, prev_k=None):
        # prev_k: diff contro un frame precedente arbitrario invece di k-1. Serve alla
        # cattura a intervallo fisso: il consumatore confronta con l'ultimo frame
        # accettato, non con il precedente (spesso scartato: mano nel campo).
        note = []
        cur, _, pos, rpos, A = self.frame(k)
        pk = k - 1 if prev_k is None else prev_k
        prev = cv2.warpAffine(self.frame(pk)[0], A, (cur.shape[1], cur.shape[0]))
        posx = {**pos, **rpos}
        pitch = abs(pos[(0, 1)][0] - pos[(0, 0)][0]) or 29.0
        g0f = cv2.cvtColor(prev, cv2.COLOR_BGR2GRAY).astype(np.float32)
        g1f = cv2.cvtColor(cur, cv2.COLOR_BGR2GRAY).astype(np.float32)

        blob_pts = blob_box = cls = None
        color = ""
        region, legs, pins, trace = [], {}, [], []
        cls_thr = df.cls_thr(None)
        frame_ko = None

        # GUARDIA 1 — allineamento: proprietà del solo frame k, prima di qualunque diff
        allin = allineamento(cur, pos)
        if allin is not None and allin < ALLIN_MIN:
            note.append(f"FRAME SCARTATO: mappa non allineata (contrasto {allin:.0f} < {ALLIN_MIN:.0f})")
            return {"k": k, "img": cur, "prev": prev, "pos": pos, "rpos": rpos,
                    "pitch": pitch, "blob_pts": None, "blob_box": None,
                    "cls": None, "color": "", "region": [], "legscore": {},
                    "pins": [], "trace": [], "cls_thr": cls_thr, "allineamento": allin,
                    "frame_ko": "allineamento", "caldi": None,
                    "registry": list(self.registry), "note": " | ".join(note)}

        bl = df.find_blobs(cur, prev, df.board_mask(posx, cur.shape, grow=140))

        # GUARDIA 2 — disturbo: il diff non è attribuibile all'inserimento di un pezzo
        caldi = None
        if bl:
            area0 = len(bl[0][0])
            caldi = sum(1 for uv in pos if df.legscore(g0f, g1f, posx, *uv) >= 0.05)
            if area0 >= DIST_AREA or caldi >= DIST_FORI:
                note.append(f"FRAME SCARTATO: corpo estraneo o board mossa "
                            f"(blob {area0} px, {caldi}/{len(pos)} fori cambiati)")
                return {"k": k, "img": cur, "prev": prev, "pos": pos, "rpos": rpos,
                        "pitch": pitch, "blob_pts": None, "blob_box": None,
                        "cls": None, "color": "", "region": [], "legscore": {},
                        "pins": [], "trace": [], "cls_thr": cls_thr, "allineamento": allin,
                        "frame_ko": "disturbo", "caldi": caldi,
                        "registry": list(self.registry), "note": " | ".join(note)}

        if not bl:
            note.append("nessuna novita' (nessun blob)")
        else:
            # Selezione del candidato componente. Con registrazione degradata il diff
            # produce blob che non sono componenti (il nastro della rail, un blob
            # degenere che copre mezza board) e il ritaglio mandato al VLM non è un componente.
            # Un blob è componente-like se (a) il riquadro non copre >=25% della board,
            # (b) non è un nastro (rapporto >=15 con lato corto <=2 passi), (c) contiene
            # almeno un foro con segnale acceso. Se nessun blob passa, il sistema si
            # dichiara cieco sulla classe: perde il COSA, non il DOVE (i fori attesi si
            # misurano comunque).
            xs = [p[0] for p in posx.values()]; ys = [p[1] for p in posx.values()]
            area_board = max(1, (max(xs) - min(xs)) * (max(ys) - min(ys)))

            def _componente(box):
                w = max(1, box[2] - box[0]); h = max(1, box[3] - box[1])
                if w * h >= 0.25 * area_board:
                    return False                                   # degenere
                if ((w / float(h) >= 15 and h <= 2 * pitch)
                        or (h / float(w) >= 15 and w <= 2 * pitch)):
                    return False                                   # nastro
                return any(df.legscore(g0f, g1f, posx, *uv) >= 0.05
                           for uv in df.region_holes(posx, box, pad=0))

            scelto = next((b for b in bl if _componente(b[1])), None)
            blob_cieco = scelto is None
            if blob_cieco:
                note.append("nessun blob componente-like (nastri/degeneri/freddi): "
                            "radar cieco su classe")
            blob_pts, blob_box = (bl[0] if blob_cieco else scelto)
            area = len(blob_pts)
            bw = max(1, blob_box[2] - blob_box[0]); bh = max(1, blob_box[3] - blob_box[1])
            fill = area / float(bw * bh)
            if area < BLOB_MIN_AREA or fill < BLOB_MIN_FILL:
                note.append(f"SCENA MOSSA (area={area}, fill={fill:.2f}) - niente VLM")
                blob_pts = blob_box = None
            else:
                region = df.region_holes(posx, blob_box, pad=80)
                legs = {uv: df.legscore(g0f, g1f, posx, *uv) for uv in region}
                color = df.blob_color(cur, blob_pts)
                if force_cls:
                    cls = force_cls                        # classe imposta (test offline, salta il VLM)
                    note.append(f"classe FORZATA={force_cls}")
                elif not novlm and not blob_cieco:
                    try:
                        # Riconoscitore contestuale: riceve il registro (cosa c'è già) e
                        # restituisce cosa è stato aggiunto. Classe dal VLM, colore da
                        # OpenCV. Riquadro pulito -> domanda diretta; riquadro con dentro
                        # un pezzo già registrato -> elenco e sottrazione nel codice,
                        # perché la domanda a una parola sceglie il pezzo più vistoso
                        # (misurato: 5 letture sbagliate su 47 riquadri ambigui).
                        cls, _ = co.arrivato(cur, [blob_box], self.registry, posx)
                    except Exception as e:
                        note.append(f"VLM ko: {e}")
                if cls is None:
                    note.append("classe non letta (radar cieco su classe)")
                else:
                    cls_thr = df.cls_thr(cls)
                    # Finestra dei fori candidati attorno al blob. Per le classi a campata
                    # il blob è spesso il solo corpo: la gamba lontana cade fuori da una
                    # finestra di 40 px (1,4 passi) e non è nemmeno candidabile.
                    # - opto: scavalca il gap centrale con ~3 passi fra riga E e riga G/D.
                    # - transistor: la terna si stende su 4 colonne (misurato: 3 fori
                    #   golden su 24 irraggiungibili a 40 px, 24/24 a 4,5 passi).
                    # - resistenza & co.: la gamba sottile si disconnette dal blob (diff
                    #   debole sul metallo) e il bbox la taglia.
                    pad_holes = (int(4.5 * pitch)
                                 if df.PINS.get(cls) == 6
                                 or cls in ("transistor", "resistenza", "condensatore",
                                            "diodo", "fotoresistenza") else 40)
                    holes = df.region_holes(posx, blob_box, pad=pad_holes)
                    df.TRACE = trace
                    try:
                        # novlm=True (classe a mano) = niente VLM: img_cur=None spegne anche
                        # il VLM interno di fit_pins (vlm_pick_hole), pin risolti per sola
                        # geometria. img_col/img_prev: canale colore sempre attivo, anche
                        # senza VLM (sagoma filo, delta saturazione, cupola led).
                        pins = df.fit_pins(g0f, g1f, posx, holes, cls, blob_pts,
                                           img_cur=(None if novlm else cur),
                                           exclude=None, rows6=None,
                                           img_col=cur, img_prev=prev)
                    finally:
                        df.TRACE = None

        if cls is not None:                                # componente identificato -> registro
            self.registry.append({"k": k, "cls": cls, "col": color,
                                  "pins": [df.name(*u) for u in pins]})

        return {"k": k, "img": cur, "prev": prev, "pos": pos, "rpos": rpos,
                "pitch": pitch, "blob_pts": blob_pts, "blob_box": blob_box,
                "cls": cls, "color": color, "region": region, "legscore": legs,
                "pins": pins, "trace": trace, "cls_thr": cls_thr,
                "allineamento": allin, "frame_ko": frame_ko, "caldi": caldi,
                "registry": list(self.registry), "note": " | ".join(note)}
