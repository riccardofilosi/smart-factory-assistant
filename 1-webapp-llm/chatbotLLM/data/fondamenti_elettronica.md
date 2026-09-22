# Fondamenti di elettronica

Base per principianti, ancorata a questo progetto. Spiega i concetti a parole, senza matematica.
Le formule si danno solo se l'operatore le chiede esplicitamente.

# La breadboard: righe, colonne e fessura

La breadboard è la basetta forata su cui si monta il circuito senza saldare: i componenti si
infilano nei fori e restano collegati fra loro dalle piste metalliche interne.
Un foro si indica con `<riga><colonna>`, per esempio `g7`: la lettera è la riga, il numero la colonna.
Le righe vanno da `a` a `j` **contando dal basso verso l'alto**: `a` è la riga più in basso, `j` quella
più in alto. Il foro zero (in basso a sinistra) è `a1`.
A metà c'è una **fessura centrale** che separa le righe `a–e` (metà bassa) dalle `f–j` (metà alta):
i due lati sono isolati fra loro.
Nella stessa metà, i cinque fori di una **stessa colonna** sono collegati elettricamente: mettere due
gambe nella stessa colonna, sullo stesso lato, vuol dire collegarle.

# I rail di alimentazione

Ai bordi della breadboard corrono due coppie di linee lunghe, i **rail**: servono a distribuire
l'alimentazione a tutto il circuito. Una linea è il **+** (positivo), l'altra il **−** (negativo/massa).
A differenza delle colonne, un rail collega tutti i suoi fori per l'intera lunghezza.
In questo progetto l'alimentazione è **6V** da 4 pile AA: il rosso porta il +6V, il blu la massa.

# Cos'è un circuito

Un circuito è un percorso chiuso in cui la corrente parte dal + dell'alimentazione, attraversa i
componenti e torna al −. Se il percorso è interrotto, non passa corrente e nulla funziona.
Due grandezze bastano per iniziare: la **tensione** (la "spinta", in volt) e la **corrente** (quanta
carica scorre, in ampere). La corrente scorre solo se il percorso è completo.

# La legge di Ohm

Lega tre grandezze: tensione, resistenza e corrente. In parole: a parità di spinta (tensione), più
alta è la resistenza, meno corrente passa; la resistenza "frena" la corrente.
Serve a capire, per esempio, perché una resistenza in serie a un LED lo protegge: limita la corrente.
La formula, se richiesta: **V = R × I** (tensione = resistenza × corrente).

# Serie e parallelo

Due componenti sono in **serie** quando sono uno dopo l'altro sullo stesso percorso: la stessa corrente
li attraversa entrambi.
Sono in **parallelo** quando sono affiancati fra gli stessi due punti: la corrente si divide fra loro.
Regola pratica: le resistenze in serie si sommano; in parallelo la resistenza totale diminuisce.

# Polarità: componenti con verso e senza verso

Alcuni componenti vanno montati in un **verso preciso** (sono polarizzati): se invertiti non funzionano
o si danneggiano. Esempi: LED, diodi, condensatori elettrolitici, transistor, buzzer.
Altri non hanno verso e si possono girare come si vuole: resistenze, condensatori ceramici,
fotoresistenze, pulsanti.
Il verso corretto per ogni montaggio è sempre indicato nel manuale operativo: quello vale.

# La resistenza

Componente che **limita la corrente**. Non ha verso, si monta in qualsiasi orientamento.
Il suo valore (in ohm, Ω) si legge dalle bande colorate sul corpo.
Uso tipico: in serie a un LED per non farlo bruciare.

# Il LED

Diodo che **emette luce** quando la corrente lo attraversa nel verso giusto. È polarizzato.
La **gamba lunga** è il positivo (anodo), va verso il +; la gamba corta è il negativo (catodo).
Va quasi sempre usato con una resistenza in serie che ne limita la corrente.

# Il diodo

Lascia passare la corrente in **un solo verso** e la blocca nell'altro (come una valvola).
È polarizzato: la **banda** stampata sul corpo indica il catodo, cioè il lato da cui la corrente esce.
Uso tipico: protezione del circuito da inversioni di polarità.

# Il transistor

Interruttore/amplificatore comandato elettricamente: una piccola corrente su un piedino comanda una
corrente più grande sugli altri due. In questo progetto è il tipo NPN (PN2222).
Ha tre piedini: Emettitore, Base, Collettore. È polarizzato: l'ordine dei piedini dipende dal verso in
cui lo si monta, quindi si segue esattamente il manuale.

# Il condensatore

Accumula e rilascia carica: serve a stabilizzare tensioni o a creare ritardi.
L'**elettrolitico** (a barilotto) ha valori grandi ed è **polarizzato**: la striscia sul corpo indica il
negativo. Il **ceramico** (a lenticchia) ha valori piccoli e **non** ha verso.

# Il buzzer

Trasforma un segnale elettrico in suono. È polarizzato (ha un + segnato).
L'**attivo** suona da solo appena riceve tensione continua. Il **passivo** ha bisogno di un segnale che
oscilla per suonare. In questo progetto, dove serve un suono in continua, si usa l'attivo.

# La fotoresistenza (LDR)

Resistenza che **cambia valore con la luce**: molta luce → bassa resistenza, buio → alta resistenza.
Non ha verso. Serve a far reagire il circuito alla luce (per esempio accendere qualcosa al buio).

# Il trimmer (potenziometro)

Resistenza **regolabile**: girando la vite con un cacciavite se ne cambia il valore.
Ha tre piedini: i due esterni sono i capi, quello centrale è l'uscita regolata.
Uso tipico: regolare a mano una tensione o una soglia.

# Il pulsante

Interruttore momentaneo: **chiude il circuito solo mentre lo tieni premuto**, poi si riapre.
Non ha una polarità, ma ha un **verso giusto**: i pin sono collegati a coppie, quindi va montato a
cavallo della fessura centrale nell'orientamento indicato dal manuale.

# L'optoaccoppiatore

Collega due parti del circuito **con la luce anziché con un filo**: dentro c'è un LED che illumina un
sensore, così i due lati restano elettricamente separati.
È polarizzato e va montato nel verso indicato dal manuale (una tacca segna il riferimento).
