"""
Auto Cue neu trainieren -- Entwicklerwerkzeug, laeuft nur im Quellbaum
(`./run.command autocue-train`, `autocue-eval`; Ablauf in run.py).

Nachbau der Trainingsskripte des Autocue-Projekts, aber auf TrackTabs Stand:
Grid und Phrasen aus TrackTab (gridstore), Hand-Cues aus der DJ-Ansicht bzw. dem
Rekordbox-Abgleich (labels.py), Gold-Set als TrackTab-Playlist (gold.py).

    dataset.py   Takt-Merkmale + Labels je Track (.npz, im Datenordner der Instanz)
    base.py      Grundmodelle Drop 2 / Break 1
    chain.py     Anker-Kette Drop 1 / Break 1 / Break 2 / Outro
    evaluate.py  volle Kette gegen die Hand-Cues des Gold-Sets
    boost.py     LightGBM (nur hier), isotonische Kalibrierung, Faltungen

LightGBM ist die einzige zusaetzliche Abhaengigkeit (requirements-train.txt) und
kommt nie ins Bundle (tracktab.spec: excludes). Merkmale nur ueber die Funktionen
der Laufzeit (features.py, learned.build_design_matrix()) -- Training und App
duerfen nie verschieden rechnen. Aendert sich eine Merkmalsrechnung,
learned.FEATURE_VERSION erhoehen: dann rechnet der Export alles neu, und alte
Modelle werden zur Laufzeit abgelehnt.
"""
