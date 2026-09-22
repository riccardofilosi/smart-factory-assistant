# GUIDA WEBAPP — Introduzione

L'applicazione si chiama Electronics e accompagna l'operatore lungo tutto il turno, una schermata per fase. Il percorso di ogni commessa è sempre lo stesso: accesso con il proprio codice operatore, scelta della commessa dalla lista, prelievo dei componenti (picking), montaggio passo-passo sulla breadboard, controllo qualità, collaudo funzionale, e infine generazione del codice di destinazione con stampa dell'etichetta.

Una commessa si considera chiusa quando l'etichetta viene stampata (esito positivo) oppure quando viene dichiarata fallita. Quando tutte le commesse del turno sono chiuse, il turno finisce e ne inizia uno nuovo. L'assistente è disponibile in ogni fase per rispondere a domande sui componenti, sui posizionamenti, sul collaudo e sull'uso dell'app stessa.


# GUIDA WEBAPP — Accesso operatore

**A cosa serve.** È la prima schermata: identifica chi sta lavorando, così ogni commessa e ogni turno risultano associati all'operatore giusto.

**Cosa vede l'operatore.** Un campo per il codice operatore (ad esempio OP1, OP2 o OP3) e uno per il PIN personale.

**Cosa può fare.** Inserire codice e PIN ed entrare. Una volta dentro, si arriva alla lista delle commesse del turno.

**Se qualcosa va storto.** Se il codice o il PIN non vengono accettati, si controlla di aver scritto il codice operatore corretto e il PIN giusto. Se il problema resta, ci si rivolge al capo turno: le credenziali non si possono recuperare dall'app.


# GUIDA WEBAPP — T0 Lista commesse

**A cosa serve.** È la schermata iniziale del turno: mostra tutte le commesse da lavorare e permette di sceglierne una.

**Cosa vede l'operatore.** L'elenco delle commesse del turno, ognuna con il proprio codice (le F sono le facili, le D le difficili), il nome, la città di destinazione assegnata e lo stato (da fare, oppure OK o KO se già chiusa). In alto sono indicati l'operatore, la data e il numero del turno. Le commesse già chiuse restano visibili ma non si possono riaprire.

**Cosa può fare.** Toccare una commessa ancora da fare per iniziarla: si passa al picking dei suoi componenti. Le commesse si possono affrontare nell'ordine che si preferisce.

**Se qualcosa va storto.** Se una commessa risulta già chiusa per errore, non si può riaprire nello stesso turno; va segnalato al capo turno. Se una commessa era stata iniziata e interrotta, riprendendola si riparte dal punto in cui era rimasta.


# GUIDA WEBAPP — T1 Picking componenti

**A cosa serve.** Prelevare dal kit tutti i componenti che servono per la commessa scelta, prima di iniziare il montaggio.

**Cosa vede l'operatore.** La distinta base della commessa: l'elenco dei componenti con la foto di ciascuno, il codice, il nome e la quantità da prelevare. In alto sono riepilogati quanti componenti e quanti pezzi totali servono.

**Cosa può fare.** Confrontare ogni componente reale con la sua foto, prelevarne la quantità indicata, e poi proseguire verso l'assemblaggio. Si può anche tornare indietro alla lista commesse.

**Se qualcosa va storto.** Se un componente del kit non corrisponde alla foto o alla descrizione, conviene chiedere all'assistente il dettaglio di quel componente, oppure rivolgersi al capo turno. Non si sostituisce mai un componente con uno di valore diverso: ogni commessa ha una distinta base precisa.


# GUIDA WEBAPP — T2 Assemblaggio

**A cosa serve.** Guida il montaggio del circuito sulla breadboard un passo alla volta, nell'ordine corretto, con il foro esatto e il verso di ogni componente.

**Cosa vede l'operatore.** Un passo per volta: il componente da inserire, i fori in cui va (notazione riga-colonna, per esempio g7) e, quando serve, il verso o la polarità da rispettare. In cima è indicato a che punto si è (per esempio "Passo 3 di 11").

**Cosa può fare.** Montare il componente del passo corrente, poi avanzare al passo successivo. Ogni passo resta bloccato per circa due secondi e mezzo prima che si possa proseguire: è una pausa voluta, serve a dare il tempo di leggere e posizionare con calma. Si avanza con il pulsante Avanti o con la freccia destra della tastiera; si torna a un passo già visto con la freccia sinistra o con il pulsante Indietro. Non si può saltare a un passo che non si è ancora raggiunto: si procede in ordine. La posizione viene salvata, quindi se si esce e si rientra nella commessa si riprende dallo stesso passo.

**Se qualcosa va storto.** Se non si riesce ad avanzare, di solito non sono ancora passati i due secondi e mezzo del passo corrente: basta attendere un istante. Se si ha un dubbio su dove va un componente o su come orientarlo, si chiede all'assistente mentre si è sul passo: conosce la commessa e il punto in cui ci si trova. Se ci si accorge di aver sbagliato un passo precedente, si torna indietro con la freccia sinistra e si sistema.


# GUIDA WEBAPP — Controllo qualità

**A cosa serve.** Verificare che il circuito montato sia corretto prima di passare al collaudo funzionale.

**Cosa vede l'operatore.** L'esito del controllo qualità sulla commessa appena assemblata.

**Cosa può fare.** Se il controllo dà esito positivo, si prosegue verso il collaudo. Se dà esito negativo, si torna alla schermata di assemblaggio per correggere il montaggio (rework) e poi si ripete il controllo.

**Se qualcosa va storto.** Un esito negativo non è un errore dell'app: significa che qualcosa nel montaggio va rivisto. Si torna all'assemblaggio, si ripercorrono i passi confrontandoli con le indicazioni, si corregge, e si riprova. L'assistente può aiutare a capire quali difetti tipici cercare per quella commessa.


# GUIDA WEBAPP — T3 Collaudo

**A cosa serve.** Provare che il circuito funzioni davvero, alimentandolo e osservando il comportamento atteso.

**Cosa vede l'operatore.** La funzione attesa della commessa (cosa deve succedere), la procedura di collaudo di base comune a tutte, la procedura specifica di quella commessa, i punti da controllare sui posizionamenti e l'elenco dei difetti tipici. È mostrata anche la foto del circuito finito di riferimento.

**Cosa può fare.** Alimentare il circuito con la batteria da 6V (quattro pile AA), eseguire la prova descritta e confrontare il comportamento con quello atteso. Se funziona, si conferma l'esito positivo e si passa alla generazione del codice. Se non funziona, si può dichiarare la commessa fallita oppure tornare all'assemblaggio per correggere.

**Se qualcosa va storto.** Se il comportamento non è quello atteso, si consulta l'elenco dei difetti tipici della commessa: spesso è un componente invertito, un transistor con il verso sbagliato o una gamba non a fondo. L'assistente può guidare nella diagnosi. Se il difetto non è recuperabile, si chiude la commessa come fallita.


# GUIDA WEBAPP — T4 Codice e stampa etichetta

**A cosa serve.** Generare il codice di destinazione della commessa completata e stampare l'etichetta di spedizione.

**Cosa vede l'operatore.** La città di destinazione assegnata alla commessa, il suo codice a sette cifre e l'anteprima dell'etichetta con i dati di spedizione.

**Cosa può fare.** Controllare i dati, aprire l'anteprima di stampa e stampare l'etichetta. La stampa chiude la commessa con esito positivo e riporta alla lista commesse. Si può anche annullare l'anteprima senza stampare: in quel caso la commessa resta aperta.

**Se qualcosa va storto.** Se i dati di destinazione sembrano errati, si può annullare senza stampare e segnalare al capo turno. Finché non si stampa, la commessa non viene chiusa.


# GUIDA WEBAPP — Assistente

**A cosa serve.** È l'aiuto sempre a portata di mano: risponde a domande sul manuale, sui componenti, su ogni commessa e sull'uso dell'app.

**Cosa vede l'operatore.** Un pannello richiudibile che si apre da qualsiasi schermata, con lo storico della conversazione e un campo per scrivere.

**Cosa può fare.** Porre domande scritte oppure a voce, in qualsiasi fase. Poiché l'assistente sa quale commessa è aperta e a che passo ci si trova, si possono fare domande dirette come "dove va questo componente?" o "come lo oriento?" senza dover ripetere ogni volta di quale commessa si parla.

**Se qualcosa va storto.** Se l'assistente non conosce la risposta, lo dice chiaramente e indica dove cercarla (il passo del montaggio o il capo turno): non inventa mai posizionamenti. Per domande fuori tema rispetto al lavoro, l'assistente non risponde: è dedicato solo al processo di realizzazione delle commesse. L'assistente è un supporto, non sostituisce il giudizio del capo turno.


# GUIDA WEBAPP — Fine turno

**A cosa serve.** Segnala che tutte le commesse del turno sono state chiuse e prepara il turno successivo.

**Cosa vede l'operatore.** Il riepilogo del turno appena concluso, con quante commesse sono andate a buon fine e quante sono state dichiarate fallite.

**Cosa può fare.** Prendere atto del riepilogo. Rientrando, l'operatore trova un nuovo turno con le commesse di nuovo da fare e nuove città di destinazione assegnate.

**Se qualcosa va storto.** Se il turno risulta concluso prima del previsto, significa che tutte le commesse sono già state chiuse (con esito positivo o fallito). Eventuali anomalie sul conteggio vanno segnalate al capo turno.
