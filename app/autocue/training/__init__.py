"""
Auto Cue neu trainieren -- in der App (Einstellungen -> Auto Cue -> Training,
job.py) und im Quellbaum (`./run.command autocue-train`, `autocue-eval`). Beide
laufen ueber denselben Kern run.train_core().

Nachbau der Trainingsskripte des Autocue-Projekts, aber auf TrackTabs Stand:
Grid und Phrasen aus TrackTab (gridstore), Hand-Cues aus der DJ-Ansicht bzw. dem
Rekordbox-Abgleich (labels.py, Cue-Zuordnung je Training), Gold-Set als
TrackTab-Playlist (lists.py).

    dataset.py   Takt-Merkmale + Labels je Track (.npz, im Datenordner der Instanz)
    base.py      Grundmodelle Drop 2 / Break 1
    chain.py     Anker-Kette Drop 1 / Break 1 / Break 2 / Outro
    evaluate.py  volle Kette gegen die Hand-Cues des Gold-Sets
    boost.py     LightGBM (nur hier), isotonische Kalibrierung, Faltungen
    job.py       Training in der App (Hintergrund-Thread, Fortschritt, Abbruch)

LightGBM ist die einzige zusaetzliche Abhaengigkeit und wird nur in boost.py
importiert -- Auto Cue selbst rechnet ohne (trees.py). Im Bundle ist es dabei,
geladen wird es erst beim Training. Merkmale nur ueber die Funktionen
der Laufzeit (features.py, learned.build_design_matrix()) -- Training und App
duerfen nie verschieden rechnen. Aendert sich eine Merkmalsrechnung,
learned.FEATURE_VERSION erhoehen: dann rechnet der Export alles neu, und alte
Modelle werden zur Laufzeit abgelehnt.
"""
