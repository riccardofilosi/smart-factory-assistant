"""cosa.py — riconoscimento per ENUMERAZIONE, attivo solo sui riquadri ambigui.

Il riconoscimento standard (differential.vlm_arrived) chiede al VLM una sola
parola: "cosa è stato aggiunto dentro il riquadro magenta?". È una scelta forzata:
quando nel riquadro finisce più di un pezzo il modello sceglie il più vistoso.

Caso tipico: resistenza inserita accanto a un cavetto rosso già montato che
l'operatore ha urtato; il blob li unisce e il riquadro ne contiene due. Nessuna
variante del prompt a una parola cambia la risposta ("jumper rosso", 3/3):
regola di esclusione nel prompt, riquadro ristretto sui pin noti, margine 250 e
500 px, board intera, zoom 2x e 4x. Alla domanda "elenca cosa vedi" il VLM
risponde invece "jumper rosso, resistenza": il pezzo nuovo lo vedeva, era la
domanda a non permettergli di dirlo. Il confronto con il registro va fatto nel
codice, non chiesto al modello.

L'intervento è chirurgico perché sostituire il riconoscimento ovunque peggiora:
su 123 frame con ground truth il metodo standard legge giusto l'89%,
l'enumerazione con sottrazione globale il 46% (il registro elenca i pezzi di
tutta la board mentre l'elenco descrive il solo ritaglio, e al terzo jumper della
commessa la differenza è vuota). Quindi:
  - il riquadro è AMBIGUO se dentro il bbox cade il pin di un pezzo già registrato.
    È una verifica geometrica, non costa una chiamata. Scatta sul 38% dei frame,
    dove cadono 5 dei 7 errori di lettura del metodo standard;
  - riquadro PULITO -> domanda standard, intatta;
  - riquadro AMBIGUO -> elenco, e la sottrazione usa solo i pezzi del registro che
    cadono dentro quel bbox: insieme locale contro lista locale.

Resto vuoto (nessun pezzo nuovo) o ambiguo (due o più classi residue) -> ripiego
sulla domanda standard. Misurato sui 47 riquadri ambigui dell'archivio:
    standard                   42 giusti   5 sbagliati    0 muti
    sola enumerazione          34 giusti   0 sbagliati   13 muti
    enumerazione + ripiego     46 giusti   1 sbagliato    0 muti
L'enumerazione non sbaglia: o indovina o tace. Dove tace, la domanda standard è
giusta 12 volte su 13, quindi il ripiego vale più del silenzio. La seconda
chiamata costa solo sul 28% dei riquadri ambigui, cioè ~10% dei frame.
"""
import base64
import collections

import cv2

import differential as df

ELENCO_SYS = """Foto ritagliata di una breadboard durante un montaggio in corso.
Il RIQUADRO MAGENTA marca la ZONA in cui qualcosa e' cambiato. Dentro quella zona puo'
esserci PIU' DI UN componente: montando un pezzo se ne puo' urtare uno gia' presente.
ELENCA TUTTI i componenti che vedi DENTRO il riquadro, uno per riga, nel formato
"classe colore" (il colore solo se evidente, altrimenti la sola classe).
Classi possibili (a destra come riconoscerle sul kit reale):
{voci}
Non elencare nulla che stia fuori dal riquadro. Nessun'altra parola.""".format(
    voci="\n".join(f"- {k}: {v}" for k, v in df.DESCR.items()))


def _dentro(bbox, nome, posx):
    """Il foro `nome` cade dentro il bbox del blob?"""
    try:
        uv = df.parse_hole(nome)
    except Exception:
        return False
    if uv not in posx:
        return False
    x, y = posx[uv]
    return bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]


def gia_nel_riquadro(bbox, state, posx):
    """Classi dei pezzi già registrati che hanno almeno un pin dentro il riquadro.
    Vuoto = riquadro pulito: il pezzo nuovo è l'unico lì dentro."""
    out = []
    for c in state:
        if any(_dentro(bbox, n, posx) for n in (c.get("pins") or [])):
            out.append(c.get("cls"))
    return out


def crop_riquadro(img, bboxes):
    """Stesso ritaglio di df.vlm_arrived: margine 90 px e riquadro magenta sul blob."""
    x0 = max(0, min(b[0] for b in bboxes) - 90)
    y0 = max(0, min(b[1] for b in bboxes) - 90)
    crop = img[y0:max(b[3] for b in bboxes) + 90,
               x0:max(b[2] for b in bboxes) + 90].copy()
    for b in bboxes:
        cv2.rectangle(crop, (int(b[0]) - x0 - 8, int(b[1]) - y0 - 8),
                      (int(b[2]) - x0 + 8, int(b[3]) - y0 + 8), (255, 0, 255), 4)
    return crop


def elenca(img, bboxes):
    """[(classe, colore), ...] di tutto ciò che il VLM vede nel riquadro."""
    import read_holes as rh
    url = "data:image/png;base64," + base64.b64encode(
        cv2.imencode(".png", crop_riquadro(img, bboxes))[1]).decode()
    msgs = [{"role": "system", "content": ELENCO_SYS},
            {"role": "user", "content": [
                {"type": "text", "text": "Quali componenti vedi nel riquadro?"},
                {"type": "image_url", "image_url": {"url": url}}]}]
    try:
        raw, _ = rh.chat(msgs)
    except SystemExit:
        return []
    out = []
    for riga in raw.strip().splitlines():
        c, col = df._parse_cls_col(riga.strip("-*• ").lower())
        if c:
            out.append((c, col))
    return out


def arrivato(img, bboxes, state, posx):
    """(classe, colore) del pezzo nuovo. Riquadro pulito -> domanda standard."""
    vecchi = gia_nel_riquadro(bboxes[0], state, posx)
    if not vecchi:
        return df.vlm_arrived(img, bboxes, state)
    visti = elenca(img, bboxes)
    resto = collections.Counter(c for c, _ in visti) - collections.Counter(vecchi)
    nuovi = list(resto.elements())
    if len(nuovi) != 1:
        return df.vlm_arrived(img, bboxes, state)        # ripiego sulla domanda standard
    cls = nuovi[0]
    return cls, next((co for c, co in visti if c == cls and co), None)
