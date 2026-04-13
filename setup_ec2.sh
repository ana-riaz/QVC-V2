#!/bin/bash
# Quick setup script for EC2 - Copy this to your EC2 instance and run: bash setup.sh

set -e

echo "=========================================="
echo "Qatar Visa Bot - EC2 Auto-Setup Script"
echo "=========================================="
echo

# Check if running as root
if [[ $EUID == 0 ]]; then
   echo "This script should NOT be run as root. Exiting."
   exit 1
fi

# Update system
echo "[1/10] Updating system packages..."
sudo apt update
sudo apt upgrade -y

# Install Python and tools
echo "[2/10] Installing Python 3.10 and build tools..."
sudo apt install -y python3.10 python3.10-venv python3.10-dev
sudo apt install -y build-essential libssl-dev libffi-dev
sudo apt install -y git curl wget

# Install Chromium
echo "[3/10] Installing Chromium (browser automation)..."
sudo apt install -y chromium chromium-driver

# Install MongoDB (optional - only if running locally)
read -p "Install MongoDB locally? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "[4/10] Installing MongoDB..."
    curl -fsSL https://www.mongodb.org/static/pgp/server-6.0.asc | sudo apt-key add -
    echo "deb [ arch=amd64,arm64 ] https://repo.mongodb.org/apt/ubuntu jammy/mongodb-org/6.0 multiverse" | sudo tee /etc/apt/sources.list.d/mongodb-org-6.0.list
    sudo apt update
    sudo apt install -y mongodb-org
    sudo systemctl start mongod
    sudo systemctl enable mongod
else
    echo "[4/10] Skipping MongoDB (use MongoDB Atlas or cloud service)"
fi

# Install Nginx (optional)
read -p "Install Nginx for reverse proxy? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "[5/10] Installing Nginx..."
    sudo apt install -y nginx
else
    echo "[5/10] Skipping Nginx"
fi

# Clone repository
echo "[6/10] Setting up application directory..."
mkdir -p ~/apps
cd ~/apps

read -p "Enter your GitHub repository URL: " REPO_URL

if [ -d "qvc-v2" ]; then
    echo "Directory qvc-v2 already exists. Updating..."
    cd qvc-v2
    git pull origin main
else
    echo "Cloning repository..."
    git clone "$REPO_URL" qvc-v2
    cd qvc-v2
fi

# Create virtual environment
echo "[7/10] Creating Python virtual environment..."
python3.10 -m venv venv
source venv/bin/activate
pip install --upgrade pip setuptools wheel

# Install dependencies
echo "[8/10] Installing Python dependencies..."
pip install -r requirements.txt

# Setup environment
echo "[9/10] Setting up environment configuration..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env - PLEASE EDIT WITH YOUR VALUES:"
    echo "  nano .env"
    read -p "Press Enter after editing .env (save with Ctrl+X, Y, Enter)"
else
    echo ".env already exists"
fi

# Install systemd service
echo "[10/10] Installing systemd service..."
sudo cp qvc-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable qvc-bot.service

# Setup Nginx if installed
if command -v nginx &> /dev/null; then
    read -p "Configure Nginx reverse proxy? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        sudo cp nginx.conf /etc/nginx/sites-available/qvc-bot
        sudo rm -f /etc/nginx/sites-enabled/default
        sudo ln -sf /etc/nginx/sites-available/qvc-bot /etc/nginx/sites-enabled/
        sudo nginx -t && sudo systemctl restart nginx
        PROXY_ENABLED=true
    fi
fi

echo
echo "=========================================="
echo "Setup Complete!"
echo "=========================================="
echo
echo "Next steps:"
echo "1. Edit your .env file with actual values:"
echo "   nano ~/apps/qvc-v2/.env"
echo
echo "2. Start the application:"
if [ "$PROXY_ENABLED" = true ]; then
    echo "   sudo systemctl start qvc-bot.service"
    echo "   Access at: http://$(hostname -I | awk '{print $1}')"
else
    echo "   sudo systemctl start qvc-bot.service"
    echo "   Access at: http://$(hostname -I | awk '{print $1}'):8000"
fi
echo
echo "3. Check status:"
echo "   sudo systemctl status qvc-bot.service"
echo
echo "4. View logs:"
echo "   sudo journalctl -u qvc-bot.service -f"
echo
echo "For more information, see: ~/apps/qvc-v2/EC2_DEPLOYMENT.md"
echo
