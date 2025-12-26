# Paybot deploy (CT101)

## Update code
cd /opt/services/paybot
git pull

## Restart service
systemctl restart paybot
systemctl status paybot --no-pager

## Logs
journalctl -u paybot -n 120 --no-pager
