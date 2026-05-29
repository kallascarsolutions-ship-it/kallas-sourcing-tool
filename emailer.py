import smtplib
import os
import json
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import date


def build_html_email(results: dict) -> str:
    today = date.today().strftime("%d %B %Y")
    total_deals = sum(len(v) for v in results.values())
    cars_with_deals = {k: v for k, v in results.items() if v}
    cars_clean = [k for k, v in results.items() if not v]

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<style>
  body {{ font-family: -apple-system, 'Helvetica Neue', Arial, sans-serif; background:#f4f4f4; margin:0; padding:0; color:#111; }}
  .wrapper {{ max-width:660px; margin:0 auto; background:#fff; }}
  .header {{ background:#0c0c0c; padding:36px 40px; }}
  .header-brand {{ font-size:10px; letter-spacing:0.28em; text-transform:uppercase; color:#444; margin-bottom:8px; }}
  .header-title {{ font-size:22px; font-weight:700; color:#fff; letter-spacing:-0.01em; }}
  .header-sub {{ font-size:11px; color:#555; margin-top:6px; }}
  .summary {{ background:#f8f8f8; border-bottom:1px solid #e8e8e8; padding:14px 40px; font-size:11px; color:#888; letter-spacing:0.08em; text-transform:uppercase; }}
  .summary strong {{ color:#111; }}
  .section {{ padding:32px 40px; border-bottom:1px solid #f0f0f0; }}
  .section-label {{ font-size:8px; letter-spacing:0.3em; text-transform:uppercase; color:#bbb; font-weight:600; margin-bottom:20px; }}
  .deal {{ border:1px solid #e8e8e8; border-left:3px solid #111; padding:20px 24px; margin-bottom:14px; }}
  .deal-car {{ font-size:9px; letter-spacing:0.2em; text-transform:uppercase; color:#999; margin-bottom:5px; }}
  .deal-title {{ font-size:15px; font-weight:700; color:#111; margin-bottom:12px; }}
  .deal-price {{ font-size:22px; font-weight:700; color:#111; display:inline-block; margin-right:14px; }}
  .deal-badge {{ display:inline-block; background:#111; color:#fff; font-size:10px; font-weight:600; padding:4px 10px; letter-spacing:0.1em; vertical-align:middle; }}
  .deal-specs {{ display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin:14px 0; }}
  .spec-label {{ font-size:7px; letter-spacing:0.2em; text-transform:uppercase; color:#bbb; margin-bottom:2px; }}
  .spec-value {{ font-size:11px; font-weight:600; color:#111; }}
  .deal-meta {{ font-size:9px; color:#aaa; border-top:1px solid #f0f0f0; padding-top:10px; }}
  .no-deals {{ padding:36px 40px; text-align:center; color:#ccc; font-size:11px; letter-spacing:0.15em; text-transform:uppercase; }}
  .clean {{ padding:20px 40px 28px; border-bottom:1px solid #f0f0f0; }}
  .clean-label {{ font-size:8px; letter-spacing:0.3em; text-transform:uppercase; color:#ddd; margin-bottom:10px; }}
  .clean-item {{ font-size:10px; color:#ccc; padding:3px 0; }}
  .footer {{ background:#0c0c0c; padding:24px 40px; }}
  .footer-inner {{ display:flex; justify-content:space-between; }}
  .footer-text {{ font-size:8px; letter-spacing:0.2em; text-transform:uppercase; color:#333; }}
</style>
</head>
<body>
<div class="wrapper">

  <div class="header">
    <div class="header-brand">Kallas Car Solutions</div>
    <div class="header-title">Daily Sourcing Report</div>
    <div class="header-sub">{today}</div>
  </div>

  <div class="summary">
    Watchlist: <strong>{len(results)} cars</strong>
    &nbsp;·&nbsp;
    Deals flagged today: <strong>{total_deals}</strong>
    &nbsp;·&nbsp;
    Sources: AutoScout24 · Classic Driver
  </div>
"""

    if cars_with_deals:
        html += '<div class="section"><div class="section-label">Flagged — Below Market Threshold</div>'
        for car_name, deals in cars_with_deals.items():
            for d in deals:
                html += f"""
  <div class="deal">
    <div class="deal-car">{car_name}</div>
    <div class="deal-title">{d.get('title', car_name)}</div>
    <div>
      <span class="deal-price">€{d['price_eur']:,.0f}</span>
      <span class="deal-badge">{d['discount_pct']}% BELOW MARKET</span>
    </div>
    <div class="deal-specs">
      <div><div class="spec-label">Year</div><div class="spec-value">{d.get('year','N/A')}</div></div>
      <div><div class="spec-label">Mileage</div><div class="spec-value">{d.get('mileage_km','N/A')}</div></div>
      <div><div class="spec-label">Country</div><div class="spec-value">{d.get('country','N/A')}</div></div>
      <div><div class="spec-label">Source</div><div class="spec-value">{d.get('source','N/A')}</div></div>
    </div>
    <div class="deal-meta">Market baseline: €{d['market_baseline_eur']:,.0f} &nbsp;·&nbsp; Seller: {d.get('seller','N/A')}</div>
  </div>"""
        html += "</div>"
    else:
        html += '<div class="no-deals">No deals below threshold today</div>'

    if cars_clean:
        html += '<div class="clean"><div class="clean-label">No deals flagged</div>'
        for name in cars_clean:
            html += f'<div class="clean-item">— {name}</div>'
        html += "</div>"

    html += """
  <table width="100%" style="background:#0c0c0c;padding:24px 40px;border-spacing:0;">
    <tr>
      <td style="font-size:8px;letter-spacing:0.2em;text-transform:uppercase;color:#333;padding:0;">Kallas Car Solutions · Copenhagen</td>
      <td align="right" style="font-size:8px;letter-spacing:0.2em;text-transform:uppercase;color:#333;padding:0;">European sourcing · Global delivery</td>
    </tr>
  </table>

</div>
</body>
</html>"""

    return html


def send_email(results: dict, config: dict) -> None:
    email_to = config["settings"]["email_to"]
    email_from = config["settings"]["email_from"]
    app_password = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "")

    if not app_password:
        raise ValueError("GMAIL_APP_PASSWORD environment variable is not set")

    total_deals = sum(len(v) for v in results.values())
    subject = f"KCS Sourcing — {date.today().strftime('%d %b %Y')} — {total_deals} deal(s) flagged"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = email_to
    msg.attach(MIMEText(build_html_email(results), "html"))

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.ehlo()
        server.starttls()
        server.login(email_from, app_password)
        server.sendmail(email_from, email_to, msg.as_string())

    print(f"Email sent to {email_to} — {total_deals} deal(s) flagged")
