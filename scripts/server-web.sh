#!/usr/bin/env bash
set -euo pipefail
ip=${1:?Supply the public server IPv4 address}
if ! [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo 'Invalid IPv4 address' >&2
    exit 1
fi
cat > /etc/nginx/conf.d/noatech-security.conf <<'CONF'
limit_req_zone $binary_remote_addr zone=noatech_requests:10m rate=10r/s;
limit_conn_zone $binary_remote_addr zone=noatech_connections:10m;
CONF
cat > /etc/nginx/sites-available/noatech <<CONF
server {
    listen 80;
    server_tokens off;
    server_name $ip;
    location /.well-known/acme-challenge/ { root /var/www/noatech-acme; }
    location / { return 503; }
}
CONF
ln -sfn /etc/nginx/sites-available/noatech /etc/nginx/sites-enabled/noatech
nginx -t
systemctl reload nginx
/opt/noatech/venv/bin/certbot certonly --non-interactive --agree-tos --register-unsafely-without-email \
    --preferred-profile shortlived --webroot -w /var/www/noatech-acme \
    --ip-address "$ip" --cert-name noatech-ip
cat > /etc/nginx/sites-available/noatech <<CONF
server {
    listen 80;
    server_tokens off;
    server_name $ip;
    location /.well-known/acme-challenge/ { root /var/www/noatech-acme; }
    location / { return 301 https://\$host\$request_uri; }
}
server {
    listen 443 ssl;
    server_tokens off;
    server_name $ip;
    ssl_certificate /etc/letsencrypt/live/noatech-ip/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/noatech-ip/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_tickets off;
    client_max_body_size 1m;
    client_body_timeout 15s;
    client_header_timeout 15s;
    send_timeout 30s;
    limit_req zone=noatech_requests burst=60 nodelay;
    limit_req_status 429;
    limit_conn noatech_connections 30;
    add_header X-Content-Type-Options nosniff always;
    add_header Referrer-Policy same-origin always;
    add_header X-Frame-Options SAMEORIGIN always;
    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
    location / {
        proxy_pass http://127.0.0.1:8501;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 86400;
        proxy_buffering off;
    }
}
CONF
nginx -t
systemctl reload nginx
cat > /etc/systemd/system/noatech-certificate-renew.service <<'UNIT'
[Unit]
Description=Renew NOATECH public IP TLS certificate
After=network-online.target nginx.service

[Service]
Type=oneshot
ExecStart=/opt/noatech/venv/bin/certbot renew --quiet --deploy-hook "/bin/systemctl reload nginx"
UNIT
cat > /etc/systemd/system/noatech-certificate-renew.timer <<'UNIT'
[Unit]
Description=Check NOATECH short-lived TLS certificate twice daily

[Timer]
OnCalendar=*-*-* 00,12:15:00
RandomizedDelaySec=900
Persistent=true

[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now noatech-certificate-renew.timer
echo "NOATECH HTTPS configured at https://$ip"
