# HN Startup Hunter 🚀

**Find startups actively hiring on Hacker News — filter by skills, location, and company type.**

🔗 **Live app:** [hn-startup-hunter.onrender.com](https://hn-startup-hunter.onrender.com)

---

## What it does

Every month, thousands of startups post hiring threads on Hacker News ("Who is Hiring?"). HN Startup Hunter scrapes these threads and lets you:

- 🔍 **Search** by technology stack (Python, React, Go, Rust, ML, etc.)
- 🌍 **Filter** by location (Remote, EU, US, specific cities)
- 📋 **View** company descriptions with direct contact info
- 📥 **Export to CSV** with emails and tech stacks (Pro tier)

## Use cases

- **Developers** looking for their next role at a startup
- **Recruiters** sourcing candidates from active hiring companies  
- **Founders** researching competitors hiring in their space

## Free vs Pro

| Feature | Free | Pro (€29 lifetime) |
|---|---|---|
| Search results | First 10 | Unlimited |
| CSV export | ❌ | ✅ with emails |
| API access | ❌ | ✅ |

→ [Get Pro Lifetime Access](https://lukassbrad.gumroad.com/l/esiayp) — one-time €29

## Tech stack

- **Backend:** Python / Flask
- **Data source:** Hacker News API (Algolia)
- **Hosting:** Render (free tier)
- **Frontend:** Vanilla JS + HTML

## Run locally

```bash
git clone https://github.com/lukassbrad/hn-startup-hunter
cd hn-startup-hunter
pip install -r requirements.txt
python app.py
```

Open [http://localhost:5000](http://localhost:5000)

## API

```bash
GET /api/v1/search?q=python&location=remote
GET /api/v1/threads
```

Returns JSON with startup listings. Pro API key required for full results.

---

Built by Brad. Questions? Email lukass.brad@gmail.com
