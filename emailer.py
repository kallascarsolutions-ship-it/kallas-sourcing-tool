import smtplib
import json
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import date


def build_html_email(results: dict) -> str:
    """Build a clean HTML email from scan results."""
    today = date.today().strftime("%d %B %Y")
    total_deals = sum(len(v) for v in results.values())

    cars_with_deals = {k: v for k, v in results.items() if v}
    cars_clean = {k: v for k, v in results.items() if not v}

    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
    <meta charset="UTF-8">
    <style>
      body {{
        font-family: -apple-system, 'Helvetica Neue', Arial, sans-serif;
        background: #f4f4f4;
        margin: 0;
        padding: 0;
        color: #111;
      }}
      .wrapper {{
        max-width: 680px;
        margin: 0 auto;
        background: #fff;
      }}
      .header {{
        background: #0c0c0c;
        padding: 36px 40px;
      }}
      .header-brand {{
        font-size: 11px;
        letter-spacing: 0.28em;
        text-transform: uppercase;
        color: #444;
        margin-bottom: 8px;
      }}
      .header-title {{
        font-size: 22px;
        font-weight: 700;
        color: #fff;
        letter-spacing: -0.01em;
      }}
      .header-sub {{
        font-size: 11px;
        color: #555;
        margin-top: 6px;
        letter-spacing: 0.05em;
      }}
      .summary-bar {{
        background: #f8f8f8;
        border-bottom: 1px solid #e8e8e8;
        padding: 16px 40px;
        font-size: 11px;
        color: #888;
        letter-spacing: 0.1em;
        text-transform: uppercase;
      }}
      .summary-bar strong {{
        color: #111;
      }}
      .section {{
        padding: 32px 40px;
        border-bottom: 1px solid #f0f0f0;
      }}
      .section-label {{
        font-size: 8px;
        letter-spacing: 0.3em;
        text-transform: uppercase;
        color: #bbb;
        font-weight: 600;
        margin-bottom: 20px;
      }}
      .deal-card {{
        border: 1px solid #e8e8e8;
        border-left: 3px solid #111;
        padding: 20px 24px;
        margin-bottom: 16px;
        background: #fff;
      }}
      .deal-car-name {{
        font-size: 9px;
        letter-spacing: 0.2em;
        text-transform: uppercase;
        color: #999;
        margin-bottom: 6px;
      }}
      .deal-title {{
        font-size: 15px;
        font-weight: 700;
        color: #111;
        margin-bottom: 12px;
        letter-spacing: -0.01em;
      }}
      .deal-price-row {{
        display: flex;
        gap: 24px;
        margin-bottom: 12px;
        flex-wrap: wrap;
      }}
      .deal-price {{
        font-size: 20px;
        font-weight: 700;
        color: #111;
        letter-spacing: -0.02em;
      }}
      .deal-discount {{
        display: inline-block;
        background: #111;
        color: #fff;
        font-size: 10px;
        font-weight: 600;
        padding: 4px 10px;
        letter-spacing: 0.1em;
        margin-top: 4px;
      }}
      .deal-specs {{
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 10px;
        margin-bottom: 12px;
      }}
      .spec-label {{
        font-size: 7px;
        letter-spacing: 0.2em;
        text-transform: uppercase;
        color: #bbb;
        margin-bottom: 2px;
      }}
      .spec-value {{
        font-size: 11px;
        font-weight: 600;
        color: #111;
      }}
      .deal-baseline {{
        font-size: 9px;
        color: #aaa;
        border-top: 1px solid #f0f0f0;
        padding-top: 10px;
      }}
      .deal-source {{
        font-size: 9px;
        color: #bbb;
        margin-top: 4px;
      }}
      .clean-section {{
        padding: 24px 40px;
        border-bottom: 1px solid #f0f0f0;
      }}
      .clean-label {{
        font-size: 8px;
        letter-spacing: 0.3em;
        text-transform: uppercase;
        color: #ddd;
        margin-bottom: 12px;
      }}
      .clean-car {{
        font-size: 10px;
        color: #ccc;
        padding: 4px 0;
        letter-spacing: 0.02em;
      }}
      .footer {{
        background: #0c0c0c;
        padding: 28px 40px;
        display: flex;
        justify-content: space-between;
      }}
      .footer-brand {{
        font-size: 8px;
        letter-spacing: 0.2em;
        text-transform: uppercase;
        color: #333;
      }}
      .no-deals {{
        padding: 40px;
        text-align: center;
        color: #ccc;
        font-size: 12px;
        letter-spacing: 0.1em;
        text-transform: uppercase;
      }}
    </style>
    </head>
    <body>
    <div class="wrapper">

      <div class="header">
        <div class="header-brand">Kallas Car Solutions</div>
        <div class="header-title">Daily Sourcing Report</div>
        <div class="header-sub">{today}</div>
      </div>

      <div class="summary-bar">
        Watchlist: <strong>{len(results)} cars</strong>
        &nbsp;&nbsp;·&nbsp;&nbsp;
        Deals flagged: <strong>{total_deals}</strong>
        &nbsp;&nbsp;·&nbsp;&nbsp;
        Sources: AutoScout24, Classic Driver
      </div>
    """

    if cars_with_deals:
        html += '<div class="section">'
        html += '<div class="section-label">Flagged Deals — Below Market Threshold</div>'

        for car_name, deals in cars_with_deals.items():
            for deal in deals:
                baseline = deal["market_baseline_eur"]
                price = deal["price_eur"]
                discount = deal["discount_pct"]
                mileage = deal.get("mileage_km", "N/A")
                year = deal.get("year", "N/A")
                country = deal.get("country", "N/A")
                seller = deal.get("seller", "N/A")
                source = deal.get("source", "N/A")
                title = deal.get("title", car_name)

                if isinstance(mileage, int):
                    mileage_display = f"{mileage:,} km"
                else:
                    mileage_display = str(mileage)

                html += f"""
                <div class="deal-card">
                  <div class="deal-car-name">{car_name}</div>
                  <div class="deal-title">{title}</div>
                  <div class="deal-price-row">
                    <div>
                      <div class="deal-price">€{price:,.0f}</div>
                    </div>
                    <div>
                      <div class="deal-discount">{discount}% BELOW MARKET</div>
                    </div>
                  </div>
                  <div class="deal-specs">
                    <div>
                      <div class="spec-label">Year</div>
                      <div class="spec-value">{year}</div>
                    </div>
                    <div>
                      <div class="spec-label">Mileage</div>
                      <div class="spec-value">{mileage_display}</div>
                    </div>
                    <div>
                      <div class="spec-label">Country</div>
                      <div class="spec-value">{country}</div>
                    </div>
                  </div>
                  <div class="deal-baseline">Market baseline: €{baseline:,.0f} &nbsp;·&nbsp; Seller: {seller}</div>
                  <div class="deal-source">Source: {source}</div>
                </div>
                """

        html += "</div>"

    else:
        html += '<div class="no-deals">No deals below threshold today</div>'

    if cars_clean:
        html += '<div class="clean-section">'
        html += '<div class="clean-label">No deals flagged</div>'
        for car_name in cars_clean:
            html += f'<div class="clean-car">— {car_name}</div>'
        html += "</div>"

    html += """
      <table width="100%" style="background:#0c0c0c;padding:28px 40px;">
        <tr>
          <td style="font-size:8px;letter-spacing:0.2em;text-transform:uppercase;color:#333;">
            Kallas Car Solutions · Copenhagen
          </td>
          <td align="right" style="font-size:8px;letter-spacing:0.2em;text-transform:uppercase;color:#333;">
            European sourcing · Global delivery
          </td>
        </tr>
      </table>

    </div>
    </body>
    </html>
    """

    return html


def send_email(results: dict, config: dict) -> None:
    """Send the daily digest email via Gmail SMTP."""
    import os

    email_to = config["settings"]["email_to"]
    email_from = config["settings"]["email_from"]
    app_password = os.environ.get("GMAIL_APP_PASSWORD")

    if not app_password:
        raise ValueError("GMAIL_APP_PASSWORD environment variable not set")

    total_deals = sum(len(v) for v in results.items() if isinstance(v, list))
    subject = f"KCS Sourcing Report — {date.today().strftime('%d %b %Y')} — {sum(len(v) for v in results.values())} deal(s) flagged"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = email_to

    html_body = build_html_email(results)
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.ehlo()
        server.starttls()
        server.login(email_from, app_password)
        server.sendmail(email_from, email_to, msg.as_string())

    print(f"Email sent to {email_to}")
