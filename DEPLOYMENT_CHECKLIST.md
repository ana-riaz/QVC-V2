# EC2 Deployment Checklist

## Pre-Deployment (Local Machine)

- [ ] Code is committed and ready to push
- [ ] All changes are made and tested locally
- [ ] `.env` is added to `.gitignore`
- [ ] Database connection string is valid
- [ ] API keys (2Captcha, CapSolver, etc.) are securely stored
- [ ] Repository is up to date: `git pull origin main`

---

## AWS Configuration

- [ ] EC2 instance is running (check AWS Console)
- [ ] Instance type has sufficient resources (recommend t3.medium or larger)
- [ ] Security Group allows:
  - [ ] Inbound Port 22 (SSH) from your IP
  - [ ] Inbound Port 8000 (App) from 0.0.0.0/0
  - [ ] Inbound Port 80 (HTTP, if using Nginx) from 0.0.0.0/0
- [ ] Elastic IP is assigned (recommended for static IP)
- [ ] Outbound rules allow internet access

---

## SSH Access

- [ ] Key pair file (.pem) is downloaded and accessible
- [ ] Key file has correct permissions: `chmod 400 key.pem`
- [ ] Can connect: `ssh -i key.pem ubuntu@<EC2-IP>`
- [ ] Instance shows as "running" in AWS Console

---

## Server Setup (First Time)

- [ ] Connected to EC2 via SSH
- [ ] System updated: `sudo apt update && sudo apt upgrade -y`
- [ ] Python 3.10 installed
- [ ] Build tools installed: `sudo apt install build-essential libssl-dev libffi-dev`
- [ ] Chromium installed: `sudo apt install chromium chromium-driver`
- [ ] Git installed: `sudo apt install git`
- [ ] MongoDB installed or cloud MongoDB URI configured
- [ ] Python venv created: `python3.10 -m venv venv`
- [ ] Dependencies installed: `pip install -r requirements.txt`

---

## Application Configuration

- [ ] `.env` file created on EC2 with proper values
- [ ] Database connection verified: `MONGODB_URI` is correct
- [ ] API keys configured: `TWOCAPTCHA_API_KEY`, `CAPSOLVER_API_KEY`
- [ ] Server settings: `HOST=0.0.0.0`, `PORT=8000`
- [ ] `HEADLESS=True` for server environment
- [ ] Log files have write permissions

---

## First Run

- [ ] Application starts: `python web_server.py`
- [ ] Server binds to port 8000
- [ ] No errors in console output
- [ ] Can access frontend: `http://<EC2-IP>:8000`
- [ ] Health check returns: `http://<EC2-IP>:8000/health`
- [ ] Static files load correctly (CSS, JS visible)
- [ ] No database connection errors

---

## Systemd Service Setup

- [ ] Service file created: `/etc/systemd/system/qvc-bot.service`
- [ ] Service reloaded: `sudo systemctl daemon-reload`
- [ ] Service enabled for auto-start: `sudo systemctl enable qvc-bot.service`
- [ ] Service started: `sudo systemctl start qvc-bot.service`
- [ ] Service status is "active (running)": `sudo systemctl status qvc-bot.service`
- [ ] Service auto-restarts after manual stop: `sudo systemctl stop qvc-bot.service && sleep 5 && sudo systemctl status qvc-bot.service`

---

## Nginx Setup (Optional - Port 80)

- [ ] Nginx installed: `sudo apt install nginx`
- [ ] Config file created: `/etc/nginx/sites-available/qvc-bot`
- [ ] Site enabled: symbolic link in sites-enabled
- [ ] Nginx config tested: `sudo nginx -t` shows OK
- [ ] Nginx restarted: `sudo systemctl restart nginx`
- [ ] Can access on port 80: `http://<EC2-IP>` (no `:8000`)
- [ ] Static files load on port 80

---

## Post-Deployment Testing

- [ ] Frontend loads in browser
- [ ] Can create applicants via web UI
- [ ] Can start/stop bot sessions
- [ ] Logs display in real-time
- [ ] Database queries work
- [ ] Browser automation engine starts correctly
- [ ] No SSL certificate warnings (if using HTTPS)

---

## Security

- [ ] `.env` file permissions restricted: `chmod 600 .env`
- [ ] SSH key file permissions: `chmod 400 key.pem`
- [ ] Firewall configured correctly (AWS Security Group)
- [ ] No private keys in git repository
- [ ] No API keys logged in console output
- [ ] Database connection uses strong password or Atlas credentials

---

## Monitoring & Logs

- [ ] Can view service logs: `sudo journalctl -u qvc-bot.service -f`
- [ ] App logs are being written to: `/home/ubuntu/apps/qvc-v2/visa_bot.log`
- [ ] No errors in last 100 log lines: `sudo journalctl -u qvc-bot.service -n 100`
- [ ] Health check works: `curl -s http://localhost:8000/health | jq`

---

## Database

- [ ] MongoDB is running (if local): `sudo systemctl status mongodb`
- [ ] Database connection test passes
- [ ] Initial data can be created
- [ ] Applicants table/collection is empty or has test data
- [ ] Backup strategy is in place

---

## Final Validation

- [ ] Application accessible from external IP: `http://<EC2-PUBLIC-IP>:8000`
- [ ] Frontend is fully functional
- [ ] No console errors in browser DevTools
- [ ] Bot can successfully poll for slots
- [ ] Service survives EC2 reboot: `sudo reboot`
- [ ] All scheduled tasks work correctly

---

## Documentation

- [ ] EC2_DEPLOYMENT.md is in repository
- [ ] .env.example is documented with all variables
- [ ] README.md updated with EC2 access information
- [ ] Team has access to:
  - [ ] EC2 public IP
  - [ ] SSH key file
  - [ ] .env configuration (securely)
  - [ ] Deployment guide

---

## Push to Repository

- [ ] All code changes committed
- [ ] `.env` and sensitive files in `.gitignore`
- [ ] Deployment files pushed:
  - [ ] EC2_DEPLOYMENT.md
  - [ ] DEPLOYMENT_CHECKLIST.md
  - [ ] .env.example
  - [ ] nginx.conf
  - [ ] qvc-bot.service
- [ ] Repository main branch is clean: `git status`

---

## Ongoing Maintenance

- [ ] Set up regular backups (daily MongoDB dumps)
- [ ] Monitor EC2 metrics (CloudWatch)
- [ ] Monitor disk space usage
- [ ] Rotate logs periodically
- [ ] Keep dependencies updated monthly
- [ ] Test recovery procedures quarterly

---

## Emergency Contacts & Notes

**EC2 Instance ID:** ___________________________

**Public IP:** ___________________________

**Elastic IP:** ___________________________

**Key Pair Name:** ___________________________

**Database Connection:** ___________________________

**Dashboard URL:** http://___________________________ :8000

**Support Contact:** ___________________________

**Last Deployment Date:** ___________________________

---

## Common Commands Reference

```bash
# Connect to EC2
ssh -i key.pem ubuntu@<IP>

# Check service status
sudo systemctl status qvc-bot.service

# View logs
sudo journalctl -u qvc-bot.service -f

# Restart service
sudo systemctl restart qvc-bot.service

# Check app health
curl http://localhost:8000/health

# Monitor resources
top
htop

# View database
mongo
use qatar_visa_bot
db.applicants.find().pretty()
```

---

**Note:** Keep this checklist updated as you perform each step. It serves as both a deployment guide and a verification record.
