#!/bin/sh
# tools/take.sh <post> <file> <since>: grab Gemini's download into the bank and mark the job done
PY="/c/Users/INFOLAB 2026/AppData/Local/Programs/Python/Python312/python.exe"
cd "$(dirname "$0")/.." && "$PY" tools/gemini_grab.py "output/bank/$1/$2" --since "$3" --wait 40 && PYTHONIOENCODING=utf-8 "$PY" -m engine.bank done "$1" "$2"
