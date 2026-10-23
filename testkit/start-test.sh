#!/bin/sh
# CTRANSLATE test server for a home network, Mac or Linux (see README-TEST.txt).
# Plain http on port 8080 for this computer and phones on the same Wi-Fi, invented test
# accounts (data/demo-accounts.txt), and port 8081 on this computer alone for a quick tunnel.
cd "$(dirname "$0")" || exit 1
command -v python3 >/dev/null || { echo "Python 3 is needed."; exit 1; }
ip=$(python3 -c "import socket; s = socket.socket(2, 2); s.connect(('192.0.2.1', 9)); print(s.getsockname()[0])" 2>/dev/null)
echo
echo "  CTRANSLATE test server"
echo "  This computer:  http://localhost:8080/admin    http://localhost:8080/speak"
echo "  Phones on the same Wi-Fi:  http://${ip:-THIS-COMPUTER}:8080/listen"
echo "  Test accounts: Test Admin 111111, Test Speaker 222222 (all in README-TEST.txt)"
echo "  Ctrl+C stops the server."
echo
( sleep 3
  case "$(uname)" in
    Darwin) open http://localhost:8080/admin ;;
    *) command -v xdg-open >/dev/null && xdg-open http://localhost:8080/admin ;;
  esac ) >/dev/null 2>&1 &
exec python3 ../server.py --http --port 8080 --tunnel-port 8081 --demo --data ./data
