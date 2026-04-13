# Changes Made for EC2 Deployment

## Summary

**Good news**: The application is already designed for cloud deployment! No code changes were required. The following deployment configuration files have been added to make EC2 deployment straightforward.

---

## New Files Added

### 1. **`.env.example`**
- Template for environment variables
- Shows all available configuration options
- Include sensitive keys like API credentials, database URIs, etc.
- Copy to `.env` on EC2 and fill in your actual values

### 2. **`EC2_DEPLOYMENT.md`** ⭐ *Start here*
- Complete step-by-step deployment guide
- Covers AWS Security Group configuration
- SSH connection instructions
- System dependencies installation
- Application setup and configuration
- Systemd service setup for auto-start
- Nginx reverse proxy setup (optional)
- Troubleshooting section
- **Recommended length: 15-20 minutes to read**

### 3. **`DEPLOYMENT_CHECKLIST.md`**
- Verification checklist for deployment process
- Covers all pre-deployment, deployment, and post-deployment checks
- Use this to ensure nothing is missed
- Good reference for future deployments

### 4. **`QUICK_START_EC2.md`**
- Fast reference guide for common tasks
- TL;DR section for quick setup
- Command reference
- Architecture diagram
- Common problems & solutions
- Security checklist
- **Best for experienced users**

### 5. **`setup_ec2.sh`**
- Automated setup script for EC2
- Installs all system dependencies
- Clones repository
- Creates Python virtual environment
- Installs Python packages
- Configures systemd service
- Optional: Installs and configures Nginx
- **Run this on EC2 to automate 90% of setup**

### 6. **`nginx.conf`**
- Nginx reverse proxy configuration
- Allows running application on port 80 instead of port 8000
- Includes security headers
- Gzip compression enabled
- Static file caching configured
- Optional - only needed if using Nginx

### 7. **`qvc-bot.service`**
- Systemd service file for auto-start and management
- Application auto-restarts on crash
- Copied to `/etc/systemd/system/` on EC2
- Enables running `sudo systemctl start qvc-bot.service`

---

## Why No Code Changes Were Needed

### Backend (web_server.py) ✅
- Already listens on `0.0.0.0` (accessible from outside)
- Reads `HOST` and `PORT` from environment variables
- CORS enabled with `allow_origins=["*"]`
- Static files mounted correctly
- No hardcoded localhost references

### Frontend (app.js) ✅
- Uses relative API URLs: `const API_BASE = ''`
- Dynamically communicates with backend at current host/IP
- No hardcoded domain names
- Works on localhost, EC2 IP, or custom domain

### Configuration (config.py) ✅
- All important settings loaded from environment variables
- Database URI: `MONGODB_URI` from env
- API keys: `TWOCAPTCHA_API_KEY`, `CAPSOLVER_API_KEY` from env
- Server settings: `HOST`, `PORT` from env
- No hardcoded sensitive data

### Database (db.py) ✅
- Uses `MONGODB_URI` from config for connection
- Works with local MongoDB or MongoDB Atlas (cloud)
- Connection string is configurable per environment

---

## What You Need to Do

### Step 1: Push These Changes to Git
```bash
git add .
git commit -m "Add EC2 deployment configuration files

- Add .env.example with all configuration options
- Add EC2_DEPLOYMENT.md with complete deployment guide
- Add DEPLOYMENT_CHECKLIST.md for verification
- Add QUICK_START_EC2.md for quick reference
- Add setup_ec2.sh for automated setup
- Add nginx.conf for reverse proxy
- Add qvc-bot.service for systemd management

No code changes required - app is already EC2-ready!"

git push origin main
```

### Step 2: On Your EC2 Instance
```bash
# SSH into EC2
ssh -i your-key.pem ubuntu@YOUR-EC2-IP

# Clone repository
git clone <your-repo-url> qvc-v2
cd qvc-v2

# Run automated setup (recommended)
bash setup_ec2.sh

# OR follow EC2_DEPLOYMENT.md step-by-step
```

### Step 3: Configure Environment
```bash
# Edit .env with your actual values
nano .env

# Add at minimum:
# - MONGODB_URI=your_database_url
# - TWOCAPTCHA_API_KEY=your_key
# - CAPSOLVER_API_KEY=your_key
# - HEADLESS=True
# - HOST=0.0.0.0
# - PORT=8000
```

### Step 4: Start the Application
```bash
sudo systemctl start qvc-bot.service
sudo systemctl status qvc-bot.service

# Access at: http://YOUR-EC2-IP:8000
```

---

## Testing Checklist

After deployment, verify:
- [ ] Frontend loads: `http://EC2-IP:8000`
- [ ] Health check: `http://EC2-IP:8000/health`
- [ ] Can add applicants
- [ ] Can start/stop bot
- [ ] Browser automation works
- [ ] Database connection works
- [ ] Logs display in real-time
- [ ] Service auto-restarts after stop

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Your EC2 Instance                     │
│                                                          │
│  ┌──────────────────────────────────────────────────┐  │
│  │ FastAPI + Uvicorn (Port 8000)                   │  │
│  │  • Serves frontend (HTML/CSS/JS)                 │  │
│  │  • Provides REST API (/api/*)                    │  │
│  │  • Runs browser automation                       │  │
│  └────────────────────┬─────────────────────────────┘  │
│                       │                                  │
│  ┌────────────────────▼─────────────────────────────┐  │
│  │ MongoDB (local or MongoDB Atlas cloud)          │  │
│  │  • Stores applicants                             │  │
│  │  • Stores schedule & settings                    │  │
│  │  • Stores run history                            │  │
│  └──────────────────────────────────────────────────┘  │
│                                                          │
│  Optional:                                               │
│  ┌──────────────────────────────────────────────────┐  │
│  │ Nginx (Port 80) ← Reverse proxy                  │  │
│  │ Proxies to FastAPI on port 8000                  │  │
│  └──────────────────────────────────────────────────┘  │
│                                                          │
│  Security Group Inbound Rules:                           │
│  • Port 22 (SSH) from your IP                           │
│  • Port 8000 (App) from 0.0.0.0/0                       │
│  • Port 80 (HTTP, if Nginx) from 0.0.0.0/0              │
└─────────────────────────────────────────────────────────┘
```

---

## File Locations on EC2

After setup, your files will be at:
```
/home/ubuntu/apps/qvc-v2/
├── .env                    ← Your configuration (never commit)
├── venv/                   ← Python virtual environment
├── web/                    ← Frontend files
│   ├── index.html
│   ├── app.js
│   └── styles.css
├── web_server.py           ← Main application
├── config.py               ← Configuration loader
├── db.py                   ← Database client
├── browser_engine.py       ← Browser automation
└── [other Python files]

EC2 System files:
/etc/systemd/system/qvc-bot.service   ← Service definition
/etc/nginx/sites-available/qvc-bot    ← Nginx config (if using)
```

---

## Key Environment Variables

Minimum `.env` for EC2:

```env
# Server
HOST=0.0.0.0
PORT=8000
HEADLESS=True

# Database - IMPORTANT: Change this to your URL
MONGODB_URI=mongodb://localhost:27017
# or for MongoDB Atlas:
MONGODB_URI=mongodb+srv://user:password@cluster.mongodb.net/

# API Keys - IMPORTANT: Add your actual keys
TWOCAPTCHA_API_KEY=your_2captcha_key_here
CAPSOLVER_API_KEY=your_capsolver_key_here
```

See `.env.example` for all available options.

---

## Common Scenarios

### Scenario 1: Basic Setup (No Nginx)
```bash
# Access directly on port 8000
http://EC2-IP:8000
# Access with public DNS
http://your-domain.com:8000
```

### Scenario 2: With Nginx Reverse Proxy
```bash
# Access on standard HTTP port 80
http://EC2-IP
# Access with public DNS
http://your-domain.com
```

### Scenario 3: With HTTPS (Let's Encrypt)
```bash
# Access on HTTPS
https://your-domain.com
# Automatic certificate renewal
```

---

## Auto-Recovery

The systemd service is configured to:
- ✅ Auto-start on EC2 reboot
- ✅ Auto-restart if process crashes
- ✅ Wait 10 seconds between restart attempts
- ✅ Log all output to systemd journal

Monitor with:
```bash
sudo systemctl status qvc-bot.service
sudo journalctl -u qvc-bot.service -f
```

---

## Security Notes

1. **Never commit `.env`** - It contains sensitive API keys
2. **AWS Security Group** - Restrict ports to what's needed
3. **SSH Key** - Keep key file safe (`chmod 400`)
4. **API Keys** - Use separate keys for EC2 than development
5. **HTTPS** - Use Let's Encrypt for free SSL certificate
6. **Database Password** - Use strong credentials
7. **Backups** - Regular backups of MongoDB data

---

## Next Steps

1. **Read**: EC2_DEPLOYMENT.md (detailed guide)
2. **Quick ref**: QUICK_START_EC2.md (command reference)
3. **Verify**: DEPLOYMENT_CHECKLIST.md (nothing missed)
4. **Run**: bash setup_ec2.sh (on EC2)
5. **Configure**: Edit .env with your values
6. **Start**: sudo systemctl start qvc-bot.service
7. **Check**: http://YOUR-EC2-IP:8000 in browser

---

## Support & Troubleshooting

**Check the logs:**
```bash
sudo journalctl -u qvc-bot.service -f
```

**Common issues:**
- See QUICK_START_EC2.md → "Common Problems & Solutions"
- See EC2_DEPLOYMENT.md → "Troubleshooting"

**Key commands:**
```bash
# Service control
sudo systemctl {start|stop|restart|status} qvc-bot.service

# View logs
sudo journalctl -u qvc-bot.service -f

# Test connectivity
curl http://localhost:8000/health

# Check if port is in use
sudo lsof -i :8000
```

---

## Deployment Success ✅

When you see this in your browser:
```
Qatar Visa Bot
Automated Appointment Booking
[Status indicators, applicant list, run bot button]
```

**Congratulations! Your deployment is complete.**

For ongoing management, see QUICK_START_EC2.md for monitoring and maintenance commands.
