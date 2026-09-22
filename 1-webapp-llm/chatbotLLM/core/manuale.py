# -*- coding: utf-8 -*-
"""
Indice del manuale operativo.

Unica fonte di verità per commesse, passi di montaggio e convenzioni.
Non si usa golden-data.json: è un mirror del manuale per webapp/VLM, non la fonte.
"""
import re

# "# FACILE F1 — ..." / "# DIFFICILE D4 — ..."  →  F1 / D4
RE_COMMESSA = re.compile(r"^(?:FACILE|DIFFICILE)\s+([FD][1-5])\b")
# Riga in grassetto isolata: "**MONTAGGIO PASSO-PASSO**"
RE_BLOCCO = re.compile(r"^\*\*(.+?)\*\*\s*$", re.M)
# Passo di montaggio: "9. **Cavetto arancione** b23 → b32 *— nota*"
RE_PASSO = re.compile(r"^(\d+)\.\s+(.*)$", re.M)


class Manuale:
    """Carica il manuale una volta e lo indicizza per titolo H1."""

    def __init__(self, percorso: str = "data/manuale_operativo.md"):
        self.percorso = percorso
        with open(percorso, encoding="utf-8") as f:
            testo = f.read()

        self._sezioni: dict[str, str] = {}   # titolo H1 → testo integrale della sezione
        self._commesse: dict[str, str] = {}  # "F1" → testo integrale della scheda

        for blocco in re.split(r"\n(?=# )", testo):
            prima_riga = blocco.split("\n", 1)[0]
            if not prima_riga.startswith("# "):
                continue
            titolo = prima_riga[2:].strip()
            self._sezioni[titolo] = blocco.strip()
            trovata = RE_COMMESSA.match(titolo)
            if trovata:
                self._commesse[trovata.group(1)] = blocco.strip()

    def commesse(self) -> list[str]:
        return sorted(self._commesse)

    def sezione_commessa(self, id_commessa) -> str | None:
        if not id_commessa:
            return None
        return self._commesse.get(str(id_commessa).strip().upper())

    def convenzioni(self) -> str:
        for titolo, testo in self._sezioni.items():
            if titolo.startswith("SEZIONE 0"):
                return testo
        return ""

    def blocco(self, id_commessa, nome: str) -> str | None:
        """
        Testo di un blocco della scheda (es. "MONTAGGIO PASSO-PASSO"), dal titolo
        fino al titolo successivo. Il titolo esatto vince sul titolo che inizia
        per lo stesso testo: nel manuale convivono "COLLAUDO" e
        "COLLAUDO/ CONTROLLO QUALITÀ: ...".
        """
        sezione = self.sezione_commessa(id_commessa)
        if not sezione:
            return None

        intestazioni = list(RE_BLOCCO.finditer(sezione))
        cercato = nome.strip().upper()

        def estrai(indice: int) -> str:
            inizio = intestazioni[indice].end()
            fine = intestazioni[indice + 1].start() if indice + 1 < len(intestazioni) else len(sezione)
            return sezione[inizio:fine].strip()

        for i, m in enumerate(intestazioni):
            if m.group(1).strip().upper() == cercato:
                return estrai(i)
        for i, m in enumerate(intestazioni):
            if m.group(1).strip().upper().startswith(cercato):
                return estrai(i)
        return None

    def passi(self, id_commessa) -> list[str]:
        montaggio = self.blocco(id_commessa, "MONTAGGIO PASSO-PASSO")
        if not montaggio:
            return []
        return [testo.strip() for _numero, testo in RE_PASSO.findall(montaggio)]

    def passo(self, id_commessa, n) -> str | None:
        if not isinstance(n, int) or isinstance(n, bool):
            return None
        elenco = self.passi(id_commessa)
        if n < 1 or n > len(elenco):
            return None
        return elenco[n - 1]
