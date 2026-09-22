"""corpo.py — ramo CORPO: buzzer e trimmer, componenti con i piedini sotto il corpo.

Le gambe non sono osservabili: sono nascoste dal corpo che le tiene. Misurato su
8 montaggi con ground truth (7 corretti + 1 trimmer spostato di 6 colonne):

    fori del golden, pezzo presente     legscore 0.60 - 1.18
    fori del golden, pezzo assente      legscore 0.01

Il valore sopra 1.00 non è un errore: legscore = 1 - NCC, e il corpo scuro sopra
la plastica bianca inverte la struttura del patch (NCC negativo).

La domanda giusta non è "dov'è il corpo" ma "il pezzo è sopra questi fori".
Misurato spostando il golden di k colonne (equivale a montare il pezzo di -k):

    spostamento         k=0     k=1     k=2      preso da
    buzzer  (4 casi)    0.60    0.24    0.01     k=1
                        0.68    0.15    0.00     k=1
                        0.68    0.33    0.02     k=1
                        0.70    0.56    0.05     k=2
    trimmer (3 casi)    0.61    0.24    0.00     k=1
                        0.79    0.22    0.00     k=1
                        0.93    0.33    0.01     k=1

Una colonna di scarto: rilevata in 7 casi su 8. Due colonne: 8 su 8. Non serve
localizzare il corpo.

La riga non si giudica: uno spostamento in colonna porta i fori fuori sia dal
corpo sia dall'ombra e il legscore crolla; uno spostamento in riga li porta dentro
l'ombra, che è calda quanto il corpo.

Alternative misurate e scartate:
  - baricentro del corpo in colonna: ripetibile a 0.05-0.18 passi ma aggiunge poco
    e risente di ombra e parallasse. Sul buzzer il blob non è il corpo: faccia bianca
    su board bianca cambia pochi pixel, il blob traccia il bordo scuro e l'ombra.
  - segmentazione della testa per luminosità (Otsu) dentro il blob: peggiora
    (0.06 -> 0.27).
  - foro sparito per contrasto: sul buzzer il bordo nero si fa scambiare per un
    foro, sul trimmer la zigrinatura blu non fa crollare il contrasto.

Limite dichiarato: mai OK FORTE. I piedini non sono osservabili; la conferma viene
dalla posizione del pezzo e dalla sua rigidità.
"""
import os
import numpy as np

T_COPERTO = 0.35   # legscore sotto cui il foro non è coperto (misurato: 0.01 assente / 0.60 presente)
FAM_CORPO = {"buzzer", "trimmer", "potenziometro"}

# --- riferimento memorizzato (solo trimmer, vedi sotto) ---
NP = 5.0           # semi-larghezza del ritaglio, in passi
H = 26             # px per passo nel ritaglio raddrizzato
T_RIF = 0.45       # NCC sotto cui il riferimento non riconosce il pezzo
FAM_RIFERIMENTO = {"trimmer", "potenziometro"}
DIR_RIF = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "data", "riferimenti")


def verifica(S, attesi):
    """Regola portante del ramo. Ritorna (livello, dettaglio).
         COPERTO   tutti i fori del golden sono sotto il pezzo
         SCOPERTO  almeno uno è tornato nudo: il pezzo non è lì
    """
    freddi = [uv for uv in attesi if S.get(uv, 0.0) < T_COPERTO]
    if freddi:
        det = ", ".join(f"{u},{v}={S.get((u, v), 0.0):.2f}" for u, v in freddi)
        return "SCOPERTO", f"fori del golden tornati nudi: {det} (soglia {T_COPERTO})"
    peggio = min(S.get(uv, 0.0) for uv in attesi)
    return "COPERTO", f"tutti i fori del golden sotto il pezzo (il peggiore {peggio:.2f})"


# ---------------------------------------------------------------------------
# Riferimento memorizzato — solo per i componenti bassi (trimmer).
#
# Si memorizza il ritaglio del montaggio validato in coordinate della board: la
# board si muove nella foto, il ritaglio no. Poi lo si fa scorrere in colonna sulla
# foto nuova e si tiene il confronto migliore. Misurato con riferimento e prova su
# run diverse: trimmer corretto picco a 0.0 con NCC 0.73-0.78, trimmer spostato di
# 6 colonne NCC 0.07.
# Il buzzer resta escluso: è alto un centimetro, la sua faccia sta sopra il piano
# che la mappa raddrizza e appare ingrandita e traslata in funzione della distanza
# della fotocamera. Con scala libera il picco si raddrizza su un montaggio (1.12 e
# 0.82, reciproche) ma non sull'altro (NCC 0.24); per il trimmer la scala scelta è
# 1.00. Con fotocamera su supporto fisso la deformazione diventa costante e il
# riferimento varrebbe anche per il buzzer.
# ---------------------------------------------------------------------------

def ritaglio(img, pos, centro_uv, dc=0.0):
    """Ritaglio in coordinate della board. dc = scorrimento in colonne."""
    import cv2
    u, v = centro_uv
    if (u, v) not in pos:
        return None
    n = int(NP * 2 * H)
    ex = (np.array(pos[(u, v+1)], float) - np.array(pos[(u, v)], float)
          if (u, v+1) in pos else np.array([29., 0.]))
    ey = (np.array(pos[(u+1, v)], float) - np.array(pos[(u, v)], float)
          if (u+1, v) in pos else np.array([0., 29.]))
    o = np.array(pos[(u, v)], float) + ex * dc
    M = np.array([[ex[0]/H, ey[0]/H, o[0] - (ex[0]+ey[0])*NP],
                  [ex[1]/H, ey[1]/H, o[1] - (ex[1]+ey[1])*NP]], np.float32)
    return cv2.warpAffine(img, M, (n, n), flags=cv2.WARP_INVERSE_MAP | cv2.INTER_LINEAR)


def _percorso_rif(commessa, step):
    return os.path.join(DIR_RIF, f"{commessa}_s{step}.png")


def salva_riferimento(img, pos, centro_uv, commessa, step):
    """Memorizza il ritaglio di un montaggio validato. Da chiamare a mano, mai in automatico."""
    import cv2
    r = ritaglio(img, pos, centro_uv)
    if r is None:
        return None
    os.makedirs(DIR_RIF, exist_ok=True)
    p = _percorso_rif(commessa, step)
    cv2.imwrite(p, r)
    return p


def confronta_riferimento(img, pos, centro_uv, commessa, step):
    """Ritorna (scarto_in_colonne, ncc) oppure None se il riferimento non esiste."""
    import cv2
    p = _percorso_rif(commessa, step)
    if not os.path.exists(p):
        return None
    ref = cv2.imread(p)
    if ref is None:
        return None
    ref = cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY).astype(np.float32)
    c = ref.shape[0] // 2
    m = int(2.2 * H)
    ref = ref[c-m:c+m, c-m:c+m]
    best = None
    for dc in (-2, -1.5, -1, -0.5, 0, .5, 1, 1.5, 2):
        r = ritaglio(img, pos, centro_uv, dc)
        if r is None:
            continue
        t = cv2.cvtColor(r, cv2.COLOR_BGR2GRAY).astype(np.float32)[c-m:c+m, c-m:c+m]
        if t.shape != ref.shape:
            continue
        v = float(cv2.matchTemplate(t, ref, cv2.TM_CCOEFF_NORMED)[0, 0])
        if best is None or v > best[1]:
            best = (dc, v)
    return best
