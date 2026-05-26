# Production-Grade Error Handling in Flask: From Development to Monitoring

*A practical guide for turning your Flask side project into a production-ready service*

---

When I launched HN Startup Hunter — a Flask app that scrapes Hacker News "Who is Hiring" threads for startup leads — I learned quickly that the gap between "it works on my machine" and "it works for users" is filled with unhandled exceptions, silent failures, and missing visibility. This article covers the error handling patterns I use in production Flask applications, from structured responses to real-time monitoring.

## Why Flask's Default Error Handling Falls Short

Flask's default error handling is minimal by design. An unhandled exception returns a generic 500 HTML page. In a JSON API, that means clients receive unexpected HTML. Errors silently disappear into your server logs — if you even have logs.

Three things you need for production:

1. **Structured error responses** — JSON errors with consistent shape, not HTML
2. **Contextual logging** — errors logged with request context, user info, and stack traces
3. **Alerting** — knowing when errors happen before users do

## Pattern 1: Structured JSON Error Responses

Start with a base error class and register Flask error handlers:

```python
from flask import Flask, jsonify, request
import logging

app = Flask(__name__)

class AppError(Exception):
    """Base class for application errors."""
    status_code = 500
    error_code = "INTERNAL_ERROR"
    
    def __init__(self, message, status_code=None, error_code=None):
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        if error_code is not None:
            self.error_code = error_code
    
    def to_dict(self):
        return {
            "error": self.error_code,
            "message": self.message,
            "request_id": request.headers.get("X-Request-ID", "unknown")
        }

class NotFoundError(AppError):
    status_code = 404
    error_code = "NOT_FOUND"

class ValidationError(AppError):
    status_code = 400
    error_code = "VALIDATION_ERROR"

@app.errorhandler(AppError)
def handle_app_error(error):
    response = jsonify(error.to_dict())
    response.status_code = error.status_code
    return response

@app.errorhandler(404)
def handle_404(error):
    return jsonify({
        "error": "NOT_FOUND",
        "message": "The requested resource does not exist."
    }), 404

@app.errorhandler(500)
def handle_500(error):
    return jsonify({
        "error": "INTERNAL_ERROR",
        "message": "An unexpected error occurred. Our team has been notified."
    }), 500
```

Now your API consistently returns JSON. Raise custom errors anywhere in your application:

```python
@app.route("/api/startups/<company_id>")
def get_startup(company_id):
    startup = db.session.get(Startup, company_id)
    if startup is None:
        raise NotFoundError(f"Startup {company_id} not found")
    return jsonify(startup.to_dict())
```

## Pattern 2: Contextual Logging with Request Context

The default `logging` module loses request context. You need to know *which request* caused the error, *who was affected*, and *what the request looked like*.

```python
import logging
import uuid
from functools import wraps

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s [%(request_id)s] %(message)s'
)
logger = logging.getLogger(__name__)

# Request ID middleware
@app.before_request
def assign_request_id():
    request.request_id = request.headers.get(
        "X-Request-ID", 
        str(uuid.uuid4())[:8]
    )

class RequestContextFilter(logging.Filter):
    """Inject request context into every log record."""
    def filter(self, record):
        try:
            record.request_id = request.request_id
            record.method = request.method
            record.path = request.path
            record.remote_addr = request.remote_addr
        except RuntimeError:
            # Outside request context (startup, etc.)
            record.request_id = "SYSTEM"
            record.method = "-"
            record.path = "-"
            record.remote_addr = "-"
        return True

for handler in logging.root.handlers:
    handler.addFilter(RequestContextFilter())
```

Update your error handler to log with context:

```python
@app.errorhandler(500)
def handle_500(error):
    logger.error(
        "Unhandled exception",
        exc_info=True,
        extra={
            "url": request.url,
            "method": request.method,
            "data": request.get_json(silent=True),
        }
    )
    return jsonify({
        "error": "INTERNAL_ERROR",
        "message": "An unexpected error occurred.",
        "request_id": request.request_id
    }), 500
```

Now every error log includes the request ID, which you can correlate with client-side error reports.

## Pattern 3: Graceful Degradation for External Services

Flask applications usually depend on external services — databases, third-party APIs, scraping targets. These fail unpredictably. The right pattern is circuit breaking with exponential backoff:

```python
import time
import functools
from requests.exceptions import Timeout, ConnectionError

class CircuitBreaker:
    """Simple circuit breaker for external service calls."""
    
    def __init__(self, failure_threshold=5, recovery_timeout=60):
        self.failure_count = 0
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.last_failure_time = None
        self.state = "closed"  # closed = normal, open = failing
    
    def call(self, func, *args, **kwargs):
        if self.state == "open":
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = "half-open"
            else:
                raise AppError(
                    "Service temporarily unavailable", 
                    status_code=503,
                    error_code="SERVICE_UNAVAILABLE"
                )
        
        try:
            result = func(*args, **kwargs)
            if self.state == "half-open":
                self.state = "closed"
                self.failure_count = 0
            return result
        except (Timeout, ConnectionError) as e:
            self.failure_count += 1
            self.last_failure_time = time.time()
            if self.failure_count >= self.failure_threshold:
                self.state = "open"
                logger.error(f"Circuit breaker opened: {e}")
            raise AppError(
                "External service unavailable",
                status_code=503,
                error_code="SERVICE_UNAVAILABLE"
            )

# Usage
hn_circuit = CircuitBreaker(failure_threshold=3, recovery_timeout=30)

def fetch_hn_jobs(query):
    return hn_circuit.call(_fetch_hn_jobs_internal, query)
```

## Pattern 4: Health Check Endpoints

Load balancers and uptime monitors need something to probe. A health check endpoint that reports dependency status:

```python
import sqlite3
from datetime import datetime

@app.route("/health")
def health_check():
    """Health check for load balancers and monitoring."""
    status = {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "version": app.config.get("VERSION", "unknown"),
        "dependencies": {}
    }
    
    # Check database
    try:
        db.session.execute("SELECT 1")
        status["dependencies"]["database"] = "ok"
    except Exception as e:
        status["dependencies"]["database"] = f"error: {str(e)}"
        status["status"] = "degraded"
    
    # Check external API
    try:
        import requests
        r = requests.get("https://api.example.com/ping", timeout=2)
        r.raise_for_status()
        status["dependencies"]["external_api"] = "ok"
    except Exception:
        status["dependencies"]["external_api"] = "unreachable"
        # Don't mark fully unhealthy for optional dependencies
    
    http_status = 200 if status["status"] == "healthy" else 503
    return jsonify(status), http_status
```

A `200` on `/health` means "accept traffic." A `503` means "take this instance out of rotation."

## Pattern 5: Integrating Honeybadger for Real-Time Alerts

Logs are reactive — you see errors after users do. An error monitoring service gives you instant alerts and aggregated error tracking.

```bash
pip install honeybadger
```

```python
from honeybadger import honeybadger
from honeybadger.contrib.flask import FlaskHoneybadger

app.config["HONEYBADGER_API_KEY"] = "your_api_key_here"
app.config["HONEYBADGER_ENVIRONMENT"] = "production"

FlaskHoneybadger(app)

# Honeybadger auto-instruments your app:
# - Catches and reports unhandled exceptions
# - Captures request context (URL, method, params, headers)
# - Groups similar errors automatically
# - Alerts you immediately via email/Slack
```

For errors you catch and handle gracefully, you can still report them:

```python
from honeybadger import honeybadger

@app.route("/api/search")
def search():
    try:
        results = expensive_search(request.args.get("q"))
        return jsonify(results)
    except RateLimitError as e:
        # This is expected, but we want to know if it spikes
        honeybadger.notify(e, context={
            "query": request.args.get("q"),
            "user_id": request.headers.get("X-User-ID")
        })
        return jsonify({"error": "RATE_LIMITED", "message": "Try again in 60s"}), 429
```

Custom context lets you see patterns in your Honeybadger dashboard — which queries trigger rate limits, which users see the most errors.

## Putting It Together: A Production Error Handling Stack

Here's the full initialization order for a production Flask app:

```python
from flask import Flask
from honeybadger.contrib.flask import FlaskHoneybadger
import logging
import os

def create_app(config=None):
    app = Flask(__name__)
    
    # 1. Configure logging first
    setup_logging(app)
    
    # 2. Register error handlers
    register_error_handlers(app)
    
    # 3. Add request middleware
    register_middleware(app)
    
    # 4. Initialize monitoring (Honeybadger last, so it catches errors in setup)
    if os.environ.get("HONEYBADGER_API_KEY"):
        FlaskHoneybadger(app)
    
    return app

def setup_logging(app):
    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    handler.addFilter(RequestContextFilter())
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)

def register_error_handlers(app):
    app.register_error_handler(AppError, handle_app_error)
    app.register_error_handler(404, handle_404)
    app.register_error_handler(500, handle_500)

def register_middleware(app):
    app.before_request(assign_request_id)
```

## What to Monitor in Your Honeybadger Dashboard

Once integrated, configure alerts for:

1. **Error rate threshold** — alert if errors/minute exceeds your baseline by 3x
2. **New error types** — get alerted immediately when a new exception type appears
3. **Specific high-value paths** — `/api/payment`, `/api/checkout` warrant their own alert rules
4. **Silent failures** — if your circuit breaker opens more than twice per hour, something upstream is degraded

## Conclusion

Production error handling isn't about preventing all errors — it's about knowing when they happen, understanding their context, and having users see helpful messages instead of blank 500 pages.

The stack described here (structured errors + contextual logging + Honeybadger integration) takes about 2 hours to wire into an existing Flask application. After that, errors become data: you can see which endpoints are fragile, which external dependencies are unreliable, and which error types spike after deploys.

For my HN Startup Hunter app, this setup caught a silent caching bug within hours of deploy — a bug that was causing 15% of search requests to return stale data with no error surfaced to users. Without monitoring, I would have learned about it from angry tweets.

---

*The complete code for this article is available at [github.com/lukassbrad/hn-startup-hunter](https://github.com/lukassbrad/hn-startup-hunter). Brad is a Python automation engineer who builds data tools and automation scripts.*
