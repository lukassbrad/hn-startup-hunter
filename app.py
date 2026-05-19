#!/usr/bin/env python3
"""HN Startup Hunter - Find companies hiring on Hacker News"""
import json
import re
import html
import io
import csv
import os
import hashlib
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, render_template, request, jsonify, make_response, session

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'hn-hunter-secret-2026')

FREE_LIMIT = 20  # Free tier: first 20 results
LS_WEBHOOK_SECRET = os.environ.get('LS_WEBHOOK_SECRET', '')
PRO_EMAILS_FILE = '/tmp/pro_emails.json'

def get_pro_emails():
    try:
        with open(PRO_EMAILS_FILE) as f:
            return json.load(f)
    except:
        return {}

def save_pro_email(email, order_id):
    emails = get_pro_emails()
    emails[email.lower()] = {'order_id': order_id, 'active': True}
    try:
        with open(PRO_EMAILS_FILE, 'w') as f:
            json.dump(emails, f)
    except:
        pass

def is_pro(request):
    # Check session
    if session.get('pro'):
        return True
    # Check pro_email cookie
    pro_email = request.cookies.get('pro_email', '').lower()
    if pro_email and pro_email in get_pro_emails():
        return True
    return False

# Tech stacks to extract
TECH_TAGS = [
    'Python', 'Go', 'Rust', 'TypeScript', 'JavaScript', 'React', 'Vue', 'Angular',
    'Node.js', 'Django', 'FastAPI', 'Flask', 'Rails', 'Ruby', 'Java', 'Kotlin',
    'Swift', 'iOS', 'Android', 'AWS', 'GCP', 'Azure', 'Kubernetes', 'Docker',
    'PostgreSQL', 'MySQL', 'MongoDB', 'Redis', 'Kafka', 'Spark', 'Terraform',
    'GraphQL', 'ML', 'LLM', 'AI', 'NLP', 'PyTorch', 'TensorFlow', 'Scala',
    'Elixir', 'Phoenix', 'C++', 'C#', '.NET', 'Solidity', 'Blockchain',
    'Postgres', 'Snowflake', 'dbt', 'Airflow', 'Databricks', 'R', 'Julia',
]

def fetch_hn_item(item_id):
    url = f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception:
        return None

def get_hiring_threads(n=3):
    threads = []
    try:
        url = "https://hn.algolia.com/api/v1/search?query=Ask+HN+Who+is+hiring&tags=story,ask_hn&hitsPerPage=10"
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
            for hit in data.get("hits", []):
                title = hit.get("title", "")
                if re.search(r"who is hiring", title, re.I):
                    threads.append({"id": hit["objectID"], "title": title, "date": hit.get("created_at", "")[:7]})
                    if len(threads) >= n:
                        break
    except Exception:
        threads.append({"id": "47975571", "title": "Ask HN: Who is hiring? (May 2026)", "date": "2026-05"})
    return threads if threads else [{"id": "47975571", "title": "Ask HN: Who is hiring? (May 2026)", "date": "2026-05"}]

def clean_text(html_text):
    if not html_text:
        return ""
    text = re.sub(r"<p>", "\n\n", html_text)
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r'<a\s+href=["\']([ ^"\']*)["\'"][^>]*>', "", text)
    text = re.sub(r"</a>", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()

def extract_company_name(text):
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        return "Unknown"
    first_line = lines[0]
    parts = re.split(r"\s*[|/–—-]\s*", first_line)
    if parts:
        name = parts[0].strip()
        name = re.sub(r"^(hiring:|we are hiring:|job:|position:|company:)\s*", "", name, flags=re.I)
        return name[:80] if len(name) > 2 else first_line[:80]
    return first_line[:80]

def extract_emails(text):
    emails = set(re.findall(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", text))
    personal = {"gmail.com", "yahoo.com", "hotmail.com", "proton.me", "outlook.com", "icloud.com"}
    return [e for e in sorted(emails) if e.split("@")[-1].lower() not in personal][:3]

def extract_url(text):
    urls = re.findall(r"https?://[^\s\)\]>\"]+", text)
    for url in urls:
        clean = url.rstrip(".,;)")
        if not any(x in clean for x in ["ycombinator.com", "twitter.com", "x.com"]):
            return clean
    return urls[0].rstrip(".,;)") if urls else ""

def extract_tech_tags(text):
    found = []
    text_lower = text.lower()
    for tag in TECH_TAGS:
        pattern = r"\b" + re.escape(tag.lower()) + r"\b"
        if re.search(pattern, text_lower):
            found.append(tag)
    return found[:8]

def extract_location(text):
    text_lower = text.lower()
    if re.search(r"\bremote\b", text_lower):
        if re.search(r"\bonsite\b|\bon.?site\b|\bin.?person\b", text_lower):
            return "Remote / Onsite"
        return "Remote"
    for loc in ["new york", "nyc", "san francisco", "sf", "london", "berlin",
                "toronto", "amsterdam", "paris", "sydney", "seattle", "boston",
                "chicago", "austin", "los angeles", "la,"]:
        if loc in text_lower:
            return loc.title().replace("Nyc", "NYC").replace("Sf", "SF")
    return ""

def extract_salary(text):
    for p in [r"\$[\d,]+[kK]?\s*[-–]\s*\$[\d,]+[kK]?", r"\$[\d,]+[kK]", r"€[\d,]+[kK]?\s*[-–]\s*€[\d,]+[kK]?"]:
        m = re.search(p, text)
        if m:
            return m.group(0)
    return ""

def matches_skills(text, skills_list):
    if not skills_list:
        return True
    text_lower = text.lower()
    return any(skill.lower().strip() in text_lower for skill in skills_list)

def is_company_hiring(text):
    text_lower = text.lower()
    seeking = ["seeking work", "seeking freelance", "looking for work", "available for hire",
               "hire me", "available for work", "open for work", "i'm a ", "i am a ", "freelancer available"]
    if any(p in text_lower for p in seeking):
        return False
    hiring = ["we are hiring", "we're hiring", "join our team", "apply at", "apply to",
              "careers@", "jobs@", "hiring@", "full-time", "full time", "part-time",
              "remote ok", "remote friendly", "salary", "compensation", "interview",
              "send your", "send us", "we need", "we're looking for", "we are looking"]
    return any(s in text_lower for s in hiring)

def scrape_hn_jobs(skills, thread_id, max_comments=250, remote_only=False):
    thread = fetch_hn_item(thread_id)
    if not thread:
        return [], 0
    all_kids = thread.get("kids", [])[:max_comments]
    comments = []
    with ThreadPoolExecutor(max_workers=40) as executor:
        futures = {executor.submit(fetch_hn_item, kid): kid for kid in all_kids}
        for f in as_completed(futures):
            result = f.result()
            if result and "text" in result and result.get("text"):
                comments.append(result)
    results = []
    skills_list = [s.strip() for s in skills.split(",") if s.strip()] if skills else []
    for c in comments:
        raw_text = c.get("text", "")
        clean = clean_text(raw_text)
        if not is_company_hiring(clean):
            continue
        if skills_list and not matches_skills(clean, skills_list):
            continue
        location = extract_location(clean)
        if remote_only and "remote" not in location.lower():
            continue
        emails = extract_emails(clean)
        url = extract_url(clean)
        company = extract_company_name(clean)
        tech_tags = extract_tech_tags(clean)
        salary = extract_salary(clean)
        lines = [l.strip() for l in clean.split("\n") if l.strip()]
        desc_lines = lines[1:4] if len(lines) > 1 else lines[:3]
        results.append({
            "company": company, "description": " ".join(desc_lines)[:280],
            "emails": emails, "url": url,
            "hn_link": f"https://news.ycombinator.com/item?id={c.get('id')}",
            "tech_tags": tech_tags, "location": location, "salary": salary,
        })
    results.sort(key=lambda x: (len(x["emails"]) > 0, len(x["tech_tags"])), reverse=True)
    return results, len(all_kids)


@app.route("/")
def index():
    threads = get_hiring_threads(3)
    pro = is_pro(request)
    return render_template("index.html", threads=threads, is_pro=pro)

@app.route("/search", methods=["POST"])
def search():
    skills = request.form.get("skills", "").strip()
    thread_id = request.form.get("thread_id", "").strip()
    remote_only = request.form.get("remote_only") == "true"
    if not thread_id:
        threads = get_hiring_threads(1)
        thread_id = threads[0]["id"] if threads else "47975571"
    try:
        results, total_comments = scrape_hn_jobs(skills, thread_id, remote_only=remote_only)
        pro = is_pro(request)
        free_count = len(results)
        locked = False
        if not pro and len(results) > FREE_LIMIT:
            locked = True
            results = results[:FREE_LIMIT]
        return jsonify({
            "success": True, "count": len(results), "total_scanned": total_comments,
            "results": results, "locked": locked, "total_available": free_count,
            "is_pro": pro,
            "upgrade_url": "https://bradauto.lemonsqueezy.com/checkout/buy/96ddcb80-0ed2-48af-a4e5-3fe87df49166"
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/export", methods=["POST"])
def export():
    if not is_pro(request):
        return jsonify({"error": "Pro required for export", "upgrade_url": "https://bradauto.lemonsqueezy.com/checkout/buy/96ddcb80-0ed2-48af-a4e5-3fe87df49166"}), 403
    data = request.get_json()
    results = data.get("results", [])
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Company", "Description", "Tech Stack", "Location", "Salary", "Emails", "Website", "HN Link"])
    for r in results:
        writer.writerow([r.get("company",""), r.get("description",""), ", ".join(r.get("tech_tags",[])),
                        r.get("location",""), r.get("salary",""), ", ".join(r.get("emails",[])),
                        r.get("url",""), r.get("hn_link","")])
    response = make_response(output.getvalue())
    response.headers["Content-Type"] = "text/csv"
    response.headers["Content-Disposition"] = "attachment; filename=hn-startup-leads.csv"
    return response

@app.route("/webhook/lemonsqueezy", methods=["POST"])
def lemonsqueezy_webhook():
    """Receive LemonSqueezy payment confirmation webhooks."""
    try:
        data = request.get_json()
        event = data.get("meta", {}).get("event_name", "")
        if event in ("order_created", "subscription_created", "subscription_payment_success"):
            attrs = data.get("data", {}).get("attributes", {})
            customer = attrs.get("user_email") or attrs.get("customer_email", "")
            order_id = str(data.get("data", {}).get("id", ""))
            if customer:
                save_pro_email(customer, order_id)
                return jsonify({"status": "ok", "email": customer}), 200
        return jsonify({"status": "ignored", "event": event}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route("/activate", methods=["GET", "POST"])
def activate():
    """Activate Pro access with purchase email."""
    if request.method == "POST":
        email = request.form.get("email", "").lower().strip()
        if email in get_pro_emails():
            session["pro"] = True
            session["pro_email"] = email
            resp = make_response(jsonify({"status": "activated", "message": "Pro access activated!"}))
            resp.set_cookie("pro_email", email, max_age=365*24*3600, samesite="Lax")
            return resp
        return jsonify({"status": "not_found", "message": "Email not found. Please check your purchase email or contact support."}), 404
    return render_template("activate.html")


# ============================================================
# B2B API ($49/month) — programmatic access for recruiting tools
# ============================================================

API_KEYS_FILE = '/tmp/api_keys.json'

def get_api_keys():
    try:
        with open(API_KEYS_FILE) as f:
            return json.load(f)
    except:
        return {}

def save_api_key(api_key, email, order_id):
    keys = get_api_keys()
    keys[api_key] = {'email': email, 'order_id': order_id, 'active': True}
    try:
        with open(API_KEYS_FILE, 'w') as f:
            json.dump(keys, f)
    except:
        pass

def is_valid_api_key(api_key):
    if not api_key:
        return False
    keys = get_api_keys()
    return api_key in keys and keys[api_key].get('active', False)

@app.route("/api/v1/search", methods=["GET"])
def api_search():
    """B2B API endpoint — $49/month plan."""
    api_key = request.args.get('api_key', '')
    if not is_valid_api_key(api_key):
        return jsonify({
            "error": "Invalid or missing API key. Subscribe at /api/docs",
            "docs": "https://hn-startup-hunter.onrender.com/api/docs"
        }), 401
    skills = request.args.get('skills', '').strip()
    thread_id = request.args.get('thread_id', '').strip()
    remote_only = request.args.get('remote_only', '').lower() == 'true'
    limit = min(int(request.args.get('limit', 200)), 500)
    if not thread_id:
        threads = get_hiring_threads(1)
        thread_id = threads[0]["id"] if threads else "47975571"
    try:
        results, total_comments = scrape_hn_jobs(skills, thread_id, remote_only=remote_only)
        return jsonify({
            "success": True, "count": min(len(results), limit),
            "total_scanned": total_comments, "results": results[:limit],
            "thread_id": thread_id, "skills_filter": skills,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/v1/threads", methods=["GET"])
def api_threads():
    api_key = request.args.get('api_key', '')
    if not is_valid_api_key(api_key):
        return jsonify({"error": "Invalid API key"}), 401
    threads = get_hiring_threads(6)
    return jsonify({"threads": threads})

@app.route("/api/docs")
def api_docs():
    from flask import Response
    html = """<!DOCTYPE html><html><head><title>HN Startup Hunter API</title>
<style>body{font-family:monospace;max-width:800px;margin:40px auto;padding:20px;background:#1a1a2e;color:#eee}
h1,h2{color:#ff6600}pre{background:#0d0d1a;padding:12px;border-radius:6px;overflow-x:auto}
.cta{background:#ff6600;color:#fff;padding:10px 20px;border-radius:6px;text-decoration:none;display:inline-block;margin:10px 0}
</style></head><body>
<h1>HN Startup Hunter API</h1>
<p>Programmatic access to HN "Who is Hiring?" data. Perfect for recruiting tools and ATS integrations.</p>
<a class="cta" href="https://bradauto.lemonsqueezy.com/checkout/buy/96ddcb80-0ed2-48af-a4e5-3fe87df49166">Get API Access — $49/mo &rarr;</a>
<h2>Search Endpoint</h2>
<pre>GET /api/v1/search?api_key=YOUR_KEY&skills=python,fastapi&remote_only=true&limit=100</pre>
<p><b>Parameters:</b></p>
<ul>
<li><code>api_key</code> — required</li>
<li><code>skills</code> — comma-separated tech skills filter</li>
<li><code>thread_id</code> — specific HN thread ID (see /api/v1/threads)</li>
<li><code>remote_only</code> — true/false</li>
<li><code>limit</code> — max results (default 200, max 500)</li>
</ul>
<h2>Threads Endpoint</h2>
<pre>GET /api/v1/threads?api_key=YOUR_KEY</pre>
<h2>Response Schema</h2>
<pre>{"success": true, "count": 42, "results": [
  {"company": "Acme Corp", "emails": ["jobs@acme.com"],
   "tech_tags": ["Python", "FastAPI"], "location": "Remote",
   "salary": "$120k-$160k", "url": "https://acme.com",
   "hn_link": "https://news.ycombinator.com/item?id=..."}
]}</pre>
<h2>Pricing</h2>
<p>$49/month. API key delivered within 24h of payment. Cancel anytime.</p>
<a class="cta" href="https://bradauto.lemonsqueezy.com/checkout/buy/96ddcb80-0ed2-48af-a4e5-3fe87df49166">Subscribe Now &rarr;</a>
</body></html>"""
    return Response(html, content_type='text/html')

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
