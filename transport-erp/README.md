# Transport ERP with an automatic AI team

An ERP for a road transport / logistics company, used mainly from an **Android app** on your phone. Ten AI roles
run the company's routine work on their own schedule: management, HR, accounts, marketing, operations, design,
content, social media and the website. Each role can use **Claude** or **ChatGPT**, and designs can be edited in **Canva**.

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
2. **AI (Claude or ChatGPT).** When an API key is set, the AI works as that role. It reads ERP data and uses tools
   such as creating tasks for other roles, drafting posts, designing graphics, editing the website and writing reports.

On the **AI Team** page you can change each role's mission, schedule and **AI engine** (Auto, Claude or ChatGPT),
pause it, or run it now. With both keys set, *Auto* uses Claude, and you can move single roles to ChatGPT.

## Ask AI (chat)
The **Ask AI** tab is a chat with any role, for example the General Manager. Ask "How is business this month?" or
"Which invoices are overdue? Send reminders." The role answers from live ERP data and can act with its tools.
Every chat turn is also recorded in the AI activity log.

## ChatGPT images
With `OPENAI_API_KEY` set, **Designs & Canva → Create image with ChatGPT** makes a picture from your description
(OpenAI image model, `OPENAI_IMAGE_MODEL`). The AI Designer, Content Creator and Social Media Manager can also
create images on their own for posts.

## Canva
1. Go to canva.com/developers and create an integration. Enable these scopes: asset read/write, design content
   read/write, design meta read, and profile read. Add the redirect URL shown in **ERP → Settings → Connections**
   (by default `http://127.0.0.1:8100/api/canva/callback`).
2. Put `CANVA_CLIENT_ID` and `CANVA_CLIENT_SECRET` in `.env`, restart, then click **Connect Canva** in Settings. Do
   this once in the PC's browser. The phone then uses the same connection.
3. On any design, tap **Edit in Canva**. The image opens as a Canva design, which you can edit in the Canva app or on
   the web. When you're done, tap **Bring back**, and the finished PNG is stored in the ERP for posts. **Import from
   Canva** pulls in any design you made directly in Canva.

The ERP connects to Canva with your own Canva account. Canva sets limits on what each plan can do.

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

## Mobile app (Android)
```
 Android phone (Transport ERP app)  ──Wi-Fi / Tailscale / internet──►  ERP server (your PC or a cloud server)
   all screens, chat, approvals                                          database + AI team running 24x7
```
The AI team and the database run on a **server**, either your Windows PC or a cloud host. The app is the screen
and controls for it. If the server is off, the AI team stops.

1. **Get the APK.** On GitHub, go to **Actions → Transport ERP Android APK**, open the latest green run, and download
   **TransportERP-android-apk**. Unzip it and copy `TransportERP-1.0.N.apk` to the phone, then install it (allow
   "install unknown apps" when Android asks). New versions install over the old one.
2. **Start the server for phones.** Put a password in `.env` (`ERP_PASSWORD=`), then double-click
   **`start-mobile.bat`**. It prints addresses such as `192.168.1.20:8100`. When Windows Firewall asks, allow
   **Private networks** only.
3. **Connect.** Open the app, type that address, and log in with the password. The app remembers the address. To
   change it later, go to **More → Change server address**.
4. **Away from the office.** Install **Tailscale** (free) on the PC and the phone and use the `100.x.x.x:8100`
   address. Never port-forward port 8100 on your router.

**No PC? Host it in the cloud.** The included `Dockerfile` runs on Render, Railway, Fly.io or any VPS. Give it a
persistent disk at `/app/data`, set `ERP_PASSWORD`, your keys and `PUBLIC_URL=https://...`, and then type that
https address into the app. iPhone users can open the same address in Safari and choose **Add to Home Screen**.

On the phone, the bottom tabs are **Home, Ask AI, Approvals, Trips, More**. Lists show as cards, with Call buttons
for leads, customers and staff, and one-tap **Start trip / Delivered / Paid / Done** buttons.

## Install the server on Windows
1. Install **Python 3.13** from python.org and tick *Add python.exe to PATH*.
2. Open the `transport-erp` folder and double-click **`setup.bat`**.
3. Open `.env` in Notepad and set `ERP_PASSWORD=`. Optionally add `ANTHROPIC_API_KEY=` (platform.claude.com),
   `OPENAI_API_KEY=` (platform.openai.com) and the Canva keys.
4. Double-click **`start.bat`** (this PC only) or **`start-mobile.bat`** (for phones). The ERP opens at
   <http://127.0.0.1:8100>, and the website is at `/site`.
5. Go to **Settings** and enter your real company details. Then delete the demo records, or start with
   `ERP_DEMO_DATA=0` in `.env`.

Use `backup.bat` to copy the database (`data\erp.db`) into `backups\`.

### Settings (`.env`)
| Key | Meaning |
|---|---|
| `ANTHROPIC_API_KEY` | Turns on Claude |
| `AI_MODEL`, `AI_EFFORT` | Claude model (default `claude-opus-5-5`) and effort `low` / `medium` / `high` |
| `OPENAI_API_KEY` | Turns on ChatGPT (chat, agent roles, images) |
| `OPENAI_MODEL`, `OPENAI_IMAGE_MODEL` | Defaults `gpt-5.6-sol` and `gpt-image-2.5-sunburst`. Change these to any model your OpenAI account offers |
| `AI_PROVIDER` | Engine that roles set to *Auto* use: `auto`, `claude` or `openai` |
| `CANVA_CLIENT_ID`, `CANVA_CLIENT_SECRET`, `CANVA_REDIRECT_URI` | Canva integration |
| `PUBLIC_URL` | Public https address of a cloud-hosted ERP, used for image links in posts |
| `OUTBOX_WEBHOOK_URL` | Zapier/Make/n8n webhook that delivers approved messages and posts |
| `ERP_PASSWORD` | Login password. Recommended if others can reach your PC |
| `APP_PORT`, `APP_HOST` | Default `8100`, `127.0.0.1` (this PC only). `start-mobile.bat` uses `0.0.0.0` and refuses to start without a password |

### Cost
Each AI run or chat message is one conversation with up to 10 tool steps, billed by Anthropic or OpenAI to your
key. ChatGPT images are billed per image. The **AI activity log** shows the tokens used by each run. To spend less,
set longer intervals, pause roles you don't need, or use `AI_EFFORT=low`.

## Developers
```
pip install -r requirements-dev.txt
python -m pytest -q          # tests use fake Claude / OpenAI / Canva clients and spend nothing
python -m app.main
```
Code layout: `app/db.py` (schema), `app/agents.py` (roles, rules, tools), `app/llm.py` (Claude and ChatGPT agent
loops, images), `app/canva.py` (Canva Connect), `app/main.py` (API, scheduler, outbox, website),
`app/static/index.html` (mobile-first UI), `mobile/` (Android app, built by `.github/workflows/transport-erp-apk.yml`).
