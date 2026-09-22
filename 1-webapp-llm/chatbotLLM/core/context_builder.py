# -*- coding: utf-8 -*-
"""
Costruisce il blocco di contesto da mettere nel prompt per ogni domanda.

Principio: se la commessa è nota, la sua scheda entra SEMPRE per intero e per via
deterministica. Il RAG vettoriale interviene solo sul residuo (guida webapp,
componenti, altre commesse) ed è una rete di sicurezza, non la fonte primaria.
"""
import logging
import re

logger = logging.getLogger(__name__)

RE_ID_COMMESSA = re.compile(r"\b([FD][1-5])\b")

FASI = {
    "T0": "T0 · Lista commesse",
    "T1": "T1 · Picking componenti",
    "T2": "T2 · Assemblaggio",
    "QC": "QC · Controllo qualità",
    "T3": "T3 · Collaudo",
    "T4": "T4 · Codice e stampa etichetta",
}


class ContextBuilder:
    def __init__(self, manuale, rag_engine=None):
        self.manuale = manuale
        self.rag_engine = rag_engine

    @staticmethod
    def _dizionario(contesto):
        return contesto if isinstance(contesto, dict) else {}

    def commessa_attiva(self, domanda, contesto=None) -> str | None:
        """La commessa nominata nella domanda vince su quella aperta nella webapp."""
        for cid in RE_ID_COMMESSA.findall((domanda or "").upper()):
            if self.manuale.sezione_commessa(cid):
                return cid

        cid = self._dizionario(contesto).get("commessa")
        if isinstance(cid, str) and self.manuale.sezione_commessa(cid):
            return cid.strip().upper()
        return None

    def _stato_operatore(self, cid, contesto) -> str:
        dati = self._dizionario(contesto)
        righe = []

        fase = dati.get("fase")
        if isinstance(fase, str) and fase:
            righe.append(f"Fase corrente: {FASI.get(fase.upper(), fase)}")
        if cid:
            righe.append(f"Commessa aperta: {cid}")

        numero = dati.get("passo")
        if cid and isinstance(numero, int) and not isinstance(numero, bool):
            totale = dati.get("passoTot")
            if not isinstance(totale, int) or isinstance(totale, bool):
                totale = len(self.manuale.passi(cid))
            righe.append(f"Passo corrente: {numero} di {totale}")
            testo = self.manuale.passo(cid, numero)
            if testo:
                righe.append(f'Testo del passo corrente: "{testo}"')

        if not righe:
            return ""
        return "--- STATO OPERATORE ---\n" + "\n".join(righe)

    def build(self, domanda, contesto=None) -> str:
        cid = self.commessa_attiva(domanda, contesto)
        parti = []

        stato = self._stato_operatore(cid, contesto)
        if stato:
            parti.append(stato)

        if cid:
            parti.append(
                f"--- SCHEDA COMMESSA {cid} (fonte di verità, ha la precedenza su tutto il resto) ---\n"
                f"{self.manuale.sezione_commessa(cid)}"
            )

        convenzioni = self.manuale.convenzioni()
        if convenzioni:
            parti.append(f"--- CONVENZIONI GLOBALI ---\n{convenzioni}")

        if self.rag_engine and domanda:
            try:
                extra = self.rag_engine.retrieve_context(domanda, n_results=8)
            except Exception as e:
                logger.warning("RAG non disponibile, si procede col contesto deterministico: %s", e)
                extra = ""
            if extra and extra.strip():
                parti.append(f"--- ALTRO CONTESTO (manuale e guida webapp) ---\n{extra.strip()}")

        return "\n\n".join(parti)
