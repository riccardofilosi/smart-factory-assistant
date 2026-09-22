"""radar_gate.py — filtro degli scatti che meritano l'analisi.

Uno scatto passa solo se è FERMO rispetto al precedente, mostra una NOVITÀ
LOCALIZZATA rispetto all'ultimo scatto validato (un componente è comparso da
qualche parte) e NON contiene una mano sulla board.

La novità non si misura sulla media dell'intera board: un componente piccolo
(~0,3% dell'area) si diluisce sotto il rumore. La board viene divisa in blocchi e
si guarda il blocco più caldo.

Il controllo esplicito della mano è necessario perché la cattura gira a intervallo
fisso in un thread separato: l'operatore che si ferma un istante con il dito sul
componente produce una "scena ferma con una novità" e, senza questo test, lo
scatto finirebbe in coda con la mano dentro (misurato: ~10% degli scatti).
"""
import numpy as np
import cv2

STILL   = 0.010   # differenza media globale (0-1) sotto cui la scena è ferma
NOVELTY = 0.040   # blocco più caldo (0-1) sopra cui c'è un componente nuovo.
                  # Il componente più debole del dataset (jumper corto) misura 0.044:
                  # una soglia più alta lo lascerebbe fuori per sempre. I falsi
                  # positivi da micro-spostamento li scarta lo stadio di analisi.
GRID    = 16      # board ridotta a 256x256 e divisa in 16x16 blocchi (~1 blocco per pezzo)
MANO_AREA = 25000 # px di incarnato sopra cui c'è una mano nel campo.
                  # Il colore da solo non separa: LED rossi e cavetti arancioni hanno la
                  # stessa firma della pelle. Separa la dimensione. Misurato sugli scatti
                  # grezzi dell'archivio (365 foto, 15 mani confermate a vista):
                  #    mani            57.870 - 1.191.520 px
                  #    tutto il resto       <= 14.439 px
                  # 25.000 sta nel vuoto: 1,7x sopra il più grande non-mano e 2,3x sotto
                  # la mano più piccola. Nessun vincolo di forma: una mano reale arriva a
                  # 3,6 di rapporto fra i lati e un filtro sulla compattezza la perdeva.
                  # Tarato su un operatore e una illuminazione: carnagioni o luci diverse
                  # possono non agganciare. Il guasto è dal lato sicuro (lo scatto passa
                  # agli altri controlli), ma va rimisurato se cambia la postazione.

def _small_gray(img):
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, (256, 256), interpolation=cv2.INTER_AREA).astype("float32") / 255.0
    return g - float(g.mean())   # esposizione globale esclusa: conta la struttura, non la luce

def frame_diff(a, b):
    """Differenza media assoluta (0-1) fra due scatti: test 'scena ferma'."""
    return float((abs(_small_gray(a) - _small_gray(b))).mean())

def novelty(a, b):
    """Novità localizzata: media della differenza per blocco, si tiene il massimo.
    Un componente piccolo accende un blocco anche se la media globale resta piatta."""
    d = np.abs(_small_gray(a) - _small_gray(b))
    bs = 256 // GRID
    blocks = d[:GRID * bs, :GRID * bs].reshape(GRID, bs, GRID, bs).mean(axis=(1, 3))
    return float(blocks.max())

def mano(img):
    """True se c'è una mano nel campo: una macchia di incarnato più grande di qualunque
    componente. L'incarnato è R>G>B con margine (regola di Kovac) E saturazione media.
    I due vincoli fanno due esclusioni diverse: il primo scarta i corpi beige/grigi del
    kit, che hanno i tre canali quasi uguali; il secondo scarta i rossi di plastica,
    saturi mentre la pelle non lo è. Poi decide la dimensione (MANO_AREA)."""
    b = img.astype(np.int16)
    B, G, R = b[:, :, 0], b[:, :, 1], b[:, :, 2]
    mx, mn = np.max(b, axis=2), np.min(b, axis=2)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    S, V = hsv[:, :, 1], hsv[:, :, 2]
    pelle = ((R > 95) & (G > 40) & (B > 20) & (mx - mn > 15) & (R - G > 15)
             & (R > G) & (R > B) & (S >= 25) & (S <= 150) & (V > 80))
    # chiusura morfologica: nocche e pieghe spezzano la macchia, va ricucita prima di misurarla
    pelle = cv2.morphologyEx(pelle.astype(np.uint8) * 255, cv2.MORPH_CLOSE,
                             np.ones((9, 9), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(pelle)
    return any(stats[i, cv2.CC_STAT_AREA] >= MANO_AREA for i in range(1, n))


def should_analyze(cur, prev, last_valid):
    """True se: cur è fermo rispetto a prev, ha una novità localizzata rispetto a
    last_valid e non contiene una mano. Un micro-spostamento della board può accendere
    un blocco: lo scarta lo stadio di analisi, che allinea prima di cercare.

    Il test sulla mano è volutamente l'ultimo: i primi due lavorano su una miniatura
    256x256, questo passa l'immagine a piena risoluzione e si paga solo sui pochi
    scatti che stanno per essere validati."""
    if prev is None:
        return False
    if frame_diff(cur, prev) >= STILL:              # ancora in movimento
        return False
    if last_valid is not None and novelty(cur, last_valid) < NOVELTY:
        return False                                 # fermo e nessun blocco nuovo acceso
    if mano(cur):                                    # mano sul pezzo: lo scatto non serve
        return False
    return True
