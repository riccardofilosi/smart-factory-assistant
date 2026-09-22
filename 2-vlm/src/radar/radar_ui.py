"""radar_ui.py — widget cv2 per la finestra unica.

  console  : say() scrive sia sul terminale sia nel pannello console in finestra
  input    : read_line() = riga digitata nella finestra
  menu     : menu() a tasti singoli, in finestra
  wizard GT: gt_wizard() a fine run — pannello radar per step + caselle coordinate
"""
import collections
import json
import os
import re

import cv2
import numpy as np

CONS = collections.deque(maxlen=300)     # console di sessione (righe)
FONT = cv2.FONT_HERSHEY_SIMPLEX
# tasti freccia (waitKeyEx): Windows / Linux-Qt
K_LEFT, K_RIGHT = (2424832, 65361), (2555904, 65363)
K_UP, K_DOWN = (2490368, 65362), (2621440, 65364)


def say(msg=""):
    """print + eco nel pannello console della finestra (spezza i multiline)."""
    print(msg, flush=True)
    for r in str(msg).split("\n"):
        CONS.append(r)


def console_pane(w, h, input_line=None, prompt=None):
    """Pannello console: ultime righe di CONS + riga di input attiva (se c'è)."""
    pane = np.full((h, w, 3), 18, np.uint8)
    cv2.line(pane, (0, 1), (w, 1), (90, 90, 90), 1)
    step = 21
    nrows = max(1, (h - 30) // step - (1 if input_line is not None else 0))
    y = 22
    for r in list(CONS)[-nrows:]:
        cv2.putText(pane, r[:130], (10, y), FONT, 0.42, (200, 200, 200), 1)
        y += step
    if input_line is not None:
        p = (prompt or ">>") + " "
        cv2.putText(pane, p + input_line + "_", (10, min(y, h - 10)), FONT, 0.5, (0, 255, 255), 1)
    return pane


def read_line(win, render, prompt=">>"):
    """Riga digitata NELLA finestra: `render(input_line)->frame` ridisegna a ogni tasto.
    INVIO conferma, ESC svuota e conferma vuoto. Ritorna la stringa (strip)."""
    buf = ""
    while True:
        cv2.imshow(win, render(buf))
        k = cv2.waitKeyEx(30)
        if k == -1:
            continue
        low = k & 0xFF
        if low in (13, 10):
            return buf.strip()
        if low == 27:
            return ""
        if low in (8, 127):
            buf = buf[:-1]
        elif 32 <= low < 127:
            buf += chr(low)


def menu(win, title, opts, footer=None, w=980, h=None):
    """Menu a TASTI in finestra: opts = [(tasto, etichetta)]. Ritorna il tasto premuto.
    INVIO = primo tasto della lista (default)."""
    h = h or (140 + 46 * len(opts) + (40 if footer else 0))
    frame = np.full((h, w, 3), 18, np.uint8)
    cv2.putText(frame, title, (24, 48), FONT, 0.85, (0, 255, 255), 2)
    for i, (key, lab) in enumerate(opts):
        cv2.putText(frame, f"{key})  {lab}", (40, 104 + i * 46), FONT, 0.65, (220, 220, 220), 1)
    hint = f"premi il tasto  (INVIO = {opts[0][0]})"
    cv2.putText(frame, hint, (24, h - (52 if footer else 18)), FONT, 0.55, (140, 140, 140), 1)
    if footer:
        cv2.putText(frame, footer[:120], (24, h - 18), FONT, 0.5, (0, 200, 255), 1)
    valid = {ord(k): k for k, _ in opts}
    while True:
        cv2.imshow(win, frame)
        k = cv2.waitKey(50) & 0xFF
        if k in (13, 10):
            return opts[0][0]
        if k in valid:
            return valid[k]


# ---------------------------------------------------------------- wizard GT
HOLE_RE = re.compile(r"^[a-j]\d{1,2}$|^[+-]$|^[+-][bt]\d{0,2}$")
CANW, CANH = 1600, 660                   # tela immagine FISSA: letterbox, mai stirata


def _n_pins(cls):
    """Pin totali della classe (fonte: df.PINS; ripiego se probe non è nel path)."""
    try:
        import differential as df
        return df.PINS.get(cls, 2)
    except Exception:
        return {"bottone": 4, "rgb": 4, "optoaccoppiatore": 6, "transistor": 3,
                "trimmer": 3}.get(cls, 2)


def _rail_attesa(holes):
    """Rail attese dalla stringa holes del golden ('anodo j15 . catodo -> -'):
    lista di '+'/'-' nell'ordine in cui compaiono. Normalizza il meno unicode."""
    return re.findall(r"[+-]", str(holes).replace("−", "-").replace("→", " "))


def _letterbox(img, w=CANW, h=CANH):
    """img dentro (w,h) SENZA stirare: scala al lato più vincolante, centra su fondo."""
    can = np.full((h, w, 3), 12, np.uint8)
    if img is None:
        cv2.putText(can, "immagine non trovata", (40, h // 2), FONT, 1.0, (0, 140, 255), 2)
        return can
    s = min(w / img.shape[1], h / img.shape[0])
    nw, nh = int(img.shape[1] * s), int(img.shape[0] * s)
    x0, y0 = (w - nw) // 2, (h - nh) // 2
    can[y0:y0 + nh, x0:x0 + nw] = cv2.resize(img, (nw, nh))
    return can


def _boxes_bar(w, boxes, kinds, active, virgin, el, cls, step, nstep, foto_mode,
               colore_err=False, polarita_err=False):
    """Barra inferiore del wizard: info step + caselle coordinate (griglia + RAIL,
    etichettate) + guida tasti. Larghezza fissa = tela immagine."""
    h = 170
    bar = np.full((h, w, 3), 18, np.uint8)
    cv2.line(bar, (0, 1), (w, 1), (90, 90, 90), 1)
    cv2.putText(bar, f"STEP {step}/{nstep}: {el}  [{cls}]  -  fori REALI dei pin:",
                (14, 30), FONT, 0.62, (0, 255, 255), 2)
    bw, bh, x = 120, 46, 14
    for j, txt in enumerate(boxes):
        rail = kinds[j] == "rail"
        col = (0, 255, 255) if j == active else ((60, 130, 200) if rail else (110, 110, 110))
        cv2.rectangle(bar, (x, 58), (x + bw, 58 + bh), col, 2 if j == active else 1)
        cv2.putText(bar, "rail +/-" if rail else "griglia", (x, 52), FONT, 0.42,
                    (60, 130, 200) if rail else (120, 120, 120), 1)
        tcol = (160, 160, 160) if virgin[j] else (255, 255, 255)
        cv2.putText(bar, txt or "-", (x + 12, 58 + bh - 14), FONT, 0.75, tcol, 2)
        x += bw + 14
    cv2.putText(bar, f"[vista: {foto_mode}]", (x + 10, 58 + bh - 14), FONT, 0.5, (140, 140, 140), 1)
    if colore_err:
        cv2.putText(bar, "COLORE ERRATO", (x + 10, 30), FONT, 0.6, (0, 0, 255), 2)
    if polarita_err:
        cv2.putText(bar, "POLARITA' ERRATA", (x + 10, 52), FONT, 0.6, (0, 0, 255), 2)
    guida = ("foro griglia es. g13, rail: + o - | TAB/frecce = casella | INVIO = conferma step | "
             "PgSu = step prec. | X = non montato | / = colore errato | P = polarita' errata"
             " | SU/GIU = vista | Q = esci")
    cv2.putText(bar, guida, (14, h - 14), FONT, 0.47, (140, 140, 140), 1)
    return bar


def vista_griglia(rundir, k, coords):
    """Foto dello scatto con le etichette dei fori e i golden riquadrati. Nient'altro.

    È la vista di default del wizard GT: le altre (blob, NCC-D) mostrano misure e
    pin del sistema, e chi le guarda mentre dà il ground truth smette di essere una
    fonte indipendente. Qui non c'è un solo numero prodotto dal motore: solo dove
    sono i fori e come si chiamano.

    Le posizioni vengono da pixel.json (la mappa registrata su quello scatto, 632 fori).
    Ritorna il path del PNG, generato una volta e riusato."""
    import cv2
    out = os.path.join(rundir, "telemetria", "03_gt")
    os.makedirs(out, exist_ok=True)
    dst = os.path.join(out, f"griglia_{k}.png")
    if os.path.exists(dst):
        return dst
    foto = os.path.join(rundir, f"{k}.jpeg")
    px = os.path.join(rundir, "pixel.json")
    if not (os.path.exists(foto) and os.path.exists(px)):
        return None
    img = cv2.imread(foto)
    if img is None:
        return None
    dati = json.load(open(px, encoding="utf-8"))
    voce = next((v for v in dati.values() if v.get("k") == k), None) or \
        next(iter(dati.values()), None)
    if not voce or not voce.get("fori"):
        return None
    fori = {n: p for n, p in voce["fori"].items() if n[:1].isalpha()}
    att = [c.strip().lower() for c in (coords or []) if c.strip().lower() in fori]
    if not att:
        return None

    def segna(tela, scala, ox, oy, ogni, sc_txt, sp):
        """Etichette + cerchietti sui fori, quadrato sui golden. `ogni` = passo delle
        etichette in colonne (sulla board intera si etichetta rado, nello zoom tutto)."""
        for nome, (x, y) in fori.items():
            x, y = int((x - ox) * scala), int((y - oy) * scala)
            if not (0 <= x < tela.shape[1] and 0 <= y < tela.shape[0]):
                continue
            cv2.circle(tela, (x, y), max(2, int(3 * scala)), (150, 150, 150), -1)
            col = int(nome[1:]) if nome[1:].isdigit() else 0
            if col % ogni == 0 or nome in att:
                cv2.putText(tela, nome, (x - int(13 * sc_txt), y - int(12 * sc_txt)),
                            FONT, 0.42 * sc_txt, (20, 25, 30), sp + 2)
                cv2.putText(tela, nome, (x - int(13 * sc_txt), y - int(12 * sc_txt)),
                            FONT, 0.42 * sc_txt, (255, 255, 255), sp)
        for nome in att:
            x, y = fori[nome]
            x, y = int((x - ox) * scala), int((y - oy) * scala)
            r = int(17 * scala)
            cv2.rectangle(tela, (x - r, y - r), (x + r, y + r), (20, 25, 30), 4)
            cv2.rectangle(tela, (x - r, y - r), (x + r, y + r), (255, 255, 255), 2)

    W = 1200
    # --- pannello 1: board intera, per capire DOVE si sta guardando ---
    s0 = W / img.shape[1]
    intera = cv2.resize(img, (W, int(img.shape[0] * s0)))
    segna(intera, s0, 0, 0, ogni=10, sc_txt=1.0, sp=1)

    # --- pannello 2: zoom sulla zona del golden, con TUTTE le etichette leggibili ---
    xs = [fori[n][0] for n in att]
    ys = [fori[n][1] for n in att]
    pitch = 29
    if ("a2" in fori and "a3" in fori):
        pitch = abs(fori["a3"][0] - fori["a2"][0]) or 29
    g = int(5 * pitch)
    H0, W0 = img.shape[:2]
    x0, x1 = max(0, int(min(xs)) - g), min(W0, int(max(xs)) + g)
    y0, y1 = max(0, int(min(ys)) - g), min(H0, int(max(ys)) + g)
    crop = img[y0:y1, x0:x1]
    s1 = W / max(1, crop.shape[1])
    zoom = cv2.resize(crop, (W, max(1, int(crop.shape[0] * s1))),
                      interpolation=cv2.INTER_CUBIC)
    segna(zoom, s1, x0, y0, ogni=1, sc_txt=min(2.2, s1), sp=2)
    cv2.rectangle(intera, (int(x0 * s0), int(y0 * s0)), (int(x1 * s0), int(y1 * s0)),
                  (60, 230, 255), 2)

    barra = np.full((34, W, 3), (30, 25, 20), np.uint8)
    cv2.putText(barra, f"scatto {k}  -  golden atteso: {' '.join(att)}"
                       "   (nessun numero del sistema: giudica la foto)",
                (12, 24), FONT, 0.6, (240, 237, 232), 1)
    cv2.imwrite(dst, np.vstack([intera, barra, zoom]))
    return dst


def _viste_gt(rundir, k, coords=None):
    """Le viste disponibili per una foto nel wizard GT.
    Ritorna [(nome, path)]: griglia (default, senza i numeri del motore), pannello
    radar, foto, e gli artefatti telemetria della valutazione con quella foto."""
    import glob
    v = []
    g = vista_griglia(rundir, k, coords)
    if g:
        v.append(("griglia", g))           # default: nessun numero del motore
    v += [("pannello", os.path.join(rundir, f"radar_{k}.png")),
          ("foto", os.path.join(rundir, f"{k}.jpeg"))]
    d = os.path.join(rundir, "telemetria", "02_valutazioni")
    try:
        for rj in sorted(glob.glob(os.path.join(d, "v*_rec.json"))):
            try:
                if json.load(open(rj, encoding="utf-8")).get("k") != k:
                    continue
            except Exception:
                continue
            tag = os.path.basename(rj)[:4]
            for suff, nome in (("blob", "blob"), ("ncc_D", "NCC")):
                p = os.path.join(d, f"{tag}_{suff}.png")
                if os.path.exists(p):
                    v.append((nome, p))
    except Exception:
        pass
    return v


def gt_wizard(records, rundir, cid, win="GT - fori reali"):
    """Wizard GT a fine run: per ogni step mostra il PANNELLO RADAR (radar_k.png,
    SU/GIU per la foto grezza k.jpeg) e le caselle coordinate precompilate con
    l'ATTESO golden: correggi solo ciò che era diverso. Ritorna il dict gt
    (stesso formato di raccogli_gt) o None se esci con Q."""
    per_step = {}
    for r in records:
        if r.get("step") is not None and r["atteso"].get("coords"):
            per_step[r["step"]] = r          # l'ultima occorrenza vince (correzione sovrascrive lo scarto)
    steps = [per_step[n] for n in sorted(per_step)]
    if not steps:
        return None
    # stato per step: caselle griglia (prefill = atteso) + caselle RAIL (prefill dal
    # golden 'holes' se disponibile); flag "vergine" = primo tasto sovrascrive
    st = []
    for r in steps:
        grid = list(r["atteso"]["coords"])
        rails = _rail_attesa(r["atteso"].get("holes", ""))
        n_rail = max(_n_pins(r["atteso"]["cls"]) - len(grid), len(rails))
        rails = (rails + [""] * n_rail)[:n_rail]
        st.append({"boxes": grid + rails,
                   "kinds": ["grid"] * len(grid) + ["rail"] * n_rail,
                   "att_rail": rails[:], "colore": False, "polarita": False,
                   "virgin": [True] * (len(grid) + n_rail)})
    i, j, fi = 0, 0, 0
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    try:                       # in primo piano: se il wizard si apre dietro le altre
        cv2.setWindowProperty(win, cv2.WND_PROP_TOPMOST, 1)   # finestre l'operatore non
    except cv2.error:          # lo vede, chiude il programma e perde GT ed Excel
        pass
    while True:
        r, s = steps[i], st[i]
        k = r["k"]
        viste = _viste_gt(rundir, k, r["atteso"].get("coords"))
        nome_v, pth = viste[fi % len(viste)]
        img = cv2.imread(pth)
        if img is None:                                   # fallback: l'altra immagine
            img = cv2.imread(os.path.join(rundir, f"{k}.jpeg"))
        im = _letterbox(img)                              # proporzioni SEMPRE giuste
        bar = _boxes_bar(CANW, s["boxes"], s["kinds"], j, s["virgin"], r["atteso"]["el"],
                         r["atteso"]["cls"], r["step"], steps[-1]["step"],
                         f"{nome_v} {(fi % len(viste)) + 1}/{len(viste)}",
                         colore_err=s["colore"], polarita_err=s["polarita"])
        cv2.imshow(win, np.vstack([im, bar]))
        key = cv2.waitKeyEx(50)
        if key == -1:
            continue
        low = key & 0xFF
        if low == ord('q'):
            cv2.destroyWindow(win)
            return None
        if key in K_UP:
            fi -= 1                          # scorre le viste: pannello/foto/blob/NCC/...
        elif key in K_DOWN:
            fi += 1
        elif key in K_RIGHT or low == 9:                  # TAB / freccia dx
            j = (j + 1) % len(s["boxes"])
        elif key in K_LEFT:
            j = (j - 1) % len(s["boxes"])
        elif low == ord('x'):                             # componente NON montato
            s["boxes"] = [""] * len(s["boxes"])
            s["virgin"] = [False] * len(s["boxes"])
        elif low == ord('/'):                             # colore del pezzo sbagliato (GT)
            s["colore"] = not s["colore"]
        elif low == ord('p'):
            # Polarità: fori giusti con il pezzo girato (opto a 180 gradi, ingresso e
            # uscita scambiati). Senza questa bandiera nel GT i KO POLARITA finirebbero
            # fra i falsi allarmi proprio dove il sistema ha ragione. 'p' non è un
            # carattere di foro: non ruba tasti alle caselle.
            s["polarita"] = not s["polarita"]
        elif key == 2162688:                              # PgSu = step precedente
            i = max(0, i - 1); j = 0
        elif low in (13, 10):                             # conferma step -> avanti / fine
            if i + 1 < len(steps):
                i += 1; j = 0
            else:
                break
        elif low in (8, 127):
            s["boxes"][j] = s["boxes"][j][:-1]; s["virgin"][j] = False
        elif 32 <= low < 127 and chr(low).lower() in "abcdefghij0123456789+-t":
            ch = chr(low).lower()
            if ch in "+-" and s["kinds"][j] == "grid":
                # '-' in una casella di griglia: l'operatore lo usa per "non c'è" /
                # "non lo vedo", ma score_oper.foro_valido() lo accetta come token di
                # rail ("il pin sta sul negativo"). Due significati opposti nello stesso
                # simbolo: qui si svuota la casella (foro ignoto), X resta il modo per
                # dire "componente non montato".
                s["boxes"][j] = ""
                s["virgin"][j] = False
                continue
            if s["virgin"][j]:                            # primo tasto: sovrascrive l'atteso
                s["boxes"][j] = ""
                s["virgin"][j] = False
            s["boxes"][j] += ch
            if j + 1 < len(s["boxes"]) and HOLE_RE.match(s["boxes"][j]) \
               and len(s["boxes"][j]) >= 3:               # g13 completo -> casella dopo
                j += 1
    cv2.destroyWindow(win)
    out = []
    for r, s in zip(steps, st):
        att = r["atteso"]["coords"]
        grid = [b for b, kn in zip(s["boxes"], s["kinds"]) if b and kn == "grid"]
        rail = [b for b, kn in zip(s["boxes"], s["kinds"]) if b and kn == "rail"]
        att_rail = [x for x in s["att_rail"] if x]
        err = sorted(grid) != sorted(att)
        if att_rail:                     # rail attesa nota dal golden: anche la polarità conta
            err = err or sorted(rail) != sorted(att_rail)
        out.append({"step": r["step"], "k": r["k"], "atteso": att, "atteso_rail": att_rail,
                    "reale": grid, "reale_rail": rail, "errore_montaggio": err,
                    "errore_colore": s["colore"],
                    "errore_polarita": s["polarita"],
                    # toccato = l'operatore ha digitato su almeno una casella. Misura
                    # l'attenzione del giro di GT: un giro tutto a INVIO produce
                    # "zero errori" e non misura niente.
                    "toccato": not all(s["virgin"])})
    return {"commessa": cid, "steps": out}


def salva_gt(gt, registro_path):
    """Scrive <run>_gt.json accanto al registro. Ritorna il path."""
    gpath = registro_path.replace("_registro.json", "_gt.json")
    json.dump(gt, open(gpath, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    nerr = sum(s["errore_montaggio"] or s.get("errore_colore", False)
               or s.get("errore_polarita", False) for s in gt["steps"])
    say(f"GT salvato: {gpath}  ({nerr} errori montaggio dichiarati)")
    return gpath
