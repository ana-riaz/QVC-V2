# AWS EC2 Deployment Guide - Qatar Visa Bot

## Prerequisites
- EC2 instance already launched (Ubuntu 22.04 LTS recommended)
- Security group configured
- SSH access to the instance
- Public IP or Elastic IP assigned

---

## STEP 1: Configure AWS Security Group

Your EC2 instance's security group must allow inbound traffic on:

1. **Port 8000** (Application - required)
   - Type: Custom TCP
   - Port: 8000
   - Source: `0.0.0.0/0` (or restrict to your IP)

2. **Port 22** (SSH - for management)
   - Type: SSH
   - Port: 22
   - Source: Your IP address

3. **Port 80** (Optional - if using Nginx reverse proxy)
   - Type: HTTP
   - Port: 80
   - Source: `0.0.0.0/0`

**To add security group rules:**
1. Go to AWS Console → EC2 → Security Groups
2. Select your security group
3. Click "Edit inbound rules"
4. Add the above rules
5. Click "Save rules"

---

## STEP 2: Connect to EC2 Instance

**Command:**
```bash
ssh -i your-key.pem ec2-user@your-ec2-ip
# or for Ubuntu AMI:
ssh -i your-key.pem ubuntu@your-ec2-public-ip
```

**Replace:**
- `your-key.pem` - Your EC2 key pair file
- `your-ec2-public-ip` - The public IP of your EC2 instance (from AWS Console)

---

## STEP 3: Install Dependencies

Once connected to EC2, run:

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Python and system dependencies
sudo apt install -y python3.10 python3.10-venv python3.10-dev
sudo apt install -y build-essential libssl-dev libffi-dev

# Install Chromium for browser automation
sudo apt install -y chromium chromium-driver

# Install MongoDB (if running locally) - SKIP if using cloud MongoDB
sudo apt install -y mongodb

# Install Git
sudo apt install -y git

# Install Nginx (optional, for reverse proxy)
sudo apt install -y nginx
```

---

## STEP 4: Clone Repository and Setup

```bash
# Create application directory
mkdir -p /home/ubuntu/apps
cd /home/ubuntu/apps

# Clone your repository
git clone <your-repo-url> qvc-v2
cd qvc-v2

# Create Python virtual environment
python3.10 -m venv venv
source venv/bin/activate

# Upgrade pip
pip install --upgrade pip

# Install dependencies
pip install -r requirements.txt
```

---

## STEP 5: Configure Environment Variables

```bash
# Copy example environment file
cp .env.example .env

# Edit environment variables
nano .env
```

**Important configurations for EC2:**

```env
# Server - EC2 will bind to all interfaces
HOST=0.0.0.0
PORT=8000

# Add your captcha API keys
TWOCAPTCHA_API_KEY=your_key
CAPSOLVER_API_KEY=your_key

# Database - adjust for your setup
MONGODB_URI=mongodb://localhost:27017
# or if using MongoDB Atlas:
MONGODB_URI=mongodb+srv://user:pass@cluster.mongodb.net/

# Proxy settings (optional)
PROXY_ENABLED=False

# Enable headless mode for server
HEADLESS=True
```

**Save and exit:** (Ctrl+X, Y, Enter)

---

## STEP 6: Test Run Application

```bash
# Make sure you're in the virtual environment
source venv/bin/activate
cd /home/ubuntu/apps/qvc-v2

# Run the application
python web_server.py
```

**Expected output:**
```
============================================================
Qatar Visa Bot - Web Control Panel (Parallel Mode)
============================================================

Server starting on 0.0.0.0:8000
Open in browser: http://localhost:8000
Press Ctrl+C to stop
```

**Test in browser:**
- Open: `http://<your-ec2-public-ip>:8000`
- You should see the Qatar Visa Bot control panel
- Check health: `http://<your-ec2-public-ip>:8000/health`

Once verified working, press Ctrl+C to stop.

---

## STEP 7: Setup Systemd Service (Auto-Start & Management)

Create a systemd service to run the bot automatically on restart:

```bash
# Create service file
sudo nano /etc/systemd/system/qvc-bot.service
```

**Paste this content:**

```ini
[Unit]
Description=Qatar Visa Bot Service
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/apps/qvc-v2
Environment="PATH=/home/ubuntu/apps/qvc-v2/venv/bin"
ExecStart=/home/ubuntu/apps/qvc-v2/venv/bin/python web_server.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**Enable and start the service:**

```bash
# Reload systemd
sudo systemctl daemon-reload

# Enable on startup
sudo systemctl enable qvc-bot.service

# Start the service
sudo systemctl start qvc-bot.service

# Check status
sudo systemctl status qvc-bot.service

# View logs (live)
sudo journalctl -u qvc-bot.service -f

# View recent logs
sudo journalctl -u qvc-bot.service --lines=50
```

---

## STEP 8: (Optional) Setup Nginx Reverse Proxy

If you want to run on port 80 instead of 8000:

```bash
# Create Nginx config
sudo nano /etc/nginx/sites-available/qvc-bot
```

**Paste this content:**

```nginx
upstream qvc_backend {
    server 127.0.0.1:8000;
}

server {
    listen 80;
    server_name _;
    
    client_max_body_size 10M;

    location / {
        proxy_pass http://qvc_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 86400;
    }
}
```

**Enable the site:**

```bash
# Create symbolic link
sudo ln -s /etc/nginx/sites-available/qvc-bot /etc/nginx/sites-enabled/

# Remove default site
sudo rm /etc/nginx/sites-enabled/default

# Test Nginx config
sudo nginx -t

# Restart Nginx
sudo systemctl restart nginx
```

**Now access from:** `http://<your-ec2-public-ip>` (port 80)

---

## STEP 9: Push Changes to Repository

Before pushing, you might need to add environment files to `.gitignore`:

```bash
cd /home/ubuntu/apps/qvc-v2

# Make sure .env is in .gitignore
echo ".env" >> .gitignore
echo "*.log" >> .gitignore
echo "logs/" >> .gitignore
echo "__pycache__/" >> .gitignore
```

**Push your changes:**

```bash
# Add all changes
git add .

# Commit
git commit -m "Add EC2 deployment configuration"

# Push to repository
git push origin main
```

---

## STEP 10: Update on EC2

When you push new code, update on EC2:

```bash
cd /home/ubuntu/apps/qvc-v2
source venv/bin/activate

# Pull latest code
git pull origin main

# Install any new dependencies
pip install -r requirements.txt

# Restart the service
sudo systemctl restart qvc-bot.service

# Check status
sudo systemctl status qvc-bot.service
```

---

## Accessing Your Application

### Direct Access (Port 8000)
```
http://<your-ec2-public-ip>:8000
```

### Via Nginx (Port 80)
```
http://<your-ec2-public-ip>
```

### API Endpoints
- **Health Check:** `GET /health`
- **Get Applicants:** `GET /api/applicants`
- **Add Applicant:** `POST /api/applicants`
- **Run Bot:** `POST /api/run`
- **Stop Bot:** `POST /api/stop`
- **Status:** `GET /api/status`

---

## Troubleshooting

### Application won't start
```bash
# Check logs
sudo journalctl -u qvc-bot.service -n 100

# Verify Python environment
source /home/ubuntu/apps/qvc-v2/venv/bin/activate
python -c "import fastapi; print('FastAPI OK')"
```

### Port already in use
```bash
# Find process using port 8000
sudo lsof -i :8000

# Kill if needed
sudo kill -9 <PID>
```

### Cannot access from browser
1. Verify EC2 security group allows port 8000 (or 80)
2. Check service is running: `sudo systemctl status qvc-bot.service`
3. Check firewall: `sudo ufw status` (if using UFW)

### Frontend not loading
1. Check that static files are mounted correctly
2. Verify `web/` directory exists in `/home/ubuntu/apps/qvc-v2/`
3. Check browser console for errors

---

## Monitoring

**Check application status:**
```bash
sudo systemctl status qvc-bot.service
```

**View running logs in real-time:**
```bash
sudo journalctl -u qvc-bot.service -f
```

**Monitor resource usage:**
```bash
top
# or
htop
```

---

## Backup & Recovery

**Backup your data:**
```bash
cd /home/ubuntu/apps/qvc-v2
tar -czf qvc-backup-$(date +%Y%m%d).tar.gz data/ logs/
```

**Backup MongoDB (if local):**
```bash
mongodump --out=/home/ubuntu/backups/mongo-backup-$(date +%Y%m%d)
```

---

## SSL Certificate (HTTPS)

For production, install Let's Encrypt SSL:

```bash
sudo apt install -y certbot python3-certbot-nginx

# Get certificate
sudo certbot --nginx -d your-domain.com

# Auto-renewal is configured automatically
sudo systemctl status certbot.timer
```

---

## Performance Tips

1. **For parallel browser sessions:** Ensure instance has enough RAM (at least 4GB for 2-3 sessions, 8GB+ for 5+)
2. **Enable shared memory:** If using Docker: `--shm-size=2g`
3. **Monitor resources:** Use `top` or `htop` during bot runs
4. **Use MongoDB Atlas** if you expect high data volume

---

## Additional Resources

- [AWS EC2 Documentation](https://docs.aws.amazon.com/ec2/)
- [FastAPI Deployment](https://fastapi.tiangolo.com/deployment/)
- [Systemd Service Linux](https://www.freedesktop.org/software/systemd/man/systemd.service.html)
- [Nginx Reverse Proxy](https://nginx.org/en/docs/)

---

## Support

For issues:
1. Check the logs: `sudo journalctl -u qvc-bot.service -f`
2. Test manually: Stop service, run `python web_server.py` directly
3. Verify security group and firewall rules
4. Check database connectivity

