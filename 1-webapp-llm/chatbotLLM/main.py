import os
import sys
import logging
import queue
import sounddevice as sd
import soundfile as sf
from core.api_manager import GeminiKeyRotator
from core.document_processor import KnowledgeBaseBuilder
from core.rag_engine import RAGRetriever
from core.chatbot import ElectronicAssistant

def setup_logging():
    logging.basicConfig(level=logging.INFO)

def record_audio_until_enter(filename="data/temp_audio.wav", samplerate=44100):
    q = queue.Queue()
    def callback(indata, frames, time, status):
        q.put(indata.copy())
    
    print("\n[🎙️  Microfono aperto: Registrazione in corso... Parla ora. Premi INVIO per interrompere ed inviare]")
    with sf.SoundFile(filename, mode='w', samplerate=samplerate, channels=1) as file:
        with sd.InputStream(samplerate=samplerate, channels=1, callback=callback):
            input() # Attende che l'utente prema INVIO
            while not q.empty():
                file.write(q.get())
    return filename

def main():
    setup_logging()
    print("=== Inizializzazione Assistente LLM Elettronico (Gemini 3.5 Flash) ===")
    
    # 1. Inizializza il gestore delle chiavi
    try:
        key_rotator = GeminiKeyRotator(keys_file_path="data/keys.txt")
    except Exception as e:
        print(f"Errore caricamento chiavi: {e}")
        sys.exit(1)

    # 2. Costruzione automatica della Knowledge Base (solo se non esiste)
    db_path = "db_vector"
    if not os.path.exists(db_path) or not os.listdir(db_path):
        print("\n[Prima Esecuzione] Database vettoriale non trovato.")
        print("Costruzione Knowledge Base in corso... (Questa operazione utilizzerà Gemini Vision per analizzare le immagini, potrebbe richiedere del tempo).")
        kb_builder = KnowledgeBaseBuilder(key_rotator=key_rotator, db_path=db_path)
        try:
            kb_builder.build_knowledge_base({
                "manuale": "data/manuale_operativo.md",
                "guida_webapp": "data/guida_webapp.md",
            })
        except Exception as e:
            print(f"Errore critico durante la costruzione della KB: {e}")
            sys.exit(1)
    else:
        print("\nDatabase vettoriale esistente trovato. Salto la fase di costruzione.")

    # 3. Avvia i motori RAG e Chatbot
    print("\nAvvio motore RAG e Chatbot...")
    rag_engine = RAGRetriever()

    from core.manuale import Manuale
    from core.context_builder import ContextBuilder

    manuale = Manuale("data/manuale_operativo.md")
    assistant = ElectronicAssistant(key_rotator, ContextBuilder(manuale, rag_engine))
    storico = []          # sliding window a 12 scambi, come il server (vedi server.py)
    MAX_SCAMBI = 12

    print("\n--- Sistema Pronto ---")
    print("Digita la tua domanda, oppure digita '1' per parlare al microfono. Digita 'esci' per terminare.")
    while True:
        try:
            user_input = input("\nOperatore (scrivi '1' e invia per registrare, oppure digita testo): ")
            if user_input.lower() in ['esci', 'quit', 'exit']:
                print("Chiusura del sistema...")
                break
                
            if user_input.strip() == '1':
                audio_path = record_audio_until_enter()
                print("Analisi dell'audio in corso tramite Gemini Multimodale...")
                # Chiama l'assistente usando nativamente il file audio appena creato
                response = assistant.ask(audio_file_path=audio_path, history=storico)
            elif user_input.strip():
                response = assistant.ask(query_text=user_input, history=storico)
            else:
                continue

            print(f"\nAssistente LLM:\n{response}")

            storico.append((user_input if user_input else "[Richiesta Audio]", response))
            del storico[:-MAX_SCAMBI]

        except KeyboardInterrupt:
            print("\nChiusura del sistema...")
            break
        except Exception as e:
            print(f"\nErrore inaspettato: {e}")

if __name__ == "__main__":
    main()
