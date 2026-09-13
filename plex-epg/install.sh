#!/usr/bin/env bash
set -euo pipefail

BASE="$HOME/threadfin/plex-epg"
RAW_BASE="https://raw.githubusercontent.com/ibaillie/COD-MW-Stadium-Easter-Egg-Brute-Force/plex-epg-tools/plex-epg"
BIND_IP="10.142.7.1"
PORT="34401"

mkdir -p "$BASE" "$HOME/.config/systemd/user"

echo "Downloading ordered EPG tools..."
curl -fsSL "$RAW_BASE/build_guide.py" -o "$BASE/build_guide.py"
curl -fsSL "$RAW_BASE/channels.json" -o "$BASE/channels.json"
chmod 700 "$BASE/build_guide.py"

echo "Building the first guide. This can take a few minutes..."
python3 "$BASE/build_guide.py"

cat > "$HOME/.config/systemd/user/plex-epg-http.service" <<EOF
[Unit]
Description=Iain Plex Ordered EPG HTTP server
After=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 -m http.server $PORT --bind $BIND_IP --directory $BASE
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

cat > "$HOME/.config/systemd/user/plex-epg-update.service" <<EOF
[Unit]
Description=Update Iain Plex ordered XMLTV guide
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 $BASE/build_guide.py
EOF

cat > "$HOME/.config/systemd/user/plex-epg-update.timer" <<'EOF'
[Unit]
Description=Daily update for Iain Plex ordered XMLTV guide

[Timer]
OnCalendar=*-*-* 04:30:00
Persistent=true
RandomizedDelaySec=10m

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now plex-epg-http.service plex-epg-update.timer

echo
echo "Installed successfully."
echo "Plex XMLTV URL: http://$BIND_IP:$PORT/guide.xml"
echo "Match report:      $BASE/matches.json"
echo "Guide file:        $BASE/guide.xml"
echo
echo "Service status:"
systemctl --user --no-pager --full status plex-epg-http.service | sed -n '1,8p' || true
