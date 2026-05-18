#!/usr/bin/env python3
"""HN Startup Hunter - Find companies hiring on Hacker News"""
import json
import re
import html
import io
import csv
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, render_template, request, jsonify, make_response

app = Flask(__name__)

# Current HN "Who is hiring?" thread - update monthly
# May 2026 thread - we'll auto-detect the latest one
HN_HIRING_THREAD_ID = "47975571"

def fetch_hn_item(item_id):
    url = f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception:
        return None

def get_latest_hiring_thread():
    """Search for the most recent 'Who is Hiring' thread"""
    try:
        url = "https://hn.algolia.com/api/v1/search?query=Ask+HN+Who+is+hiring&tags=story,ask_hn&hitsPerPage=5"
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
            hits = data.get('hits', [])
            for hit in hits:
                if 'who is hiring' in hit.get('title', '').lower():
                    return hit['objectID']
    except Exception:
        pass
    return HN_HIRING_THREAD_ID

def clean_text(html_text):
    """Convert HN HTML to plain text"""
    if not html_text:
        return ""
    text = re.sub(r'<p>', '\n', html_text)
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'<a\s+href=["\']([^"\']*)["\'][^>]*>([^<]*)</a>', r'\2 (\1)', text)
    text = re.sub(r'<[^>]+>', '', text)
    return html.unescape(text).strip()

def extract_company_name(text):
    """Try to extract company name from first line of HN comment"""
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    if not lines:
        return "Unknown"
    first_line = lines[0]
    # Common patterns: "Acme Corp | Remote | Full-time"
    parts = re.split(r'\s*[|/–-]\s*', first_line)
    if parts:
        name = parts[0].strip()
        # Remove common prefixes
        name = re.sub(r'^(hiring:|we are hiring:|job:|position:)\s*', '', name, flags=re.I)
        return name[:80] if name else first_line[:80]
    return first_line[:80]

def extract_emails(text):
    """Extract business emails from text"""
    emails = set(re.findall(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', text))
    # Filter out common personal email domains
    personal_domains = ['gmail.com', 'yahoo.com', 'hotmail.com', 'proton.me',
                        'outlook.com', 'icloud.com', 'me.com', 'live.com']
    return [e for e in emails if not any(d in e.lower() for d in personal_domains)]

def extract_url(text):
    """Extract first URL from comment"""
    urls = re.findall(r'https?://[^\s\)>"]+', text)
    for url in urls:
        if not any(x in url for x in ['ycombinator.com', 'linkedin.com/in', 'twitter.com']):
            return url.rstrip('.,;')
    return urls[0].rstrip('.,;') if urls else ""

def matches_skills(text, skills_list):
    """Check if comment matches any of the user's skills"""
    if not skills_list:
        return True
    text_lower = text.lower()
    return any(skill.lower().strip() in text_lower for skill in skills_list)

def is_company_hiring(text):
    """Return True if this looks like a company hiring post (not a job seeker)"""
    text_lower = text.lower()
    seeking_patterns = [
        "seeking work", "seeking freelance", "seeking contract",
        "looking for work", "available for hire", "hire me",
        "available for work", "open for work", "looking for projects",
        "freelancer available", "i am looking for", "i'm looking for",
        "open to opportunities"
    ]
    if any(p in text_lower for p in seeking_patterns):
        return False
    hiring_signals = [
        "we are hiring", "we're hiring", "we hire", "join our team",
        "apply at", "apply to", "apply here", "careers@", "jobs@",
        "hiring@", "recruiting", "full.time", "full-time", "part.time",
        "remote ok", "remote friendly", "onsite", "salary", "equity",
        "compensation", "interview"
    ]
    return any(s in text_lower for s in hiring_signals)

def scrape_hn_jobs(skills, max_comments=300):
    """Scrape HN hiring thread and filter by skills"""
    thread_id = get_latest_hiring_thread()

    # Fetch thread
    thread = fetch_hn_item(thread_id)
    if not thread:
        return [], thread_id

    all_kids = thread.get('kids', [])[:max_comments]

    # Fetch comments in parallel
    comments = []
    with ThreadPoolExecutor(max_workers=30) as executor:
        futures = {executor.submit(fetch_hn_item, kid): kid for kid in all_kids}
        for f in as_completed(futures):
            result = f.result()
            if result and 'text' in result and result.get('text'):
                comments.append(result)

    # Filter and format
    results = []
    skills_list = [s.strip() for s in skills.split(',') if s.strip()] if skills else []

    for c in comments:
        raw_text = c.get('text', '')
        clean = clean_text(raw_text)

        if not is_company_hiring(clean):
            continue
        if skills_list and not matches_skills(clean, skills_list):
            continue

        emails = extract_emails(clean)
        url = extract_url(clean)
        company = extract_company_name(clean)

        # Get first 3 lines as description
        lines = [l for l in clean.split('\n') if l.strip()]
        description = ' | '.join(lines[:3])[:300]

        results.append({
            'company': company,
            'description': description,
            'emails': emails,
            'url': url,
            'hn_link': f"https://news.ycombinator.com/item?id={c.get('id')}",
            'full_text': clean[:1000]
        })

    return results, thread_id


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/search', methods=['POST'])
def search():
    skills = request.form.get('skills', '').strip()
    try:
        results, thread_id = scrape_hn_jobs(skills, max_comments=200)
        return jsonify({
            'success': True,
            'count': len(results),
            'thread_id': thread_id,
            'results': results
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/export', methods=['POST'])
def export():
    data = request.get_json()
    results = data.get('results', [])

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Company', 'Description', 'Emails', 'Website', 'HN Link'])

    for r in results:
        writer.writerow([
            r.get('company', ''),
            r.get('description', ''),
            ', '.join(r.get('emails', [])),
            r.get('url', ''),
            r.get('hn_link', '')
        ])

    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv'
    response.headers['Content-Disposition'] = 'attachment; filename=hn-leads.csv'
    return response


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
