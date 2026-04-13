# Qatar Visa Center (QVC) Appointment Bot

A powerful, automated appointment booking system for the Qatar Visa Center portal. Designed with a hybrid architecture that combines browser automation for security and API polling for speed.

## 🚀 Key Features

-   **🖥️ Web Control Panel**: A modern FastAPI-powered dashboard for managing applicants, monitoring progress, and scheduling runs.
-   **🕵️ Stealth Browser**: Built on `nodriver` (the next-gen undetected-chromedriver) to bypass advanced bot detection.
-   **🔄 Proxy Rotation**: Integrated with Data Impulse residential proxies to prevent IP rate-limiting and blocks.
-   **🤖 Hybrid CAPTCHA**: Uses local OCR (`ddddocr`) for free solving, falling back to `CapSolver` API for complex challenges.
-   **📅 Automatic Scheduler**: Define specific windows for the bot to run automatically when slots are most likely to appear.
-   **⚡ Hybrid Architecture**: 
    -   **Phase 1 (Tank)**: Browser handles login and session initialization.
    -   **Phase 2 (Sniper)**: Background API polling detects slots with millisecond precision.
    -   **Phase 3 (Execute)**: Browser takes back control for final confirmation and booking.

---

## 🛠️ Quick Start

### Prerequisites
-   Python 3.10 or higher
-   Chrome/Chromium browser installed

### Installation
1.  **Clone the repository**:
    ```bash
    git clone https://github.com/Sohaib-Ahmed869/QVC-bot.git
    cd QVC-bot
    ```

2.  **Create and activate a virtual environment**:
    ```bash
    python -m venv .venv
    # Windows:
    .venv\Scripts\activate
    # Linux/Mac:
    source .venv/bin/activate
    ```

3.  **Install dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

---

## 🌐 Web Control Panel

The easiest way to manage your bot is through the Web Dashboard.

### Running the Server
```bash
python web_server.py
```
Open **`http://localhost:8000`** in your browser.

### Dashboard Features
-   **Applicant Management**: Add, edit, or delete applicants via a clean UI.
-   **Real-time Monitoring**: Watch the bot's logs and progress in real-time.
-   **Visual Scheduler**: Configure "Daily" or "Specific Day" windows for automatic execution.
-   **Proxy Stats**: Monitor your Data Impulse IP rotations and usage stats directly.

---

## ☁️ AWS EC2 Deployment

Deploy this bot to AWS EC2 for 24/7 operation.

### Quick Deployment

1. **Create an EC2 instance** (Ubuntu 22.04 LTS recommended)
2. **SSH into the instance**
3. **Clone this repository**
4. **Run the setup script**:
   ```bash
   bash setup_ec2.sh
   ```
5. **Edit `.env`** with your API keys and database URI
6. **Start the service**:
   ```bash
   sudo systemctl start qvc-bot.service
   ```
7. **Access dashboard** at `http://<your-ec2-ip>:8000`

### Documentation
- **Quick Start**: See [QUICK_START_EC2.md](QUICK_START_EC2.md) for fast reference
- **Full Guide**: See [EC2_DEPLOYMENT.md](EC2_DEPLOYMENT.md) for detailed instructions
- **Checklist**: See [DEPLOYMENT_CHECKLIST.md](DEPLOYMENT_CHECKLIST.md) for verification
- **Summary**: See [DEPLOYMENT_SUMMARY.md](DEPLOYMENT_SUMMARY.md) for overview

### Key Features
- ✅ Auto-start on EC2 reboot (via systemd)
- ✅ Auto-restart on crash (with 10s retry)
- ✅ Real-time logs via `journalctl`
- ✅ Optional Nginx reverse proxy (port 80)
- ✅ Works with MongoDB Atlas or local MongoDB
- ✅ Environment-based configuration (no code changes needed)

### Setup Requirements
No code changes needed! The app works seamlessly on EC2 with proper environment variables configured in `.env`.

---

## ⌨️ CLI Usage

For power users who prefer the terminal.

### Generate Template
Create a sample `applicants.xlsx` file:
```bash
python main.py --create-template
```

### Run the Bot
```bash
# Basic run (using dates from config.py)
python main.py

# Custom date range & headless mode
python main.py --start 2025-02-01 --end 2025-03-31 --headless

# custom Excel file
python main.py --excel my_list.xlsx
```

**Common Arguments:**
-   `-e, --excel`: Path to applicant Excel file.
-   `-s, --start`: Start date (YYYY-MM-DD).
-   `-n, --end`: End date (YYYY-MM-DD).
-   `--headless`: Run browser without showing the window.

---

## ⚙️ Configuration (`config.py`)

Key settings you should be aware of:

-   **`DATE_RANGE`**: Default window for slot searching.
-   **`CAPSOLVER_API_KEY`**: Required for fallback CAPTCHA solving if local OCR fails.
-   **`PROXY_SETTINGS`**: Enter your Data Impulse `USERNAME` and `PASSWORD` here.
-   **`POLL_INTERVAL`**: How often to check for slots (Default: 2.0s).

---

## 📂 Project Structure

-   `config.py`: Central hub for settings and CSS selectors.
-   `web_server.py`: FastAPI backend for the web dashboard.
-   `browser_engine.py`: Core logic for browser automation (`nodriver`).
-   `slot_monitor.py`: The "Sniper" - fast API-based slot detection.
-   `proxy_manager.py`: Rotation logic for residential proxies.
-   `captcha_solver.py`: Logic for solving portal challenges.
-   `data_handler.py`: Handles Excel and JSON applicant storage.
-   `web/`: Frontend assets (HTML, JS, CSS) for the dashboard.

---

## ⚖️ Legal Notice

This tool is for educational purposes only. Automated interaction with the QVC portal may violate their Terms of Service. The authors are not responsible for any account bans or legal issues arising from the use of this software. Use responsibly and at your own risk.
