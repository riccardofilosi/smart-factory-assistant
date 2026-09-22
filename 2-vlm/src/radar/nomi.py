"""nomi.py — schema dei nomi delle run.

Una run si chiama <commessa>.<n>: D1.1, D1.2, F3.2. Lo schema precedente era
OPER_<commessa>_<timestamp>, poco leggibile in cartella.
Il nome è la chiave di tutto il corredo: data/runs/<runid>/, i registri
data/registri/<runid>_*.json, i replay data/replay_*/<runid>_*.json, gli Excel
RUN_<runid>.xlsx e VLM_<runid>.xlsx.

L'ordinale è posizionale: dice quale run di quella commessa è, non quando è stata
fatta. Inserire a mano una run vecchia fra le altre non rinumera nulla: la
rinumerazione la fa lo script di rinomina, corredo incluso. `nuovo` non riusa mai
un ordinale già visto (prende il massimo + 1), così cancellare D1.1 non fa nascere
una seconda D1.2.
"""
import os
import re

_VECCHIO = re.compile(r"^OPER_([A-Za-z0-9]+)_")


def cid(runid):
    """Commessa di una run: 'D1.2' -> 'D1'. Tollera il vecchio schema
    'OPER_D1_<timestamp>' (run non ancora rinominate)."""
    m = _VECCHIO.match(runid)
    return m.group(1) if m else runid.split(".")[0]


def nuovo(runsdir, commessa):
    """Prossimo id libero per una commessa: <commessa>.<max ordinale visto + 1>."""
    usati = []
    pat = re.compile(rf"^{re.escape(commessa)}\.(\d+)$")
    for d in (os.listdir(runsdir) if os.path.isdir(runsdir) else []):
        m = pat.match(d)
        if m and os.path.isdir(os.path.join(runsdir, d)):
            usati.append(int(m.group(1)))
    return f"{commessa}.{max(usati, default=0) + 1}"
