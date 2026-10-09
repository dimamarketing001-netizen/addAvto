# Deploy behind Nginx

Target hostname: `реклама.союзюристовроссии.рф` (ASCII/Punycode: `xn--80aanufhx.xn--b1ajdba5acbodeeeaj1qb.xn--p1ai`).

This repository does not have shell access to your server. Run these commands on your Linux server over SSH. They add a new Nginx site and do not intentionally modify existing sites.

## 1. Clone and configure the app

```bash
git clone https://github.com/dimamarketing001-netizen/addAvto.git
cd addAvto
cp .env.example .env
nano .env
```

Set at least:
- `APP_ENV=production`
- `APP_USERNAME` to your chosen login
- `APP_PASSWORD` to a unique random password of 16+ characters
- `CLICKRU_API_TOKEN` to a newly rotated token (never use a token posted in chat)
- `CLICKRU_USER_ID` only when required for your Click.ru account context

Protect the file: `chmod 600 .env`.

Start the app. Docker Compose publishes port 8000 on loopback only, so it is not directly exposed to the internet:

```bash
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:8000/health
```

## 2. Install Nginx if needed

Ubuntu/Debian:

```bash
sudo apt update
sudo apt install -y nginx
sudo systemctl enable --now nginx
```

Check existing sites before changing anything:

```bash
sudo nginx -T | less
ls -la /etc/nginx/sites-enabled/
```

## 3. Add this site's HTTP reverse proxy

From the repository root:

```bash
sudo cp deploy/nginx-reklama.conf /etc/nginx/sites-available/reklama.soyuzuristov.conf
sudo ln -s /etc/nginx/sites-available/reklama.soyuzuristov.conf /etc/nginx/sites-enabled/reklama.soyuzuristov.conf
sudo nginx -t
sudo systemctl reload nginx
```

If the symbolic link already exists, do not create a duplicate. Confirm the app responds locally first. Test the hostname via HTTP:

```bash
curl -I http://xn--80aanufhx.xn--b1ajdba5acbodeeeaj1qb.xn--p1ai/
```

If the hostname does not resolve, verify DNS A/AAAA records with the DNS provider before proceeding. The A record must point to this server's public IPv4 address; only keep an AAAA record if IPv6 is correctly routed to this server.

## 4. Issue HTTPS certificate with Certbot

Make sure inbound TCP ports 80 and 443 are allowed by the provider firewall and server firewall. Then:

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d xn--80aanufhx.xn--b1ajdba5acbodeeeaj1qb.xn--p1ai
sudo nginx -t
sudo systemctl reload nginx
sudo certbot renew --dry-run
```

Choose the HTTPS redirect option if Certbot asks. Certbot edits the Nginx site to add TLS and HTTP-to-HTTPS redirection. It uses the existing Nginx server name in Punycode, which avoids terminal/IDN encoding issues.

## 5. Verify

```bash
curl -I https://xn--80aanufhx.xn--b1ajdba5acbodeeeaj1qb.xn--p1ai/
curl -fsS https://xn--80aanufhx.xn--b1ajdba5acbodeeeaj1qb.xn--p1ai/health
sudo nginx -t
sudo systemctl status nginx --no-pager
docker compose logs --tail=100 app
```

The home page should request HTTP Basic Auth. The `/health` endpoint is intentionally a minimal health check and does not expose Click.ru credentials.

## Important

- No server commands have been executed remotely by this repository change; you must run them on your server, or provide an authorized remote shell workflow.
- Do not expose port 8000 publicly. Keep Compose bound to `127.0.0.1:8000`; only Nginx should accept public traffic.
- Keep HTTPS enabled. HTTP Basic Auth is unsafe over plain HTTP.
- Do not place tokens/passwords in Nginx config or commit a real `.env`.
- Check `nginx -T` before changes because the same server may host other domains.
