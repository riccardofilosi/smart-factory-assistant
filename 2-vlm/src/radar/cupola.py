"""cupola.py — verifica del LED per copertura della cupola.

Ramo DISATTIVATO (USA_CUPOLA = False in radar_operativa). Resta nel repo come
risultato negativo documentato, per non ripetere l'esperimento.

Misura su 30 step LED con ground truth (49 capi):

    NCC sul foro vero      mediana 0.20    sopra 0.35: 15/49
    NCC a una colonna      mediana 0.05    sopra 0.35: 18/49

Il foro accanto supera la soglia più spesso di quello giusto: sul LED l'NCC per
foro non è debole, è fuorviante. Dentro ai numeri c'è però una struttura stabile:
un capo caldissimo (sotto la cupola, che lo copre) e uno freddo (la gamba nuda).

Regola provata: un capo si verifica per copertura, l'altro si deduce dalla
campata dichiarata dal golden. Il ramo non accusa mai: se la cupola non si vede
(14 step su 29) il verdetto resta alla logica standard.

Perché è stata scartata: sui montaggi corretti passava da 2/23 a 13/23 conferme,
ma sui montaggi sbagliati dichiarava OK 2/3 invece di 0/3. Per un controllo
qualità è il baratto peggiore. La causa è fisica: la cupola è larga quasi due
passi e copre i fori del golden anche quando il LED sta altrove.

    golden       reale        NCC sui fori del golden
    h12,h15      h13,h18      h12=0.44   h15=0.62
    j15          i15          j15=0.41

Né il massimo (troppo permissivo) né il minimo (il capo nudo vale 0.00-0.24, non
passerebbe mai) separano i due casi: il LED non è verificabile per copertura.
"""
T_LED = 0.10       # ramo LED su soli NCC dei fori golden: sopra = capo acceso
T_LED_MUTO = 0.05  # sotto = foro vuoto. Misurato: led corretto 0.55/0.71, led
                   # volutamente errato 0.003; le soglie stanno nel vuoto fra i due.


def verifica_led(S, attesi):
    """LED verificato sui soli NCC dei fori golden, senza ricerca libera dei pin
    (misurato: la ricerca libera agganciava la resistenza vicina). Un capo sta sotto
    la cupola (caldo), l'altro è nudo ma il suo foro si accende comunque all'ingresso.
        CONFERMATO   entrambi i golden >= T_LED
        SMENTITO     almeno un golden sotto T_LED_MUTO (foro vuoto)
        INCERTO      terra di mezzo: decide lo strato ancorato
    Ritorna (livello, dettaglio)."""
    if not attesi:
        return None
    vals = {uv: S.get(uv, 0.0) for uv in attesi}
    det = " ".join(f"{u},{v}={x:.2f}" for (u, v), x in vals.items())
    if min(vals.values()) >= T_LED:
        return "CONFERMATO", f"entrambi i capi golden accesi ({det})"
    if min(vals.values()) < T_LED_MUTO:
        return "SMENTITO", f"un foro golden e' VUOTO ({det})"
    return "INCERTO", det


FAM_CUPOLA = {"led"}

T_CUPOLA = 0.35    # NCC sopra cui il foro è coperto dalla cupola (stessa soglia del ramo corpo)


def verifica(S, attesi):
    """Ritorna (livello, dettaglio) oppure None se la cupola non si aggancia.
         CUPOLA   almeno un capo è sotto la cupola: l'altro segue per campata
    """
    if not attesi:
        return None
    caldi = [(S.get(uv, 0.0), uv) for uv in attesi]
    v, uv = max(caldi)
    if v < T_CUPOLA:
        return None                       # cupola non visibile: nessun verdetto
    altri = [f"{a},{b}={S.get((a, b), 0.0):.2f}" for a, b in attesi if (a, b) != uv]
    det = f"cupola su {uv[0]},{uv[1]} ({v:.2f})"
    if altri:
        det += f"; l'altro capo e' dedotto dalla campata del golden ({', '.join(altri)})"
    return "CUPOLA", det
