**Manuale Operativo**

**Smart Factory**

Assemblaggio e collaudo di 10 circuiti su breadboard · 5 facili + 5 difficili

Alimentazione 6V (4×AA) · circuiti passivi, niente Arduino · solo cavetti

# SEZIONE 0 — Convenzioni globali

Si leggono una volta e valgono per tutte le commesse. Alimentazione 6V (4×AA), circuiti passivi, niente Arduino. Solo cavetti: rigidi dove possibile, flessibili dove il rigido non arriva.

## 0.1 · Com’è fatta la breadboard

* Griglia: righe a–j, colonne 1–63. Le righe si contano **dal basso verso l’alto**: `a` è la riga più in basso, poi b, c, d, e, f, g, h, i, e `j` è la riga più in alto.
* Fessura centrale tra e ed f: separa la metà bassa (a–e) dalla metà alta (f–j).
* In una stessa colonna i 5 fori a–e sono lo stesso nodo; idem f–j. Le due metà NON sono collegate tra loro.
* 4 bus di alimentazione continui ai due lati: le righe esterne sono rosse, le interne blu. Su di essi corrono +6V (rosso) e GND (blu).
* L’assegnazione è per COLORE, non per lato fisso: in alcune commesse il GND (−) viene portato su entrambi i lati della board.

## 0.2 · Orientamento e «foro zero»

* Board sempre nello stesso verso: numeri leggibili, colonna 1 a sinistra. Foro di riferimento = a1, cioè l’angolo in **basso** a sinistra (riga a = riga più in basso).
* Se la stampa della board è ambigua → adesivo/freccia su a1.
* Alimentazione per colore: +6V = rosso, GND = blu. Batteria: filo + → rosso, filo − → blu.

## 0.3 · Notazione dei fori

* Griglia: <lettera><colonna> → e5, f45. Rail: +6V e GND.
* Si indica sempre il foro esatto: serve al controllo qualità posizionale.

## 0.4 · Codice colore dei cavetti (regola unica)

* Rosso = +6V. Blu = GND (−). Verde rigido = estensione del collettore del transistor su tratte lunghe (molte colonne). Arancione rigido = estensione del collettore su tratte corte (poche colonne). Nessun altro colore.
* Rigidi dove possibile: sagomati a misura, liberano la vista dall’alto della board (utile per il controllo qualità con fotocamera / VLM).
* Flessibili solo dove il rigido non arriva comodamente. Nessun ponte prefabbricato: solo cavetti.

## 0.5 · Componenti polarizzati — verso obbligato

|  |  |
| --- | --- |
| **Componente** | **Come riconosci il verso** |
| LED (tutti) | pin lungo = + (anodo); pin corto = − (catodo) |
| Cond. elettrolitico | striscia sul corpo = − (catodo) |
| Diodo 1N4007 | banda = catodo (−) |
| Transistor PN2222 | lato piatto (scritte) verso di te → E · B · C; semicerchio → C · B · E |
| Buzzer attivo | «+» segnato sul corpo |
| Trimmer 10 kΩ | non polarizzato; pin centrale = cursore/uscita |

*⚠ Batteria SEMPRE staccata quando si tocca il circuito. L’interruttore del portabatterie stacca la corrente senza sfilare i fili.*

## 0.6 · Procedura di collaudo/controllo qualità (base per tutte)

1. Collega la batteria (interruttore ON).
2. Esegui il collaudo/controllo qualità sul funzionamento specifico della commessa (es: premere pulsante, dito sul LDR, cosa deve suonare o accendersi).
3. Se NON OK: assicurarsi che i pezzi siano inseriti bene, a fondo nel foro (circuito non aperto)
4. Se NON OK: una correzione alla volta, ricontrolla ogni volta tutti i pin e i versi (polarità). Non scartare al primo tentativo.
5. Stacca sempre la batteria prima di rimettere le mani.

## 0.7 · Tassonomia difetti (legenda globale)

|  |  |  |  |
| --- | --- | --- | --- |
| **Codice** | **Difetto** | **Come te ne accorgi** | **Rimedio** |
| DEF-01 | Polarità invertita | LED spento, buzzer muto, diodo blocca tutto | Ruota il pezzo di 180° rispettando +/− |
| DEF-02 | Pezzo sbagliato | Comportamento «strano» (luce fioca, lampeggio anomalo) | Verifica valore/bande e sostituisci |
| DEF-03 | Pin piegato / non a fondo | Circuito aperto: la corrente non passa | Sfila, raddrizza, reinserisci a fondo |
| DEF-04 | Foro sbagliato | Falso contatto: pin su nodo non previsto | Confronta col piazzamento e sposta |
| DEF-05 | Pezzo mancante | Il circuito non parte affatto | Ricontrolla la distinta base |

# SEZIONE 1 — Guida ai componenti

Riconoscimento a colpo d’occhio. Foto reali del kit. Solo i componenti usati nelle 10 commesse.

|  |  |  |
| --- | --- | --- |
| **R220** ×20  **Resistenza 220 Ω**  Bande Rosso-Rosso-Marrone-Oro. Non polarizzata. Protezione LED. | **R5k1** ×10  **Resistenza 5.1 kΩ**  Bande Verde-Marrone-Rosso-Oro. Non polarizzata. | **R10k** ×10  **Resistenza 10 kΩ**  Bande Marrone-Nero-Arancio-Oro. Non polarizzata. |
| **R100k** ×10  **Resistenza 100 kΩ**  Bande Marrone-Nero-Giallo-Oro. Non polarizzata. | **LED-R** ×10  **LED rosso 5 mm**  Gamba lunga = anodo (+); lato piatto = catodo (−). | **LED-G** ×10  **LED verde 5 mm**  Gamba lunga = anodo (+); lato piatto = catodo (−). |
| **LED-Y** ×10  **LED giallo 5 mm**  Gamba lunga = anodo (+); lato piatto = catodo (−). | **LED-B** ×10  **LED blu 5 mm**  Gamba lunga = anodo (+); lato piatto = catodo (−). | **LED-RGB** ×1  **LED RGB 5 mm (catodo comune)**  4 pin, lente smerigliata. Gamba più lunga = catodo comune; gli altri 3 = anodi R/G/B. |
| **POT** ×1  **Trimmer 10 kΩ (3386P)**  3 pin: i due esterni = estremi, il centrale = cursore/uscita. Si regola col cacciavite. | **C-EL100** ×5  **Cond. elettrolitico 100 µF**  Sul corpo «100µF 50V». Striscia chiara = − (catodo). Polarizzato. | **D1** ×5  **Diodo 1N4007**  Banda sul corpo = catodo (−). |
| **Q1** ×5  **Transistor NPN PN2222 (TO-92)**  Lato piatto (scritte) → E · B · C; semicerchio → C · B · E. | **OPTO** ×1  **Optoaccoppiatore 4N35 (DIP-6)**  Tacca/pallino = pin 1. Polarizzato (LED in / fototransistor out). | **LDR** ×2  **Fotoresistenza CDS-55**  Non polarizzata. Resistenza cala con la luce (buio ≈ MΩ, luce ≈ kΩ). |
| **BTN** ×10  **Pulsante tattile 4 pin**  A cavallo della fessura; usa i due pin in diagonale. | **BUZ-A** ×1  **Buzzer attivo**  Nastro nero sul dorso, «+» segnato. Suona in continua (DC). | **CAVETTI** —  **Cavetto rigido blu**  Collegato al GND (-) |
| **CAVETTI** —  **Cavetto flessibile blu**  Collegato al GND (-) | **CAVETTI** —  **Cavetto rigido arancione**  Collettore (tratte corte) | **CAVETTI** —  **Cavetto rigido verde**  Collettore (tratte lunghe) |
| **CAVETTI** —  **Cavetto rigido rosso**  Collegato al +6V |

# SEZIONE 2 — Schede commessa

# FACILE F1 — Spia luminosa a pulsante

*Premi il pulsante → il LED rosso si accende; rilasci → si spegne.*

**DISTINTA BASE (BOM)**

|  |  |  |
| --- | --- | --- |
| **Cod.** | **Componente** | **Q.tà** |
| BTN | Pulsante tattile 4 pin | 1 |
| R220 | Resistenza 220 Ω | 1 |
| LED-R | LED rosso 5 mm | 1 |
| CAV | Cavetti (1 rosso rigido +6V · 1 blu rigido GND) | 2 |

**FOTO REALE**

![](images/immagine_6296b336.jpg)

**MONTAGGIO PASSO-PASSO**

1. **Pulsante BTN** e5 · e7 · f5 · f7 *— a cavallo fessura; ingresso e5, uscita f7 (diagonale)*

2. **R220** g7 – g12 *— span 5 colonne; non polarizzata*

3. **LED rosso** anodo h12 · catodo h15 *— anodo (gamba lunga) verso R220*

4. **Cavetto rosso rigido +6V** d5 → +

5. **Cavetto blu rigido GND** i15 → −

**SCHEMA ELETTRICO**

![](images/immagine_bf66e9a2.png)

**COLLAUDO/ CONTROLLO QUALITÀ: procedura base per tutte (passo 0.6 della sezione 0) + funzionamento specifico**

**COLLAUDO**

1. Collega batteria 6V (+→rosso, −→blu), interruttore ON.
2. A riposo SPENTO · premuto ACCESO · rilasciato SPENTO.
3. Stacca la batteria.

**CONTROLLO QUALITÀ — posizionale**

* BTN a cavallo della fessura su col 5 e 7 (ingresso e5, uscita f7).
* R220 g7 🡪 g12.
* LED anodo h12 (gamba lunga verso R220), catodo h15.
* Cavetto rosso d5 🡪 +; cavetto blu i15 🡪 -.

**CONTROLLO QUALITÀ — funzionale**

A riposo: LED spento. Pulsante premuto: LED acceso. Rilasciato: LED spento.

**DIFETTI ATTESI**

|  |  |
| --- | --- |
| **Codice** | **Difetto** |
| DEF-01 | LED invertito → resta spento. |
| DEF-04 | Pulsante ruotato / non a cavallo → sempre acceso o mai. |
| DEF-03 | Gamba non a fondo → non commuta. |

# FACILE F2 — Semaforo 3 LED

*Un pulsante accende insieme 3 LED (rosso, giallo, verde).*

**DISTINTA BASE (BOM)**

|  |  |  |
| --- | --- | --- |
| **Cod.** | **Componente** | **Q.tà** |
| BTN | Pulsante tattile 4 pin | 1 |
| R220 | Resistenza 220 Ω | 3 |
| LED-R | LED rosso 5 mm | 1 |
| LED-Y | LED giallo 5 mm | 1 |
| LED-G | LED verde 5 mm | 1 |
| CAV | Cavetto rosso rigido +6V | 1 |

**FOTO REALE**

![](images/immagine_7843e80f.jpg)

**MONTAGGIO PASSO-PASSO**

1. **Pulsante BTN** e5 · e7 · f5 · f7 *— uscita f7 = nodo N2 (col 7)*

2. **R220 #1 (rosso)** g7 – g15 *— parte da N2*

3. **R220 #2 (giallo)** h7 – h17 *— parte da N2*

4. **R220 #3 (verde)** i7 – i19 *— parte da N2*

5. **LED rosso** anodo j15 · catodo → − *— gamba dritta nel bus −*

6. **LED giallo** anodo j17 · catodo → −

7. **LED verde** anodo j19 · catodo → −

8. **Cavetto rosso rigido +6V** d5 → + *— alimenta ingresso bottone*

**SCHEMA ELETTRICO**

![](images/immagine_5f728840.png)

**COLLAUDO/ CONTROLLO QUALITÀ: procedura base per tutte (passo 0.6 della sezione 0) + funzionamento specifico**

**COLLAUDO**

1. Collega batteria 6V (+→rosso, −→blu).
2. Premi = 3 LED accesi insieme; rilascia = tutti spenti.
3. Stacca la batteria.

**CONTROLLO QUALITÀ — posizionale**

* BTN a cavallo col 5–7; uscita f7.
* 3 resistenze partono tutte da col 7 (g7/h7/i7) verso g15/h17/i19.
* LED anodi su j15/j17/j19 (gamba lunga), catodi dritti nel GND − adiacente.
* Cavetto rosso da d5 🡪 +.

**CONTROLLO QUALITÀ — funzionale**

Premuto: tutti e 3 i LED accesi. Se uno resta spento → ricontrolla quel ramo (LED invertito o Resistenze non in colonne corrette).

**DIFETTI ATTESI**

|  |  |
| --- | --- |
| **Codice** | **Difetto** |
| DEF-01 | Un LED invertito → solo quello resta spento. |
| DEF-04 | Bottone ruotato/non a cavallo → nessuno si accende. |
| DEF-03 | Gamba resistenza non in colonna corretta → ramo aperto. |
| DEF-02 | Resistenza sbagliata |

# FACILE F3 — Dimmer a trimmer

*Giri il trimmer col giravite e regoli la luminosità del LED blu (pieno → spento).*

**DISTINTA BASE (BOM)**

|  |  |  |
| --- | --- | --- |
| **Cod.** | **Componente** | **Q.tà** |
| POT | Trimmer 10 kΩ (3386P) | 1 |
| R220 | Resistenza 220 Ω | 1 |
| LED-B | LED blu 5 mm | 1 |
| CAV | Cavetti rosso rigido +6V | 2 |

**FOTO REALE**

![](images/immagine_9c452eed.jpg)

**MONTAGGIO PASSO-PASSO**

1. **Trimmer 10 kΩ** estremi f13 · f15 · cursore g14 *— pin1 (f13) = +6V in · cursore g14 = uscita · f15 libero*

2. **R220** i14 – i21 *— dal cursore (col14) → col21*

3. **LED blu** anodo j21 · catodo → − *— gamba dritta nel bus −*

4. **Cavetto rosso rigido +6V** i13 → i3 *— porta il nodo pin1 (col13) verso col3*

5. **Cavetto rosso rigido +6V** f3 → + *— dal col3 al rail +*

**SCHEMA ELETTRICO**

![](images/immagine_9b9a9757.png)

**COLLAUDO/ CONTROLLO QUALITÀ: procedura base per tutte (passo 0.6 della sezione 0) + funzionamento specifico**

**COLLAUDO**

1. Collega batteria 6V (+→rosso, −→blu).

2. LED acceso; giri il trimmer → luminosità varia.

3. Stacca la batteria.

**CONTROLLO QUALITÀ — posizionale**

* Trimmer: pin1 f13, cursore g14, estremo libero f15.
* R220 dal cursore (col14), i14 🡪 i21.
* LED anodo j21, catodo in -.
* +6V al pin1 tramite i due cavetti rigidi i13→i3 e f3→+.

**CONTROLLO QUALITÀ — funzionale**

Ruotando il trimmer la luminosità cambia con continuità (da pieno a spento).

**DIFETTI ATTESI**

|  |  |
| --- | --- |
| **Codice** | **Difetto** |
| DEF-01 | LED invertito → spento. |
| DEF-04 | R220 non sul cursore (col14) → intensitò luce non regolata. |
| DEF-03 | Pin trimmer non a fondo → circuito aperto. |

# FACILE F4 — Esposimetro a LDR

*Esposimetro proporzionale (Meno luce sulla LDR → LED meno luminoso. Più luce 🡪 LED più luminoso)*

**DISTINTA BASE (BOM)**

|  |  |  |
| --- | --- | --- |
| **Cod.** | **Componente** | **Q.tà** |
| LDR | Fotoresistenza CDS-55 | 1 |
| R220 | Resistenza 220 Ω | 1 |
| LED-G | LED verde 5 mm | 1 |
| CAV | Cavetto blu flessibile GND | 1 |

**FOTO REALE**

![](images/immagine_0093817f.jpg)

**MONTAGGIO PASSO-PASSO**

1. **LDR (CDS-55)** un pin → + · altro pin → e11 *— non polarizzata; pin dritto nel bus +*

2. **LED verde** anodo d11 · catodo d14 *— polarizzato*

3. **R220** b14 – b22 *— col14 (catodo) → col22*

4. **Cavetto blu flessibile GND** e22 → −

**SCHEMA ELETTRICO**

![](images/immagine_563a934c.png)

**COLLAUDO/ CONTROLLO QUALITÀ: procedura base per tutte (passo 0.6 della sezione 0) + funzionamento specifico**

**COLLAUDO**

1. Collega batteria 6V (+→rosso, −→blu).

2. Oscurare la LDR con le dita → LED nettamente meno luminoso.

3. Stacca la batteria.

**CONTROLLO QUALITÀ — posizionale**

* LDR: e11 🡪 +.
* LED anodo d11 (verso LDR), catodo d14.
* R220 b14 🡪 b22.
* Cavetto blu: e22 🡪 -.

**CONTROLLO QUALITÀ — funzionale**

Ombra → luce più fioca.

**DIFETTI ATTESI**

|  |  |
| --- | --- |
| **Codice** | **Difetto** |
| DEF-01 | LED invertito (polarità) → spento. |
| DEF-03 | Pin LDR non a fondo → circuito aperto. |
| DEF-02 | LED non cambia → LDR o cablaggio. |

# FACILE F5 — Miscelatore RGB

*LED RGB a catodo comune: i 3 colori accesi insieme → luce bianca.*

**DISTINTA BASE (BOM)**

|  |  |  |
| --- | --- | --- |
| **Cod.** | **Componente** | **Q.tà** |
| LED-RGB | LED RGB 5 mm (catodo comune) | 1 |
| R220 | Resistenza 220 Ω | 3 |
| CAV | Cavetti (1 rosso rigido +6V · 1 blu flessibile GND) | 2 |

**FOTO REALE**

![](images/immagine_d142a514.jpg)

**MONTAGGIO PASSO-PASSO**

1. **LED RGB** a14 (R+) · a15 (catodo comune) · a16 (G+) · a17 (B+) *— gamba lunga = catodo comune (col15); R su col14, G su col16, B su col17*

2. **R220 (rosso)** b6 – b14

3. **R220 (verde)** c6 – c16

4. **R220 (blu)** d6 – d17

5. **Cavetto rosso rigido +6V** a6 → + *— col6 = bus + comune ai 3 R220*

6. **Cavetto blu flessibile GND** e15 → − *— catodo comune*

**SCHEMA ELETTRICO**

![](images/immagine_0a79af0f.png)

**COLLAUDO/ CONTROLLO QUALITÀ: procedura base per tutte (passo 0.6 della sezione 0) + funzionamento specifico**

**COLLAUDO**

1. Collega batteria 6V (+→rosso, −→blu).
2. Luce bianca (3 colori insieme).
3. Togli una ad una le resistenze R220 → sparisce un colore (individua il ramo).
4. Stacca la batteria.

**CONTROLLO QUALITÀ — posizionale**

* RGB: R+ a14, catodo comune a15 (gamba lunga), G+ a16, B+ a17.
* 3 R220 partono da col6 (b6, c6, d6) verso b14/c16/d17 (i 3 anodi).
* Cavetto rosso da a6 🡪 +; blu da e15 🡪 -.

**CONTROLLO QUALITÀ — funzionale**

Batteria → luce bianca. Togliendo un R220 sparisce quel colore.

**DIFETTI ATTESI**

|  |  |
| --- | --- |
| **Codice** | **Difetto** |
| DEF-01 | Catodo/anodo RGB scambiati → spento. |
| DEF-04 | Un R220 non in direzione della gamba corretta → colore assente. |
| DEF-04 | Gamba RGB su colonna sbagliata → colore errato/assente. |
| DEF-02 | Resistenza sbagliata |

# DIFFICILE D1 — Allarme acustico a pulsante

*Premi il pulsante → suonano insieme buzzer e LED rosso (transistor = interruttore).*

**DISTINTA BASE (BOM)**

|  |  |  |
| --- | --- | --- |
| **Cod.** | **Componente** | **Q.tà** |
| BTN | Pulsante tattile 4 pin | 1 |
| R5k1 | Resistenza 5.1 kΩ (base) | 1 |
| Q1 | Transistor PN2222 (TO-92) | 1 |
| BUZ-A | Buzzer attivo | 1 |
| LED-R | LED rosso 5 mm | 1 |
| R220 | Resistenza 220 Ω | 1 |
| CAV | Cavetti (rosso ×2, verde rigido collettore ×1, blu flessibile ×1) | 4 |

**FOTO REALE**

![](images/immagine_2b20df55.jpg)

**MONTAGGIO PASSO-PASSO**

1. **Pulsante BTN** e2 · e4 · f2 · f4 *— ingresso col2, uscita col4*

2. **Cavetto rosso rigido +6V** d2 → + *— ingresso bottone*

3. **R5k1 (base)** c4 – c9 *— verso la base del transistor (col9)*

4. **Transistor Q1** E b7 · B b9 · C b11 *— E-B-C, lato piatto (scritte) verso operatore*

5. **Cavetto blu flessibile GND** e7 → − *— emettitore*

6. **Buzzer attivo** − e11 (collettore) · + e14

7. **Cavetto rosso +6V** b14 → + *— buzzer +*

8. **Cavetto verde rigido (collettore)** a11 → a41 *— estende il collettore fino al LED*

9. **LED rosso** catodo d41 (collettore) · anodo d44

10. **R220** b44 → + *— +6V → R220 → anodo LED*

**SCHEMA ELETTRICO**

![](images/immagine_3403e79e.png)

**COLLAUDO/ CONTROLLO QUALITÀ: procedura base per tutte (passo 0.6 della sezione 0) + funzionamento specifico**

**COLLAUDO**

1. Collega batteria 6V (+→rosso, −→blu).
2. Premi = buzzer suona + LED rosso acceso; rilascia = buzzer silenzioso/LED spento.
3. Stacca la batteria.

**CONTROLLO QUALITÀ — posizionale**

* BTN a cavallo col 2 e 4 (e2 · e4 · f2 · f4).
* transistor E b7 · B b9 · C b11 (lato piatto verso operatore).
* R5k1 c4 🡪 c9.
* Buzzer − e11 , + e14.
* LED catodo d41, anodo d44.
* R220 b44 → +.
* Collettore esteso (cavetto verde) a11→ a41.

**CONTROLLO QUALITÀ — funzionale**

Premuto: buzzer suona e LED acceso insieme. A riposo: buzzer silenzioso/LED spento.

**DIFETTI ATTESI**

|  |  |
| --- | --- |
| **Codice** | **Difetto** |
| DEF-01 | Transistor: E/C scambiati (verso del transistor, lato piatto corretto). |
| DEF-01 | Buzzer polarità invertita → muto. (vedere dove va il +) |
| DEF-01 | LED invertito → spento. |
| DEF-04 | Pulsante non a cavallo → non parte. |
| DEF-04 | Cavo rosso collegato al buzzer posizionato male (deve andare sul + del buzzer) |
| DEF-02 | Resistenza sbagliata |

# DIFFICILE D2 — Sensore crepuscolare

*Luce → LED spento (partitore 100k/LDR pilota la base). Buio → LED acceso.*

**DISTINTA BASE (BOM)**

|  |  |  |
| --- | --- | --- |
| **Cod.** | **Componente** | **Q.tà** |
| R100k | Resistenza 100 kΩ | 1 |
| LDR | Fotoresistenza CDS-55 | 1 |
| Q1 | Transistor PN2222 (TO-92) | 1 |
| LED-B | LED blu 5 mm | 1 |
| R220 | Resistenza 220 Ω | 1 |
| CAV | Cavetti (verde rigido collettore ×1, blu ×1) | 2 |

**FOTO REALE**

![](images/immagine_4b2377d5.jpg)

**MONTAGGIO PASSO-PASSO**

1. **LDR (CDS-55)** − (GND) – e9 *— non polarizzata; nodo base = col9 (ramo basso del partitore)*

2. **R100k** b9 → + *— ramo alto del partitore (col9)*

3. **Transistor Q1** E c7 · B c9 · C c11 *— base = col9; lato piatto verso operatore*

4. **Cavetto blu GND** e7 → − *— emettitore*

5. **Cavetto verde rigido (collettore)** e11 → e41 *— collettore → LED*

6. **LED blu** anodo d44 · catodo d41 *— catodo lato collettore (col41)*

7. **R220** b44 → + *— +6V → R220 → anodo LED*

**SCHEMA ELETTRICO**

![](images/immagine_b908fff3.png)

**COLLAUDO/ CONTROLLO QUALITÀ: procedura base per tutte (passo 0.6 della sezione 0) + funzionamento specifico**

**COLLAUDO**

1. Collega batteria 6V (+→rosso, −→blu).
2. Oscurare LDR con le dita. Luce sulla LDR → LED spento. Buio/ombra → LED acceso.
3. Stacca la batteria.

**CONTROLLO QUALITÀ — posizionale**

* Partitore: R100k b9 → +. LDR e9 🡪 -; nodo comune col9 = base.
* Transistor E c7 · B c9 · C c11.
* LED catodo d41, anodo d44.
* R220 b44 → +6V.
* Collettore esteso (cavetto verde) e11→ e41.

**CONTROLLO QUALITÀ — funzionale**

Luce sulla LDR → LED spento. Buio → LED acceso.

**DIFETTI ATTESI**

|  |  |
| --- | --- |
| **Codice** | **Difetto** |
| DEF-04 | Partitore invertito (LDR↔R100k) → acceso alla luce. |
| DEF-01 | Transistor: E/C scambiati (verso del transistor, lato piatto corretto). |
| DEF-01 | LED invertito (polarità) → spento. |
| DEF-02 | Resistenza sbagliata |

# DIFFICILE D3 — Lampeggiatore alternato

*Multivibratore astabile: i 2 LED lampeggiano alternati (~0,7 Hz).*

**DISTINTA BASE (BOM)**

|  |  |  |
| --- | --- | --- |
| **Cod.** | **Componente** | **Q.tà** |
| Q1 | Transistor PN2222 (TO-92) | 2 |
| C-EL100 | Cond. elettrolitico 100 µF | 2 |
| R10k | Resistenza 10 kΩ | 2 |
| R220 | Resistenza 220 Ω | 2 |
| LED-R | LED rosso 5 mm | 1 |
| LED-B | LED blu 5 mm | 1 |
| CAV | Cavetti blu flessibili GND | 2 |

**FOTO REALE**

![](images/immagine_276e01ea.jpg)

**MONTAGGIO PASSO-PASSO**

1. **R220 #1** d3 → + *— carico collettore Q1 (col3)*

2. **LED rosso (sx)** anodo c3 · catodo c6 *— catodo = collettore Q1 (col6)*

3. **R10k #1** a8 → + *— pull-up base Q1 (col8)*

4. **Transistor Q1** C b6 · B b8 · E b10 *— semicerchio (cerchio vuoto) verso operatore → C-B-E*

5. **C1 100 µF** anodo e6 · catodo e18 *— collettore Q1 (col6) → base Q2 (col18)*

6. **Transistor Q2** E b16 · B b18 · C b20 *— lato piatto verso operatore (specchiato) → E-B-C*

7. **C2 100 µF** anodo d20 · catodo d8 *— collettore Q2 (col20) → base Q1 (col8)*

8. **R10k #2** a18 → + *— pull-up base Q2 (col18)*

9. **LED blu (dx)** anodo c24 · catodo c20 *— catodo = collettore Q2 (col20)*

10. **R220 #2** d24 → + *— carico collettore Q2 (col24)*

11. **Cavetto blu flessibile GND** e10 → − *— emettitore Q1 (col10)*

12. **Cavetto blu flessibile GND** e16 → − *— emettitore Q2 (col16)*

**SCHEMA ELETTRICO**

![](images/immagine_6ad746bd.png)

**COLLAUDO/ CONTROLLO QUALITÀ: procedura base per tutte (passo 0.6 della sezione 0) + funzionamento specifico**

**COLLAUDO**

1. Collega batteria 6V (+→rosso, −→blu).
2. I 2 LED lampeggiano alternati (~1 volta/sec).
3. Stacca la batteria.

**CONTROLLO QUALITÀ — posizionale**

* Q1 (C b6 · B b8 · E b10, semicerchio verso operatore) e Q2 (E b16 · B b18 · C b20, piatto verso operatore).
* C1 e6→e18 (collettore Q1→base Q2), C2 d20→d8 (collettore Q2→base Q1). Controllare striscia sul corpo del condensatore, corrisponde al catodo (-).
* Cavetti blu: e10 🡪 -, e16 🡪 -.

**CONTROLLO QUALITÀ — funzionale**

Batteria → i 2 LED lampeggiano alternati (~1 volta/sec). Nessuno resta fisso acceso o spento.

**DIFETTI ATTESI**

|  |  |
| --- | --- |
| **Codice** | **Difetto** |
| DEF-01 | Condensatore o transistor invertiti (polarità) → nessuna oscillazione. |
| DEF-01 | Transistor non specchiati → niente lampeggio. |
| DEF-01 | Condensatori non specchiati 🡪 niente lampeggio |
| DEF-03 | Gamba transistor non a fondo → ramo aperto. |
| DEF-02 | Resistenza sbagliata |

# DIFFICILE D4 — Timer a spegnimento ritardato

*Premi e rilasci → il buzzer suona e continua ~1–2 s, poi tace (RC + transistor).*

**DISTINTA BASE (BOM)**

|  |  |  |
| --- | --- | --- |
| **Cod.** | **Componente** | **Q.tà** |
| BTN | Pulsante tattile 4 pin | 1 |
| D1 | Diodo 1N4007 | 1 |
| C-EL100 | Cond. elettrolitico 100 µF | 1 |
| R10k | Resistenza 10 kΩ | 1 |
| Q1 | Transistor PN2222 (TO-92) | 1 |
| BUZ-A | Buzzer attivo | 1 |
| CAV | Cavetti (rosso rigido ×2, blu rigido ×1, blu flessibile ×1, arancione rigido collettore ×1) | 5 |

**FOTO REALE**

![](images/immagine_b681d1a1.jpg)

**MONTAGGIO PASSO-PASSO**

1. **Pulsante BTN** e3 · e5 · f3 · f5 *— +6V → col3, uscita col5*

2. **Cavetto rosso rigido +6V** d3 → + *— ingresso bottone*

3. **Diodo 1N4007** anodo d5 · catodo d13 (banda) *— col5 → nodo carica (col13)*

4. **Condensatore 100 µF** + c13 (nodo carica) · − h13 *— a cavallo della fessura*

5. **Cavetto blu rigido GND** i13 → − *— catodo cap (col13-alto)*

6. **R10k** b13 – b21 *— nodo carica → base (col21)*

7. **Transistor Q1** E a19 · B a21 · C a23 *— E-B-C, lato piatto verso operatore*

8. **Cavetto blu flessibile GND** b19 → − *— emettitore (col19)*

9. **Cavetto arancione rigido (collettore)** b23 → b32 *— estende il collettore (col23) fino al buzzer (col32); tratta corta*

10. **Buzzer attivo** − e32 (collettore) · + e35

11. **Cavetto rosso rigido +6V** b35 → + *— buzzer +*

**SCHEMA ELETTRICO**

![](images/immagine_8cc75612.png)

**COLLAUDO/ CONTROLLO QUALITÀ: procedura base per tutte (passo 0.6 della sezione 0) + funzionamento specifico**

**COLLAUDO**

1. Collega batteria 6V (+→rosso, −→blu).
2. Premi e rilascia → il buzzer suona e continua ~1–2 s, poi tace.
3. Stacca la batteria.

**CONTROLLO QUALITÀ — posizionale**

* Diodo anodo d5 🡪 catodo d13 (banda verso col13).
* Condensatore + c13, − h13 (alto) a cavallo fessura.
* R10k b13 → b21.
* Transistor E a19 · B a21 · C a23.
* Buzzer − e32, + e35.
* Collettore esteso (cavetto arancione) b23 → b32.
* Cavetti rossi d3 → +, b35 → +.
* Blu i13 → −, b19 → −.

**CONTROLLO QUALITÀ — funzionale**

Premi e rilascia: il suono persiste ~1–2 s poi cessa (spegnimento ritardato).

**DIFETTI ATTESI**

|  |  |
| --- | --- |
| **Codice** | **Difetto** |
| DEF-01 | Diodo invertito (polarità) (controllare banda 🡪 -) |
| DEF-01 | Transistor E/C scambiati (polarità, lato piatto verso operatore) |
| DEF-01 | Buzzer polarità invertita → muto. (controllare il +) |
| DEF-01 | Condensatore elettrolitico invertito. (controllare banda 🡪 -) |

# DIFFICILE D5 — Opto-isolatore a pulsante (4N35)

*Premi → LED\_in acceso → il fototransistor conduce → LED\_out acceso (ingresso e uscita isolati dalla fessura).*

*⚠ In questa commessa il GND (−) è portato su entrambi i lati della board.*

**DISTINTA BASE (BOM)**

|  |  |  |
| --- | --- | --- |
| **Cod.** | **Componente** | **Q.tà** |
| OPTO | Optoaccoppiatore 4N35 (DIP-6) | 1 |
| BTN | Pulsante tattile 4 pin | 1 |
| R220 | Resistenza 220 Ω | 2 |
| LED-R | LED (ingresso) | 1 |
| LED-G | LED (uscita) | 1 |
| CAV | Cavetti (rosso rigido ×1, blu rigido ×2, blu flessibile ×1) | 4 |

**FOTO REALE**

![](images/immagine_aab5e15c.jpg)

**MONTAGGIO PASSO-PASSO**

1. **Pulsante BTN** e2 · e4 · f2 · f4 *— +6V → col2, uscita col4*

2. **Cavetto rosso rigido +6V** d2 → + *— ingresso bottone*

3. **R220 #1 (ingresso)** d4 – d9

4. **LED\_in** anodo c9 · catodo c13 *— col13 → LED interno 4N35*

5. **4N35 (DIP-6)** ingresso e13·e14·e15 / uscita g13·g14·g15 *— tacca lato f–j (lato alto). LED int: anodo col13-basso, catodo col14-basso. Fototr.: base col13-alto (libera), emettitore col14-alto, collettore col15-alto*

6. **Cavetto blu rigido GND** c14 → − *— catodo LED interno (col14-basso)*

7. **LED\_out** catodo h15 (collettore) · anodo h18

8. **R220 #2 (uscita)** f18 → + *— al rail +*

9. **Cavetto blu rigido GND** j14 → − *— emettitore (col14-alto)*

10. **Cavetto blu flessibile GND** da − a − *— di lato a destra della resistenza, per non ostacolare la vista dall’alto*

**SCHEMA ELETTRICO**

![](images/immagine_b26d5d88.png)

**COLLAUDO/ CONTROLLO QUALITÀ: procedura base per tutte (passo 0.6 della sezione 0) + funzionamento specifico**

**COLLAUDO**

1. Collega batteria 6V (+→rosso, −→blu).
2. Premi = LED\_in + LED\_out accesi (l’uscita segue l’ingresso attraverso l’isolamento); rilascia = spenti.
3. Stacca la batteria.

**CONTROLLO QUALITÀ — posizionale**

* 4N35 su col 13-14-15, tacca verso lato f–j; ingresso in a–e (col13/14), uscita in f–j (col14/15).
* LED\_in anodo c9, catodo c13; LED\_out catodo h15, anodo h18.
* R220 uscita f18 → +.
* cavetti blu c14 → −, j14 → −.

**CONTROLLO QUALITÀ — funzionale**

Pulsante premuto: LED\_in e LED\_out accesi insieme. L’uscita è isolata galvanicamente dall’ingresso.

**DIFETTI ATTESI**

|  |  |
| --- | --- |
| **Codice** | **Difetto** |
| DEF-04 | Verso optoaccopiatore 4N35 errato → niente. |
| DEF-01 | LED\_in / LED\_out invertito (polarità) → spento. |
| DEF-04 | Resistenze posizionate male |
| DEF-04 | Emettitore↔collettore scambiati → LED\_out spento. |

# APPENDICE — Punteggio di complessità

Metrica a priori: pezzi + pin + 2·polarizzati + cavetti.

|  |  |  |  |  |  |  |
| --- | --- | --- | --- | --- | --- | --- |
| **#** | **Commessa** | **Pezzi** | **Pin** | **Pol.** | **Cav.** | **Score** |
| F1 | Spia luminosa a pulsante | 3 | 6 | 1 | 2 | 13 |
| F2 | Semaforo 3 LED | 7 | 14 | 3 | 1 | 28 |
| F3 | Dimmer a trimmer | 3 | 7 | 1 | 2 | 14 |
| F4 | Esposimetro a LDR | 3 | 6 | 1 | 1 | 12 |
| F5 | Miscelatore RGB | 4 | 10 | 1 | 2 | 18 |
| D1 | Allarme acustico a pulsante | 5 | 13 | 3 | 4 | 28 |
| D2 | Sensore crepuscolare | 5 | 11 | 2 | 2 | 22 |
| D3 | Lampeggiatore alternato | 8 | 20 | 4 | 2 | 38 |
| D4 | Timer a spegnimento ritardato | 6 | 14 | 4 | 5 | 33 |
| D5 | Opto-isolatore a pulsante (4N35) | 5 | 15 | 3 | 4 | 30 |