# RU edge

Tenants whose visitors are in Russia embed the widget from `widget-ru.getchat9.live`
and talk to `api-ru.getchat9.live`. Both names point at a single VM running nginx,
which proxies the widget to Vercel (`widget.getchat9.live`) and the API to Railway.

`chat9-ru-edge.conf` is the source of truth for that nginx site. Change it here,
in a PR, and then apply it on the VM — never edit the VM copy by hand.

The VM also hosts unrelated sites in their own files under `sites-available/`.
Touching them must not touch this file, and the reverse.

## Apply

```bash
scp deploy/ru-edge/chat9-ru-edge.conf <vm>:/tmp/
ssh <vm>
sudo cp -p /etc/nginx/sites-available/chat9-ru-edge /etc/nginx/chat9-ru-edge.bak.$(date +%s)
sudo cp /tmp/chat9-ru-edge.conf /etc/nginx/sites-available/chat9-ru-edge
sudo nginx -t && sudo systemctl reload nginx
```

The file declares the `widget_cache` zone itself, so `nginx.conf` must not declare
it as well (`nginx -t` reports a duplicate zone otherwise).

## Watching it

`.github/workflows/ru-edge-check.yml` probes the edge every 15 minutes the way a
browser inside the widget iframe does, and fails on a Vercel challenge
(`x-vercel-mitigated`), a non-200 widget page or asset, or an unhealthy API.
The cached location must cover every path the iframe loads: anything that falls
through to `location /` reaches Vercel with the visitor's browser headers and
can be challenged.
