import mammoth
from markdownify import markdownify
import os
import shutil
import base64
import uuid

# Creazione cartella immagini se non esiste
image_dir = "data/images"
os.makedirs(image_dir, exist_ok=True)

# Funzione per salvare le immagini gestita da mammoth
def convert_image(image):
    with image.open() as image_bytes:
        # Crea un nome file univoco. Se è una foto vera, nel processo
        # futuro l'operatore potrà rinominare i file in `foto_reale_*`
        # Per ora diamo un nome progressivo
        content = image_bytes.read()
        extension = image.content_type.split("/")[1]
        
        # Sostituiamo jpeg con jpg
        if extension == "jpeg":
            extension = "jpg"
            
        # Generiamo un ID univoco per evitare collisioni, 
        # oppure diamo un nome sequenziale
        file_name = f"immagine_{uuid.uuid4().hex[:8]}.{extension}"
        file_path = os.path.join(image_dir, file_name)
        
        with open(file_path, "wb") as f:
            f.write(content)
            
        # Ritorna il dizionario con l'attributo src da inserire nel tag <img>
        # Mettiamo il percorso relativo che useremo poi
        return {"src": f"images/{file_name}"}

docx_path = "Manuale Operativo.docx"   # sorgente Word del manuale (non versionato)
output_md_path = "data/manuale_operativo.md"

if not os.path.exists(docx_path):
    print(f"Errore: Il file '{docx_path}' non è stato trovato!")
    exit(1)

print(f"Lettura del file '{docx_path}' in corso...")

try:
    with open(docx_path, "rb") as docx_file:
        result = mammoth.convert_to_html(
            docx_file, 
            convert_image=mammoth.images.inline(convert_image)
        )
        html_content = result.value
        messages = result.messages
        for message in messages:
            print(f"Avviso Mammoth: {message}")

    print("Conversione HTML completata. Conversione in Markdown...")
    # Markdownify converte html in markdown e preserva img tag o li trasforma in markdown image tag
    md_content = markdownify(html_content, heading_style="ATX")
    
    with open(output_md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
        
    print(f"Conversione completata con successo! File Markdown salvato in '{output_md_path}'")
    print(f"Le immagini sono state estratte in '{image_dir}'")
    
except Exception as e:
    print(f"Si è verificato un errore durante la conversione: {e}")
