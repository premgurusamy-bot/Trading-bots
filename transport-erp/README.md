# Transport ERP with an automatic AI team

A local ERP for a road transport / logistics company. Ten AI roles run the company's routine work on their own
schedule: management, HR, accounts, marketing, operations, design, content, social media and the website.

| Area | What you manage |
|---|---|
| Operations | Trips/bookings, fleet, drivers & staff, customers |
| Finance | Invoices (GST), expenses, receivables, overdue tracking |
| Growth | Leads, content & social posts, designs (SVG), public website at `/site` with an enquiry form |
| Control | Dashboard KPIs, alerts, tasks, reports, approvals, AI activity log |

## The AI team

| Role | Runs every | What it does automatically |
|---|---|---|
| AI General Manager | 12 h | KPI snapshot, daily management brief, delegates tasks to the team |
| AI Team Leader (Operations) | 30 min | Assigns a vehicle and driver to every pending trip (checks capacity, availability and license), flags late trips, service due, insurance/permit/fitness expiry |
| AI Worker | 30 min | Completes tasks from the other roles (follow-ups, messages, look-ups) |
| AI HR Manager | 1 day | License expiry alerts, monthly payroll summary, hiring needs, job posts |
| AI Accountant | 2 h | Invoices every delivered trip, marks invoices overdue, drafts payment reminders, checks trip profitability |
| AI Marketing Manager | 12 h | Scores leads, creates follow-ups, plans campaigns, briefs the creative roles |
| AI Content Creator | 1 day | Social posts, blogs, ads, email campaigns |
| AI Designer | 1 day | Branded SVG graphics for posts, banners and flyers |
| AI Social Media Manager | 6 h | Keeps a posting calendar, schedules and publishes approved posts |
| AI Website Designer | 7 days | Builds and keeps the company website up to date |

Every run has two layers:

1. **Rules.** Fixed automation that always runs, even without an API key: invoicing, dispatch, alerts, reminders,
   lead scoring, payroll summary, publishing, and a starter website.
2. **AI (Claude).** When `ANTHROPIC_API_KEY` is set, Claude works as that role. It reads ERP data and uses tools
   such as creating tasks for other roles, drafting posts, designing graphics, editing the website and writing reports.

You can change each role's mission and schedule, pause it, or run it now from the **AI Team** page.

### What still needs you
* **Approvals.** Emails, WhatsApp/SMS messages and social posts wait in **Approvals** by default. To make them
  fully automatic, go to **Settings** and turn on *auto-publish* and *auto-send*.
* **Sending for real.** The ERP does not log in to Facebook, Instagram, Gmail and so on itself. Set
  `OUTBOX_WEBHOOK_URL` to a Zapier, Make or n8n webhook, and that service sends each approved item (channel,
  recipient, subject, body) to the right platform. Without a webhook, approved items stay in the Outbox so you can
  send them yourself.
* **Decisions.** The AI roles recommend money, hiring/firing, legal and safety decisions as tasks for `human`.
  They never make those decisions themselves.
* **Real-world updates.** Drivers or dispatchers mark trips *in transit* and *delivered*. Marking a trip delivered
  frees the vehicle and creates the invoice immediately.

## Install on Windows
1. Install **Python 3.13** from python.org and tick *Add python.exe to PATH*.
2. Open the `transport-erp` folder and double-click **`setup.bat`**.
3. Optional: open `.env` in Notepad and set `ANTHROPIC_API_KEY=` (get a key from platform.claude.com).
4. Double-click **`start.bat`**. The ERP opens at <http://127.0.0.1:8100>, and the website is at `/site`.
5. Go to **Settings** and enter your real company details. Then delete the demo records, or start with
   `ERP_DEMO_DATA=0` in `.env`.

Use `backup.bat` to copy the database (`data\erp.db`) into `backups\`.

### Settings (`.env`)
| Key | Meaning |
|---|---|
| `ANTHROPIC_API_KEY` | Turns on the AI layer. Leave it empty for rules-only mode |
| `AI_MODEL` | Default `claude-opus-5-5` |
| `AI_EFFORT` | `low` / `medium` / `high`. Higher effort reasons more and uses more tokens |
| `OUTBOX_WEBHOOK_URL` | Zapier/Make/n8n webhook that delivers approved messages and posts |
| `ERP_PASSWORD` | Login password. Recommended if others can reach your PC |
| `APP_PORT`, `APP_HOST` | Default `8100`, `127.0.0.1` (this PC only) |

### Cost
Each AI run is one Claude conversation with up to 10 tool steps. The **AI activity log** shows the tokens used by
each run. To spend less, set longer intervals, pause roles you don't need, or use `AI_EFFORT=low`.

## Developers
```
pip install -r requirements-dev.txt
python -m pytest -q          # tests use a fake Claude client and spend no tokens
python -m app.main
```
Code layout: `app/db.py` (schema), `app/agents.py` (roles, rules, tools, agent loop), `app/main.py` (API, scheduler,
outbox, website), `app/static/index.html` (UI).
