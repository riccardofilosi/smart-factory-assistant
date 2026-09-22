import os
import re
import json
import logging
import chromadb
from chromadb.utils import embedding_functions
from core.api_manager import GeminiKeyRotator, MODELLO_PREDEFINITO

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class KnowledgeBaseBuilder:
    """
    Legge il manuale in Markdown, processa le immagini (escludendo le foto reali)
    estraendo descrizioni dettagliate tramite Gemini Vision, effettua il chunking
    del testo e popola il database vettoriale ChromaDB locale.
    """
    def __init__(self, key_rotator: GeminiKeyRotator, db_path: str = "db_vector"):
        self.key_rotator = key_rotator
        self.db_path = db_path
        
        # Inizializza ChromaDB locale persistente
        self.chroma_client = chromadb.PersistentClient(path=self.db_path)
        
        # Usiamo l'embedding model di default di Chroma (all-MiniLM-L6-v2)
        # che è eccellente, locale (non consuma API key) e velocissimo.
        self.embedding_fn = embedding_functions.DefaultEmbeddingFunction()
        
        self.collection_name = "manuale_operativo"
        # Elimina la collezione se esiste già per ricrearla da zero
        try:
            self.chroma_client.delete_collection(self.collection_name)
        except Exception:
            pass
        self.collection = self.chroma_client.create_collection(
            name=self.collection_name, 
            embedding_function=self.embedding_fn
        )

    def _extract_image_description(self, image_path: str) -> str:
        """Usa Gemini 3.5 Flash per estrarre informazioni dall'immagine (es. schema funzionale)."""
        if not os.path.exists(image_path):
            logger.warning(f"Immagine non trovata: {image_path}")
            return "[Immagine non trovata]"

        chiave = os.path.basename(image_path)
        cache = getattr(self, "_cache_descrizioni", None)
        if cache is not None and chiave in cache:
            logger.info("Descrizione immagine da cache: %s", chiave)
            return cache[chiave]

        from PIL import Image
        try:
            img = Image.open(image_path)
            prompt = (
                "Sei un esperto di elettronica. Analizza questa immagine (uno schema funzionale o componente elettronico). "
                "Descrivi nel massimo dettaglio tecnico ciò che vedi. Se è una resistenza, elenca i colori delle bande e il valore. "
                "Se è uno schema, spiega i collegamenti tra i vari pin e componenti. "
                "Rispondi solo con la descrizione tecnica."
            )
            response = self.key_rotator.generate_content_with_retry(
                model=MODELLO_PREDEFINITO,
                contents=[prompt, img]
            )
            if cache is not None:
                cache[chiave] = response.text
            return response.text
        except Exception as e:
            logger.error(f"Errore durante l'analisi dell'immagine {image_path}: {str(e)}")
            return f"[Errore analisi immagine: {os.path.basename(image_path)}]"

    def _process_markdown_with_vision(self, markdown_text: str, base_dir: str) -> str:
        """
        Cerca i tag immagine markdown ![alt](path), li filtra, 
        e li sostituisce con il testo generato da Gemini.
        """
        # Regex per trovare ![alt](path)
        pattern = r"!\[.*?\]\((.*?)\)"
        matches = list(re.finditer(pattern, markdown_text))
        
        # Facciamo i replace al contrario per non sballare gli indici
        for match in reversed(matches):
            img_path_relative = match.group(1)
            img_path_abs = os.path.join(base_dir, img_path_relative)
            
            # Se l'utente ha rinominato il file sul disco aggiungendo 'foto_reale_'
            img_dir = os.path.dirname(img_path_abs)
            img_basename = os.path.basename(img_path_abs)
            renamed_abs_path = os.path.join(img_dir, f"foto_reale_{img_basename}")
            
            # Filtro Anti-Allucinazione: Ignora le foto reali delle breadboard (identificate dal nome sul disco o nel markdown)
            if "foto_reale" in img_path_relative.lower() or os.path.exists(renamed_abs_path):
                logger.info(f"Ignorata foto reale (Filtro Anti-Allucinazione): {img_basename}")
                replacement = "" # Rimuove l'immagine dal testo utile
            else:
                # E.g., schemi funzionali, componenti isolati
                logger.info(f"Analisi immagine (Vision-to-Text): {img_path_relative}")
                # Assicuriamoci che il file esista prima di analizzarlo
                if not os.path.exists(img_path_abs):
                    logger.warning(f"File non trovato: {img_path_abs}")
                    replacement = f"\n[Immagine mancante: {img_basename}]\n"
                else:
                    description = self._extract_image_description(img_path_abs)
                    replacement = f"\n[DESCRIZIONE IMMAGINE {img_basename}: {description}]\n"
                
            start, end = match.span()
            markdown_text = markdown_text[:start] + replacement + markdown_text[end:]
            
        return markdown_text

    def _chunk_text(self, text: str, max_caratteri: int = 4000) -> list[str]:
        """
        Chunking per titolo: un chunk = una sezione (H1) o sotto-sezione (H2).
        Le sezioni più lunghe di max_caratteri si dividono sui confini di riga,
        mai a metà di una riga di tabella o di un passo di montaggio.
        """
        chunks = []
        titolo_h1 = ""

        for sezione in re.split(r"\n(?=#{1,2}\s)", text):
            sezione = sezione.strip()
            if not sezione:
                continue

            prima_riga = sezione.split("\n", 1)[0]
            if prima_riga.startswith("# "):
                titolo_h1 = prima_riga[2:].strip()
            etichetta = titolo_h1 or prima_riga.lstrip("#").strip()

            if len(sezione) <= max_caratteri:
                chunks.append(self._con_etichetta(sezione, etichetta))
                continue

            corrente = []
            lunghezza = 0
            for riga in sezione.split("\n"):
                if corrente and lunghezza + len(riga) + 1 > max_caratteri:
                    chunks.append(self._con_etichetta("\n".join(corrente), etichetta))
                    corrente, lunghezza = [], 0
                corrente.append(riga)
                lunghezza += len(riga) + 1
            if corrente:
                chunks.append(self._con_etichetta("\n".join(corrente), etichetta))

        return chunks

    @staticmethod
    def _con_etichetta(chunk: str, etichetta: str) -> str:
        """
        Appiccica il titolo di appartenenza a ogni frammento che non sia la sezione H1
        stessa: le sotto-sezioni H2 e le code delle sezioni lunghe perderebbero
        altrimenti l'informazione su quale commessa stanno descrivendo.
        """
        chunk = chunk.strip()
        if not etichetta:
            return chunk
        prima_riga = chunk.split("\n", 1)[0]
        if prima_riga.startswith("# "):     # è già la sezione col suo titolo H1
            return chunk
        return f"[Appartiene a: {etichetta}]\n\n{chunk}"

    CACHE_DESCRIZIONI = "data/descrizioni_immagini.json"

    def _carica_cache(self) -> dict:
        try:
            with open(self.CACHE_DESCRIZIONI, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, ValueError):
            return {}

    def _salva_cache(self, cache: dict):
        with open(self.CACHE_DESCRIZIONI, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=1)

    def build_knowledge_base(self, sorgenti: dict):
        """
        sorgenti: {"manuale": "data/manuale_operativo.md", "guida_webapp": "data/guida_webapp.md"}
        La chiave finisce nei metadati come 'fonte'.
        """
        logger.info("Inizio costruzione Knowledge Base…")
        self._cache_descrizioni = self._carica_cache()

        documenti, metadati, identificativi = [], [], []
        for fonte, percorso in sorgenti.items():
            if not os.path.exists(percorso):
                logger.warning("Sorgente assente, la salto: %s", percorso)
                continue
            with open(percorso, encoding="utf-8") as f:
                raw_text = f.read()

            logger.info("Fase 1 (%s): pre-processing Vision-to-Text…", fonte)
            processed_text = self._process_markdown_with_vision(raw_text, os.path.dirname(percorso))

            logger.info("Fase 2 (%s): chunking per titolo…", fonte)
            chunks = self._chunk_text(processed_text)
            logger.info("%s: %d chunk.", fonte, len(chunks))

            for i, chunk in enumerate(chunks):
                documenti.append(chunk)
                metadati.append({"source": fonte, "fonte": fonte})
                identificativi.append(f"{fonte}_{i}")

        self._salva_cache(self._cache_descrizioni)

        if not documenti:
            raise RuntimeError("Nessuna sorgente valida: knowledge base non costruita.")

        logger.info("Fase 3: embedding e inserimento in ChromaDB (%d chunk)…", len(documenti))
        self.collection.add(documents=documenti, metadatas=metadati, ids=identificativi)
        logger.info("Costruzione Knowledge Base completata.")

