# Quick Reference Guide - EC2 Deployment

## TL;DR - Fast Deployment

### 1. AWS Configuration (5 minutes)

Go to AWS Console → EC2 → Security Groups:
- Add inbound rule: Custom TCP, Port **8000**, Source **0.0.0.0/0**
- Add inbound rule: SSH, Port **22**, Source **Your IP**

### 2. SSH into EC2 (2 minutes)

```bash
chmod 400 your-key.pem
ssh -i your-key.pem ubuntu@YOUR-EC2-PUBLIC-IP
```

### 3. Run Auto-Setup (10 minutes)

```bash
# Clone your repo first
git clone <your-repo-url> qvc-v2
cd qvc-v2

# Option A: Run the setup script
bash setup_ec2.sh

# Option B: Manual setup (see EC2_DEPLOYMENT.md for details)
```

### 4. Configure Environment

```bash
nano .env
# Edit these minimum values:
# - HOST=0.0.0.0
# - PORT=8000
# - MONGODB_URI=your_db_connection
# - TWOCAPTCHA_API_KEY=your_key
# - HEADLESS=True

# Save with Ctrl+X, Y, Enter
```

### 5. Start Service

```bash
sudo systemctl start qvc-bot.service
sudo systemctl status qvc-bot.service  # Should show "active (running)"
```

### 6. Access Your App

Open in browser: `http://YOUR-EC2-PUBLIC-IP:8000`

---

## System Architecture

```
┌─────────────────────────────────────┐
│     Your Browser (Local Computer)   │
│  http://EC2-PUBLIC-IP:8000/         │
└──────────────┬──────────────────────┘
               │ HTTPS/HTTP
               ▼
┌─────────────────────────────────────┐
│           EC2 Instance               │
│  ┌───────────────────────────────┐  │
│  │   FastAPI + Uvicorn (8000)    │  │
│  │  - Frontend (HTML/JS/CSS)     │  │
│  │  - Backend API                │  │
│  │  - Browser Automation         │  │
│  └───────────────┬───────────────┘  │
│                  │                   │
│  ┌───────────────▼───────────────┐  │
│  │   MongoDB (local or cloud)    │  │
│  │   - Applicants                │  │
│  │   - Schedule                  │  │
│  │   - Run records               │  │
│  └───────────────────────────────┘  │
└─────────────────────────────────────┘
```

---

## Directory Structure on EC2

```
/home/ubuntu/apps/qvc-v2/
├── venv/                    # Python virtual environment
├── web/                     # Frontend files
│   ├── index.html
│   ├── app.js
│   └── styles.css
├── .env                     # Environment variables (NOT in git)
├── .env.example             # Template for .env
├── web_server.py            # Main app entry point
├── config.py                # Configuration
├── db.py                    # MongoDB client
├── browser_engine.py        # Browser automation
├── requirements.txt         # Python dependencies
├── EC2_DEPLOYMENT.md        # Full deployment guide
├── DEPLOYMENT_CHECKLIST.md  # Verification checklist
├── setup_ec2.sh             # Automated setup script
├── nginx.conf               # Nginx config (optional)
├── qvc-bot.service          # Systemd service
└── visa_bot.log             # Application logs
```

---

## Environment Variables Reference

### Required for EC2
- `HOST=0.0.0.0` - Listen on all interfaces
- `PORT=8000` - Application port
- `MONGODB_URI=mongodb+srv://...` - Database connection
- `HEADLESS=True` - Run in server mode

### API Keys
- `TWOCAPTCHA_API_KEY=xxx` - For CAPTCHA solving (primary)
- `CAPSOLVER_API_KEY=xxx` - For CAPTCHA solving (fallback)

### Optional
- `PROXY_ENABLED=False` - Enable proxy rotation
- `S3_LOGGING_ENABLED=False` - AWS S3 logging

See `.env.example` for all available options.

---

## Common Problems & Solutions

| Problem | Solution |
|---------|----------|
| "Connection refused" | Check security group allows port 8000 |
| "Cannot access from browser" | Verify EC2 public IP and port 8000 is open |
| "Service won't start" | Check logs: `sudo journalctl -u qvc-bot.service -n 50` |
| "Frontend not loading" | Check that `web/` directory exists in app folder |
| "Database error" | Verify `MONGODB_URI` in `.env` is correct |
| "Browser automation fails" | Ensure Chromium is installed: `which chromium` |

---

## Useful Commands

```bash
# Service Management
sudo systemctl start qvc-bot.service
sudo systemctl stop qvc-bot.service
sudo systemctl restart qvc-bot.service
sudo systemctl status qvc-bot.service
sudo systemctl enable qvc-bot.service      # Auto-start on reboot

# Logs
sudo journalctl -u qvc-bot.service -f      # Live logs
sudo journalctl -u qvc-bot.service -n 100  # Last 100 lines
sudo journalctl -u qvc-bot.service --since "2 hours ago"

# Health Check
curl http://localhost:8000/health           # Check API
curl http://YOUR-EC2-IP:8000/health         # From outside

# Database
mongo                                        # Connect to MongoDB (if local)
show dbs
use visa_bot
db.applicants.find().count()

# System Resources
top                                         # Real-time resources
htop                                        # Better top interface
df -h                                       # Disk usage
free -h                                     # Memory usage

# File Permissions
sudo chown ubuntu:ubuntu *.log              # Fix log ownership
chmod 644 .env                              # Protect .env file
```

---

## Security Checklist

- [ ] `.env` not in git repository
- [ ] Security group only allows necessary ports
- [ ] API keys never logged to console
- [ ] HTTPS enabled (use Let's Encrypt for free SSL)
- [ ] SSH key file permissions: `chmod 400 key.pem`
- [ ] MongoDB requires authentication (if cloud)
- [ ] Regular backups configured

---

## Monitoring & Maintenance

### Daily
- Check service is running: `sudo systemctl status qvc-bot.service`
- Review logs for errors

### Weekly
- Monitor disk space: `df -h`
- Check memory usage: `free -h`
- Verify database connectivity

### Monthly
- Update packages: `sudo apt update && sudo apt upgrade`
- Backup database (if local MongoDB)
- Review MongoDB connection limits

### Quarterly
- Test disaster recovery
- Review security settings
- Clean up old logs

---

## Backup & Disaster Recovery

### Backup MongoDB data
```bash
# Local MongoDB
mongodump --out=/home/ubuntu/backup-$(date +%Y%m%d)

# MongoDB Atlas - use built-in backup (automatic)
```

### Backup application code
```bash
cd /home/ubuntu/apps
tar -czf qvc-v2-backup-$(date +%Y%m%d).tar.gz qvc-v2/
```

### Restore from backup
```bash
cd /home/ubuntu/apps
tar -xzf qvc-v2-backup-DATE.tar.gz
```

---

## Advanced: Nginx Reverse Proxy (Port 80)

If you want to run on port 80 instead of 8000:

```bash
# Install Nginx
sudo apt install nginx

# Copy config
sudo cp nginx.conf /etc/nginx/sites-available/qvc-bot

# Enable site
sudo rm /etc/nginx/sites-enabled/default
sudo ln -s /etc/nginx/sites-available/qvc-bot /etc/nginx/sites-enabled/

# Test and restart
sudo nginx -t
sudo systemctl restart nginx

# Now access at: http://YOUR-EC2-IP (no :8000 needed)
```

---

## HTTPS/SSL Setup

Using Let's Encrypt (free):

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d yourdomain.com
# Auto-renewal runs automatically via cron
```

---

## Next Steps

1. **Complete EC2_DEPLOYMENT.md** for detailed instructions
2. **Use DEPLOYMENT_CHECKLIST.md** to verify each step
3. **Run `bash setup_ec2.sh`** for automated setup
4. **Edit `.env`** with your API keys and settings
5. **Start service** with `sudo systemctl start qvc-bot.service`
6. **Access dashboard** at `http://YOUR-EC2-IP:8000`

---

## Questions?

- Check logs: `sudo journalctl -u qvc-bot.service -f`
- Read EC2_DEPLOYMENT.md for detailed guide
- Check https://fastapi.tiangolo.com/deployment/
- See requirements.txt for dependency versions
