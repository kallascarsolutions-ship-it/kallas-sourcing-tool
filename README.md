# KCS Sourcing Tool

Scans AutoScout24 and Classic Driver daily for undervalued collector cars. Sends a formatted email digest to kallascarsolutions@gmail.com every morning.

---

## Setup (one time only)

### 1. Create a new GitHub repo
Go to github.com → New repository → name it `kallas-sourcing-tool` → Public → Create.

### 2. Upload these files
Drag all files from this folder into the GitHub repo. Make sure the folder structure is preserved:
```
.github/workflows/daily_scan.yml
watchlist.json
scraper.py
emailer.py
main.py
requirements.txt
```

### 3. Add your Gmail app password as a secret
- Go to your GitHub repo → Settings → Secrets and variables → Actions → New repository secret
- Name: `GMAIL_APP_PASSWORD`
- Value: your Gmail app password for kallascarsolutions@gmail.com
- Click Add secret

### 4. Enable GitHub Actions
- Go to the Actions tab in your repo
- Click "I understand my workflows, go ahead and enable them"

The tool will now run every day at 09:00 Copenhagen time and send an email.

---

## Updating the watchlist

To add or remove cars, edit `watchlist.json` directly on GitHub:
- Click the file → pencil icon to edit
- Add a new block under `"cars"` following the same format
- Update `market_baseline_eur` whenever market prices shift
- Change `alert_threshold_pct` to make alerts more or less sensitive (15 = flag anything 15% below market)
- Commit the change — takes effect on the next daily run

---

## Manual trigger

To run the scan immediately without waiting for the daily schedule:
- Go to Actions tab in your GitHub repo
- Click "KCS Daily Sourcing Scan" in the left sidebar
- Click "Run workflow" → Run workflow

---

## Email format

The daily email shows:
- All flagged deals below the threshold, with price, discount %, year, mileage, country, and seller
- Cars with no deals flagged listed at the bottom
- Sent from and to kallascarsolutions@gmail.com
