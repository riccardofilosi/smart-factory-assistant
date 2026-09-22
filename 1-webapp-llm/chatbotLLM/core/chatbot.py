# -*- coding: utf-8 -*-
import os
import logging

from google.genai import types

from core.api_manager import MODELLO_PREDEFINITO

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Sei l'assistente operativo di Electronics, l'applicazione che guida l'operatore "
    "nella realizzazione delle commesse elettroniche su breadboard. Aiuti durante il "
    "picking, l'assemblaggio, il controllo qualità, il collaudo e la chiusura della commessa.\n"
    "\n"
    "ARGOMENTI DI TUA COMPETENZA:\n"
    "a) elettronica, componenti e contenuti del manuale operativo;\n"
    "b) uso della webapp: le fasi del processo, cosa fa ogni schermata, cosa succede a un "
    "controllo qualità negativo, come si stampa l'etichetta, come si chiude una commessa, il fine turno;\n"
    "c) significato dei termini di processo (commessa, picking, rework, codice difetto DEF-xx);\n"
    "d) concetti base di elettronica e circuiti: come funziona la breadboard, cos'è un circuito, la "
    "legge di Ohm, le leggi di Kirchhoff, serie e parallelo, polarità, alimentazione, a cosa serve un "
    "componente. Qualunque domanda sensata di elettronica rientra qui: non escludere un concetto a priori.\n"
    "\n"
    "REGOLE FONDAMENTALI (Guardrails):\n"
    "1. Rifiuta solo ciò che è davvero fuori dominio (fisica atomica o quantistica, chimica, "
    "matematica pura) o fuori tema (ricette, politica, sport, programmazione non legata a queste "
    "commesse, meteo...): in quei casi rispondi ESATTAMENTE con: 'Sono un assistente dedicato "
    "esclusivamente al processo di realizzazione delle commesse elettroniche. Non posso aiutarti "
    "con questa richiesta.'\n"
    "2. DUE CLASSI DI RISPOSTA. (a) Domanda OPERATIVA (dove va un componente, con quale verso o "
    "polarità, in quale foro di una commessa): basati ESCLUSIVAMENTE sul contesto fornito qui sotto; "
    "se il dato non c'è, dillo ('il manuale non lo specifica') e indica dove cercarlo (il passo di "
    "montaggio o il capo turno), NON dedurre, NON stimare, NON proporre alternative. (b) Domanda "
    "CONCETTUALE (come funziona X, cos'è un principio o una legge): spiega pure. Se il tema è coperto "
    "dal contesto dei FONDAMENTI, usalo; altrimenti usa la tua conoscenza generale di elettronica, "
    "ma sempre in forma leggera.\n"
    "3. STILE delle risposte concettuali: conciso, a parole, calibrato sulla domanda. Un accenno "
    "sensato al principio, non una lezione. NIENTE formule di default: la formula (es. V = R × I) si "
    "fornisce SOLO se l'operatore la chiede esplicitamente. Niente derivazioni né dettaglio matematico.\n"
    "4. REGOLA DI POSIZIONAMENTO E STANDARDIZZAZIONE: lo scopo dell'assistente, oltre a far "
    "funzionare il circuito, è la standardizzazione delle commesse. Quando l'operatore chiede DOVE "
    "va un componente, con quale verso o polarità, usa ESCLUSIVAMENTE il blocco 'MONTAGGIO "
    "PASSO-PASSO' della commessa. NON usare lo 'SCHEMA ELETTRICO' né i concetti generali per proporre "
    "posizionamenti alternativi: la spiegazione teorica non può mai diventare fonte di un posizionamento.\n"
    "5. OFFERTA DI APPROFONDIMENTO: se una domanda operativa ha anche un risvolto concettuale, "
    "rispondi prima sull'operativo (dove/verso/foro), poi CHIEDI se l'operatore vuole capire anche il "
    "principio, con questa formula: 'Vuoi che ti spieghi anche il principio di funzionamento?'. Non "
    "imporre la teoria: aprila solo su richiesta.\n"
    "6. Se il contesto contiene uno STATO OPERATORE, usalo per risolvere le domande che si "
    "riferiscono alla situazione corrente ('questo', 'qui', 'questa resistenza', 'il prossimo'): "
    "si riferiscono al passo corrente indicato. Se non c'è un passo corrente e la domanda è "
    "ambigua, chiedi di quale componente o commessa si tratta.\n"
    "7. Riporta le coordinate dei fori esattamente come sono scritte nel manuale (esempio: g7, "
    "e32, b23 → b32). Non riscriverle, non convertirle, non arrotondarle.\n"
    "8. Il nome dell'applicazione è 'Electronics': non usare mai la dicitura 'Smart Factory' nelle "
    "tue risposte, nemmeno se compare nel contesto.\n"
    "9. Rivolgiti a una persona che non ha MAI montato un circuito su breadboard. Sii chiaro e "
    "paziente: spiega in una riga i termini tecnici la prima volta che li usi (anodo, catodo, "
    "polarità, fessura centrale, rail di alimentazione), indica sempre il foro esatto e il verso, "
    "e non dare per scontato alcun passaggio. Meglio una frase in più che lasciare l'operatore "
    "nel dubbio. Resta comunque fedele al manuale: la pazienza non è licenza per inventare "
    "posizionamenti. Rispondi in italiano."
)


class ElectronicAssistant:
    """
    Motore del chatbot. Senza stato: la memoria della conversazione è del chiamante
    (SessionStore lato server, lista locale lato CLI).
    """

    def __init__(self, key_rotator, context_builder):
        self.key_rotator = key_rotator
        self.context_builder = context_builder
        # Modello unico di progetto, pinned in core/api_manager.py.
        self.model_name = os.getenv("GEMINI_MODEL", MODELLO_PREDEFINITO)
        self.system_prompt = SYSTEM_PROMPT

    @staticmethod
    def _storico(history) -> str:
        if not history:
            return ""
        righe = ["--- STORICO CONVERSAZIONE (ultime interazioni) ---"]
        for domanda, risposta in history:
            # Difensivo: uno scambio potrebbe contenere un valore non-stringa.
            domanda = str(domanda) if domanda is not None else ""
            risposta = str(risposta) if risposta is not None else ""
            d = domanda[:500] + "..." if len(domanda) > 500 else domanda
            r = risposta[:500] + "..." if len(risposta) > 500 else risposta
            righe.append(f"Utente: {d}")
            righe.append(f"Assistente: {r}")
        righe.append("---------------------------------------------------")
        return "\n".join(righe)

    def costruisci_prompt(self, query_text=None, contesto=None, history=None) -> str:
        ricerca = query_text or "Domanda posta a voce dall'operatore."
        blocco_contesto = self.context_builder.build(ricerca, contesto)

        parti = [self.system_prompt]
        storico = self._storico(history)
        if storico:
            parti.append(storico)
        if blocco_contesto:
            parti.append(blocco_contesto)
        parti.append(
            f"Domanda dell'operatore: {query_text}" if query_text
            else "Domanda dell'operatore: [Audio in allegato]"
        )
        parti.append("Risposta:")
        return "\n\n".join(parti)

    def trascrivi(self, audio_file_path: str) -> str:
        """Trascrive la domanda vocale in testo, con lo stesso modello.

        La voce è dettatura, non un canale verso il modello. L'audio viene
        trascritto e la trascrizione finisce nel campo
        di chat della webapp; l'operatore la rilegge, la corregge e decide se
        inviarla — il chatbot riceve solo domande testuali. Tre ragioni:
        (1) un errore di trascrizione non può propagarsi da solo a un montaggio
        sbagliato, perché passa sempre dal controllo dell'operatore;
        (2) la ricerca — ibrida e deterministica — lavora sul testo reale
        della domanda (con l'audio nativo la query restava vuota: né sigle
        riconosciute né query esatta in ChromaDB); (3) la memoria di sessione
        registra la domanda vera invece del segnaposto "[Richiesta Audio]".
        Ritorna "" se la trascrizione fallisce.
        """
        if not audio_file_path or not os.path.exists(audio_file_path):
            return ""
        try:
            with open(audio_file_path, "rb") as f:
                audio_data = f.read()
            mime_type = "audio/mp3"
            if audio_file_path.lower().endswith(".wav"):
                mime_type = "audio/wav"
            elif audio_file_path.lower().endswith(".ogg"):
                mime_type = "audio/ogg"
            risposta = self.key_rotator.generate_content_with_retry(
                model=self.model_name,
                contents=[
                    "Trascrivi esattamente, parola per parola e in italiano, la "
                    "domanda contenuta nell'audio. Rispondi SOLO con la "
                    "trascrizione, senza virgolette né commenti.",
                    types.Part.from_bytes(data=audio_data, mime_type=mime_type),
                ],
            )
            return (risposta.text or "").strip()
        except Exception as e:
            logger.error("Errore trascrizione audio: %s", e)
            return ""

    def ask(self, query_text=None, audio_file_path=None, contesto=None, history=None) -> str:
        if not query_text and not audio_file_path:
            return "Errore: fornire un testo o un file audio."

        contents = [self.costruisci_prompt(query_text, contesto, history)]

        if audio_file_path:
            if os.path.exists(audio_file_path):
                logger.info("Caricamento audio multimodale: %s", audio_file_path)
                try:
                    with open(audio_file_path, "rb") as f:
                        audio_data = f.read()
                    mime_type = "audio/mp3"
                    if audio_file_path.lower().endswith(".wav"):
                        mime_type = "audio/wav"
                    elif audio_file_path.lower().endswith(".ogg"):
                        mime_type = "audio/ogg"
                    contents.append(types.Part.from_bytes(data=audio_data, mime_type=mime_type))
                except Exception as e:
                    logger.error("Errore lettura audio: %s", e)
            else:
                logger.warning("File audio non trovato: %s", audio_file_path)

        try:
            logger.info("Invocazione %s…", self.model_name)
            risposta = self.key_rotator.generate_content_with_retry(
                model=self.model_name,
                contents=contents,
            )
            # .text può essere None (risposta bloccata dai filtri o vuota): restituiamo
            # sempre una stringa, così la memoria della sessione non si corrompe.
            return risposta.text or "Non sono riuscito a formulare una risposta. Riprova o riformula la domanda."
        except Exception as e:
            logger.error("Errore durante l'elaborazione del Chatbot: %s", e)
            return "Errore interno del sistema di assistenza."
