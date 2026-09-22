"""filo.py — ramo FILO: jumper e cavetti. Due sottorami, separati dalla fisica.

Misurato su 50 scatti con ground truth:

                        NCC sul foro vero    a 1 colonna    sagoma: errore mediano
    blu FLESSIBILE           0.60               0.01              1.71 passi
    rosso rigido             0.29               0.01              0.20 passi
    blu rigido               0.26               0.01              0.54 passi
    arancione rigido         0.20               ---               0.18 passi

I due tipi vogliono strumenti opposti, per un motivo meccanico:

  FLESSIBILE   struttura: collare nero - filo blu - collare nero. Il metallo non
               si vede, sta dentro il collare infilato nel foro. Ciò che tocca il
               piano della board è il collare, grasso, che copre il foro: l'NCC lo
               sente fortissimo. Il filo invece galleggia in aria per quasi tutta
               la lunghezza e la sua immagine è spostata dalla prospettiva: la
               sagoma non dice dove entra (1.71 passi di errore mediano; inseguendo
               il collare va peggio, 2.62).

  RIGIDO       filo sottile appoggiato sulla board. Entra con un puntino, l'NCC lo
               sente poco (0.26-0.29), ma la sagoma sta dove sta il pezzo: errore
               0.20 passi.

In sintesi: l'NCC dove il pezzo è grasso, la sagoma dove è sottile e piatto.

Limite dichiarato: mai OK FORTE. Nel flessibile i piedini non sono osservabili
(stanno dentro il collare); nel rigido la sagoma è una prova indiretta, e 6 capi
su 20 restano oltre il mezzo foro.

Fuori scopo: il VERDE. Filo sottile (NCC debole) e sagoma non trovata: la soglia
di saturazione lo taglia via al 97% perché è un verde spento. Dichiararlo fuori
non bastava: la sagoma non è che non aggancia niente, aggancia l'oggetto
sbagliato, e verifica_rigido restituiva un LONTANO a 17 passi con la stessa faccia
di una misura buona (2 falsi allarmi su 6 in una campagna di 10 run, su fori del
golden centrati). I colori in COL_FUORI_SCOPO non producono verdetto: il ramo
dichiara FUORI SCOPO e lascia decidere agli altri strati.
"""
import collections
import numpy as np

FAM_FILO = {"jumper", "cavetto"}

V_FLOOR = 40         # pavimento di luminosità della maschera colore. Misurato: alzarlo
                     # fino a 110 non cambia nessuna distanza, perché il vincolo al blob
                     # tiene già l'ombra fuori dal calcolo del capo; a 130 inizia a
                     # mangiarsi il filo vero.
T_COPERTO = 0.35     # NCC sotto cui il foro non è coperto dal collare (misurato: 0.01 / 0.60)
T_VICINO = 0.70      # passi entro cui il capo della sagoma conferma il foro
T_LONTANO = 1.50     # oltre i quali il pezzo è da un'altra parte
SC = 4               # sottocampionamento della visita

COLORI = ("rosso", "blu", "verde", "giallo", "nero", "bianco", "arancione")
COL_FUORI_SCOPO = {"verde"}    # la segmentazione per tinta non lo tiene (v. docstring)


def flessibile(el):
    """Il golden descrive il pezzo a parole: 'Cavetto blu flessibile GND'."""
    return "fless" in (el or "").lower()


def colore(el):
    e = (el or "").lower()
    return next((c for c in COLORI if c in e), None)


# --------------------------------------------------------------------------
# FLESSIBILE: il collare copre il foro. Stessa domanda del ramo corpo.
# --------------------------------------------------------------------------

def verifica_flessibile(S, attesi):
    """Ritorna (livello, dettaglio): COPERTO / SCOPERTO."""
    freddi = [uv for uv in attesi if S.get(uv, 0.0) < T_COPERTO]
    if freddi:
        det = ", ".join(f"{u},{v}={S.get((u, v), 0.0):.2f}" for u, v in freddi)
        return "SCOPERTO", f"il collare non copre: {det} (soglia {T_COPERTO})"
    peggio = min(S.get(uv, 0.0) for uv in attesi)
    return "COPERTO", f"il collare copre i fori attesi (il peggiore {peggio:.2f})"


# --------------------------------------------------------------------------
# RIGIDO: il filo sta dove appare. I due capi di una CURVA con la doppia visita.
# --------------------------------------------------------------------------

def sagoma(img, col, bmask, blob=None):
    """Maschera del filo, per colore. None se non si aggancia.

    blob = i punti cambiati fra i due scatti. Sulla board c'è quasi sempre più di un
    oggetto dello stesso colore (l'altro cavetto, il trimmer blu, le scritte): senza
    questo vincolo la maschera aggancia il pezzo sbagliato e il capo esce a 5-8 passi
    dal foro (misurato: 2 falsi KO POSIZIONE su blu rigido). Il cavo appena montato
    è quello che è cambiato."""
    import cv2
    import differential as df
    if col not in df.HUES:
        return None
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    m = np.zeros(img.shape[:2], np.uint8)
    for lo, hi in df.HUES[col]:
        m |= cv2.inRange(hsv, (lo, 70, V_FLOOR), (hi, 255, 255))
    m &= bmask
    if blob is not None and len(blob):
        z = np.zeros(img.shape[:2], np.uint8)
        z[blob[:, 1].astype(int), blob[:, 0].astype(int)] = 255
        z = cv2.dilate(z, np.ones((21, 21), np.uint8))
        m &= z
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, ls, st, _ = cv2.connectedComponentsWithStats(m)
    if n < 2:
        return None
    i = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
    if st[i, cv2.CC_STAT_AREA] < 500:
        return None
    return (ls == i).astype(np.uint8) * 255


def _lontano(mask, start):
    h, w = mask.shape
    dist = -np.ones((h, w), np.int32)
    q = collections.deque([start])
    dist[start[1], start[0]] = 0
    best = (start, 0)
    while q:
        x, y = q.popleft()
        d = dist[y, x]
        if d > best[1]:
            best = ((x, y), d)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and mask[ny, nx] and dist[ny, nx] < 0:
                dist[ny, nx] = d + 1
                q.append((nx, ny))
    return best


def capi(mask):
    """I due capi della curva: doppia visita dentro la sagoma, così vale anche se
    il filo gira. L'asse del blob su un arco non direbbe niente."""
    import cv2
    small = cv2.resize(mask, None, fx=1. / SC, fy=1. / SC, interpolation=cv2.INTER_NEAREST)
    ys, xs = np.where(small > 0)
    if len(xs) < 20:
        return None
    A, _ = _lontano(small, (int(xs[0]), int(ys[0])))
    B, _ = _lontano(small, A)
    C, _ = _lontano(small, B)
    return (B[0] * SC, B[1] * SC), (C[0] * SC, C[1] * SC)


def contorno(mask):
    """Poligono della sagoma, semplificato: serve solo a ridisegnarla nel pannello.
    La maschera intera peserebbe quanto la foto."""
    import cv2
    cs, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cs:
        return []
    c = max(cs, key=cv2.contourArea)
    eps = 0.004 * cv2.arcLength(c, True)
    return [[int(p[0][0]), int(p[0][1])] for p in cv2.approxPolyDP(c, eps, True)]


def sagoma_diff(res, soglia=18):
    """Sagoma del cambiamento intero dal diff. Il blob è la sola componente connessa
    più grande e taglia le gambe sottili; il diff le vede: si riprende ogni
    componente che tocca la zona del blob."""
    import cv2
    import differential as df
    pts = res.get("blob_pts")
    if pts is None or not len(pts):
        return None
    g = cv2.cvtColor(cv2.absdiff(res["img"], res["prev"]), cv2.COLOR_BGR2GRAY)
    posx = {**res["pos"], **res["rpos"]}
    bm = df.board_mask(posx, res["img"].shape, grow=40)
    m = (((g > soglia) & (bm > 0)).astype(np.uint8)) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((13, 13), np.uint8))
    seed = np.zeros_like(m)
    pp = pts.astype(int)
    seed[pp[:, 1], pp[:, 0]] = 255
    seed = cv2.dilate(seed, np.ones((25, 25), np.uint8))
    # Fantasmi delle correzioni: il diff di una correzione contiene il pezzo dov'era
    # e dov'è. Il fantasma ha struttura nel prima e board piatta nel dopo: si tiene
    # la componente solo se il dopo ha almeno tanta struttura (bordi) quanto il prima.
    g0 = cv2.cvtColor(res["prev"], cv2.COLOR_BGR2GRAY)
    g1 = cv2.cvtColor(res["img"], cv2.COLOR_BGR2GRAY)
    l0 = np.abs(cv2.Laplacian(g0, cv2.CV_32F))
    l1 = np.abs(cv2.Laplacian(g1, cv2.CV_32F))
    n, ls, st, _ = cv2.connectedComponentsWithStats(m)
    keep = np.zeros_like(m)
    for i in range(1, n):
        if st[i, cv2.CC_STAT_AREA] < 150:
            continue
        comp = ls == i
        if not (seed[comp] > 0).any():
            continue
        if float(l1[comp].mean()) < 0.75 * float(l0[comp].mean()):
            continue                       # fantasma: struttura solo nel prima
        keep[comp] = 255
    # Raffinamento a pixel (correzioni con posizioni sovrapposte: vecchio e nuovo
    # finiscono nella stessa componente): via i pixel dove la struttura sta solo
    # nel prima; la chiusura ricuce il pezzo vero.
    b0 = cv2.GaussianBlur(l0, (15, 15), 0)
    b1 = cv2.GaussianBlur(l1, (15, 15), 0)
    keep[b1 < 0.6 * b0] = 0
    return cv2.morphologyEx(keep, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))


def verifica_inserzione(res, pos, attesi, pitch, thr):
    """Misura combinata per il filo rigido: il capo della sagoma-diff sceglie il foro
    candidato, l'NCC conferma l'ingresso, il golden muto conferma l'errore. Né la
    sagoma né l'NCC bastano da soli (misurato: la spina "tocca" 4 fori, l'NCC premia
    il collare).

        ALLINEATO   un capo della sagoma-diff su ogni foro atteso
        LONTANO     il capo entra in un altro foro (NCC acceso) e il golden è muto
        SCENTRATO   né l'uno né l'altro: dubbio dichiarato

    Ritorna (livello, dettaglio, geometria) o None se la sagoma non aggancia
    (chi chiama ripiega sulla misura a colore)."""
    import cv2
    import differential as df
    m = sagoma_diff(res)
    if m is None:
        return None
    # Guardia contro la sagoma degenerata (catene di ombre e slittamenti di
    # registrazione portavano i capi a 11-12 passi): se l'estensione ha più che
    # sestuplicato il blob, si torna alla silhouette del solo blob.
    pts = res.get("blob_pts")
    if pts is not None and len(pts) and int(m.sum() / 255) > 6 * len(pts):
        m = np.zeros(res["img"].shape[:2], np.uint8)
        pp = pts.astype(int)
        m[pp[:, 1], pp[:, 0]] = 255
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    c2 = capi(cv2.dilate(m, np.ones((5, 5), np.uint8)))
    if not c2:
        return None
    att = [uv for uv in attesi if uv in pos]
    if not att:
        return None
    geom = {"contorno": contorno(m), "capi": [list(map(int, q)) for q in c2],
            "colore": "diff"}
    # Decisione a snap: il capo della sagoma-diff è preciso (0.2-0.3 passi), quindi il
    # foro più vicino al capo deve essere il golden, non "abbastanza vicino" (una banda
    # di 0.70 passi promuoveva come allineato il capo nel foro adiacente).
    grid = [uv for uv in pos if isinstance(uv[0], int)]

    def snap(q):
        near = min(grid, key=lambda uv: (pos[uv][0] - q[0]) ** 2
                   + (pos[uv][1] - q[1]) ** 2)
        return near, np.hypot(pos[near][0] - q[0], pos[near][1] - q[1]) / pitch
    snaps = [snap(q) for q in c2]
    det = " ".join(f"{df.name(*n)}({d:.2f})" for n, d in snaps)
    ok = []
    for uv in att:
        i = int(np.argmin([np.hypot(pos[uv][0] - q[0], pos[uv][1] - q[1])
                           for q in c2]))
        n, d = snaps[i]
        ok.append(n == uv and d <= 0.6)
    if all(ok):
        return "ALLINEATO", f"capi della sagoma-diff nei fori attesi ({det})", geom
    g0 = cv2.cvtColor(res["prev"], cv2.COLOR_BGR2GRAY).astype(np.float32)
    g1 = cv2.cvtColor(res["img"], cv2.COLOR_BGR2GRAY).astype(np.float32)
    ncc_a = max(df.legscore(g0, g1, pos, *uv) for uv in att)
    for n, d in snaps:
        if n not in att and d <= 0.6:
            ncc_c = df.legscore(g0, g1, pos, *n)
            # Ingresso forte (>= 2*thr): separa un capo slittato di un foro su un
            # montaggio giusto (NCC 0.11) dai KO veri (0.155-0.42). Il margine sul
            # caso peggiore è sottile (0.155 vs 0.14): da ritarare con più ground truth.
            if ncc_c >= 2 * thr and ncc_a < thr:
                return ("LONTANO",
                        f"il capo della sagoma-diff entra in {df.name(*n)} "
                        f"(NCC {ncc_c:.2f}) e il golden e' muto ({ncc_a:.2f})", geom)
    return "SCENTRATO", f"capi della sagoma-diff: {det} - golden non confermato", geom


def verifica_rigido(img, pos, attesi, col, bmask, pitch, blob=None):
    """Ritorna (livello, dettaglio, geometria) oppure None se la sagoma non si aggancia.
         ALLINEATO    ogni foro atteso ha un capo del filo addosso
         SCENTRATO    il capo più vicino sta fra T_VICINO e T_LONTANO
         LONTANO      il filo è da un'altra parte
         FUORI SCOPO  colore che la segmentazione non tiene: non si giudica

    La geometria (contorno della sagoma + i due capi) esce sempre che la sagoma
    esista, anche a FUORI SCOPO: mostra su quale oggetto si è agganciata.
    """
    m = sagoma(img, col, bmask, blob)
    if m is None:
        return None
    c2 = capi(m)
    if not c2:
        return None
    geom = {"contorno": contorno(m), "capi": [list(map(int, q)) for q in c2], "colore": col}

    if col in COL_FUORI_SCOPO:
        return ("FUORI SCOPO",
                f"colore {col}: la segmentazione per tinta non lo tiene, la sagoma puo'"
                " agganciare il pezzo sbagliato. Ramo non applicabile", geom)

    peggio, det = 0.0, []
    for uv in attesi:
        if uv not in pos:
            continue
        d = min(np.hypot(pos[uv][0] - q[0], pos[uv][1] - q[1]) / pitch for q in c2)
        det.append(f"{uv[0]},{uv[1]}={d:.2f}")
        peggio = max(peggio, d)
    if not det:
        return None
    testo = f"capo del filo a {' '.join(det)} passi"
    if peggio <= T_VICINO:
        return "ALLINEATO", testo, geom
    if peggio <= T_LONTANO:
        return "SCENTRATO", testo, geom
    return "LONTANO", testo, geom
