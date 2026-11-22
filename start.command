#!/usr/bin/env bash
# Odwołania2json – dwuklik w Finderze (macOS) uruchamia aplikację w przeglądarce.
# Tryb wsadowy (input -> output): w Terminalu ./start.sh batch
cd "$(dirname "$0")"
./start.sh "$@"
status=$?
if [ $status -ne 0 ]; then
  echo
  read -n 1 -s -r -p "Wystąpił błąd (kod $status). Naciśnij dowolny klawisz, aby zamknąć..."
fi
exit $status
