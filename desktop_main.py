"""
Startskript fuer PyInstaller.

multiprocessing.freeze_support() MUSS die erste Anweisung sein: ohne sie
startet jeder Worker-Prozess das Programm komplett neu, statt nur die
Analysefunktion auszufuehren — der Scan wuerde endlos Fenster oeffnen.
"""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from app.desktop import main
    sys.exit(main())
