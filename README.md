# Jobs.

**A qualification matched job search.** Instead of dumping a wall of listings on you the way Indeed or LinkedIn does, this app first tests how qualified you are for the role you actually want, then ranks real live job listings by how well you fit, and tells you which single skill would unlock the most extra matches if you learned it.

Built for the **Playing Around with APIs** summative at African Leadership University. It talks to three external APIs (a jobs board, a resume parser and a large language model), runs on two web servers behind a load balancer, and is covered by an automated test suite.

## Links

| | |
|---|---|
| Live app (through the load balancer) | **[http://13.40.99.74](http://13.40.99.74)** |
| Demo video | *Link added after recording* |
| Repository | **[github.com/africancontrarian/job_search_project_apis](https://github.com/africancontrarian/job_search_project_apis)** |

## The problem it solves

Job boards show you hundreds of listings and leave you to guess which ones you are actually competitive for. Students and early career developers waste hours applying to roles that were never a fit, and never find out what is holding them back. Jobs. answers two questions at once: *which of these real openings am I a good match for right now*, and *what is the one thing I should learn next to qualify for more of them*.

## How it works

1. You type the role you want, pick a country and optionally a city, and either upload your CV (PDF or DOCX) or paste it as text.
2. The app asks Groq to write a five question skill test tailored to that role, and to your real experience if you gave a CV.
3. You answer one question per screen with a progress bar.
4. When you finish, two calls run in parallel: Groq grades your answers (score, level, strengths, gaps, feedback and CV advice), while Adzuna returns up to twenty live listings for that role and country.
5. The backend scores every listing against the key skills Groq found in you. A skill found in the job title counts double, a skill found only in the description counts once. Listings are ranked by that match score.
6. You see your qualification card, the ranked listings, and a small dashboard showing total listings, how many you strongly match (70 percent or higher), average pay of your strong matches, and the gap that would unlock the most extra strong matches if closed.

## Working with the data: search, sort and filter

Above the matched listings there is a toolbar that works instantly in the browser, with no extra API calls.

| Control | What it does |
|---|---|
| **Search these results** | Free text search across title, company, city, category, description and matched skills. Several words narrow the results (all must match). |
| **Sort by** | Best match, highest salary, newest first, or company A to Z. |
| **Minimum match** | Show everything, only 40 percent and up, or only 70 percent and up. |
| **Salary listed** | Hide listings that do not publish a salary. |

The count above the list updates live ("Showing 3 of 18"), and if your filters remove everything you get a clear empty state with a one click **Clear filters** button.

## APIs used

| API | What it does here | Docs |
|---|---|---|
| **Adzuna Jobs API** | Live job listings with title, company, salary, location, description and an apply link. | [developer.adzuna.com/docs](https://developer.adzuna.com/docs/search) |
| **Groq API** (model `openai/gpt-oss-20b`) | Writes the tailored test and grades the answers. Called with JSON response mode so replies are machine readable. | [console.groq.com/docs](https://console.groq.com/docs/api-reference) |
| **APILayer Resume Parser** | Turns an uploaded CV into structured skills, experience and education. | [apilayer.com/marketplace/resume_parser-api](https://apilayer.com/marketplace/resume_parser-api) |

The Python backend also exposes its own small API, which the frontend uses:

```
GET  /api/health          { "status": "ok", "server": "web01" }
POST /api/generate-test   { job_title, cv_text? }                       -> { questions: [...] }
POST /api/parse-cv        raw PDF or DOCX bytes (max 5MB)               -> { text, parsed }
POST /api/jobs            { job_title, country, location? }             -> { jobs: [...], cached }
POST /api/score           { job_title, country, location?, answers, cv_text? }
                          -> { qualification, jobs (ranked), metrics, warning }
```

Every response carries an `X-Served-By` header naming the machine that answered. That header is how the load balancing is verified below.

## Stack

The backend is plain Python 3 using only the standard library: `http.server`, `urllib`, `json`, `threading` and `concurrent.futures`. No Flask, no pip install. I chose this on purpose, because the assignment is about understanding what happens at the HTTP level, and a framework would hide most of that.

The frontend is vanilla HTML, CSS and JavaScript with no build step. A single `state` object in `script.js` drives four views (start, quiz, loading, results), switched by toggling the `hidden` attribute.

## Run it locally

You need Python 3.8 or newer and three free API accounts (none ask for a card).

1. **Get the code**
   ```
   git clone https://github.com/africancontrarian/job_search_project_apis.git
   cd job_search_project_apis
   ```
2. **Get your keys**
   Groq: sign up at [console.groq.com](https://console.groq.com) and create a key under API Keys.
   Adzuna: sign up at [developer.adzuna.com](https://developer.adzuna.com) and copy your `app_id` and `app_key`.
   APILayer: sign up, subscribe to the free plan of the [Resume Parser](https://apilayer.com/marketplace/resume_parser-api), and copy the key from your account page.
3. **Create your `.env`** (this file is in `.gitignore` and never gets committed)
   ```
   cp .env.example .env
   ```
   Then fill it in:
   ```
   GROQ_API_KEY=your_groq_key
   ADZUNA_APP_ID=your_adzuna_id
   ADZUNA_APP_KEY=your_adzuna_key
   APILAYER_API_KEY=your_apilayer_key
   PORT=8000
   SERVER_NAME=
   ```
4. **Start the server**
   ```
   python3 server.py
   ```
5. Open [http://localhost:8000](http://localhost:8000).

If a key is missing the server refuses to start and tells you exactly which one.

### Run the tests

```
python3 -m unittest discover -s tests -v
```

22 tests cover the match scoring, metrics, cache expiry, input validation, path traversal protection, friendly error messages and the full `/api/score` flow, including what happens when Adzuna is down. The external APIs are faked, so the tests need no keys and spend no quota.

## Deployment

### Architecture

```
                 Browser
                    |
                    v
        lb01  (nginx, port 80, round robin)
             /                     \
            v                       v
  web01:8000 (server.py)    web02:8000 (server.py)
            \                       /
             v                     v
        Adzuna, Groq and APILayer APIs
```

A note on `sudo`: the commands below use it, but some server images log you in as root and do not have `sudo` installed. If you see `sudo: command not found`, run the same command without the word `sudo`. `setup_web.sh` detects this on its own, and if systemd is not available either, it starts the app in the background with `nohup` instead.

Both web servers run identical copies of the app as a systemd service. lb01 only runs nginx and forwards traffic. The API keys live in a `.env` file on web01 and web02 only; they never touch the load balancer or the repository.

### Step 1: set up web01 (then repeat exactly on web02)

SSH in:
```
ssh ubuntu@13.40.149.114     # web02: ssh ubuntu@18.175.45.200
```
Clone the repo. This downloads the code into your home folder.
```
git clone https://github.com/africancontrarian/job_search_project_apis.git
```
Move into it:
```
cd job_search_project_apis
```
Create the secrets file from the template, then add your keys. On web01 set `SERVER_NAME=web01`, on web02 set `SERVER_NAME=web02`.
```
cp .env.example .env
```
Install the service. `setup_web.sh` copies `deploy/jobs.service` into `/etc/systemd/system/`, fixes the paths to match this machine, enables it so it starts on boot, starts it, and finishes with a health check.
```
bash deploy/setup_web.sh
```
You should see `HTTP/1.0 200 OK` and `X-Served-By: web01`.

**Why systemd instead of `nohup python3 server.py &`:** with `nohup`, the app dies if it crashes or the server reboots and nobody notices. systemd restarts it within three seconds of any crash and starts it automatically after a reboot. Useful commands:

| Command | What it does |
|---|---|
| `sudo systemctl status jobs` | Is it running, and since when |
| `sudo journalctl -u jobs -f` | Follow the live logs |
| `sudo systemctl restart jobs` | Restart after pulling new code |

Optional but recommended: only allow the load balancer to reach port 8000.
```
sudo ufw allow from 13.40.99.74 to any port 8000
```

### Step 2: configure the load balancer on lb01

SSH in and install nginx:
```
sudo apt update
```
```
sudo apt install -y nginx
```
Copy the config from this repo (`deploy/nginx-lb01.conf`) to `/etc/nginx/sites-available/jobs`, putting your real web01 and web02 IPs in the `upstream` block. Then enable it and remove the default site:
```
sudo ln -s /etc/nginx/sites-available/jobs /etc/nginx/sites-enabled/jobs
```
```
sudo rm /etc/nginx/sites-enabled/default
```
Check the syntax before applying it, then reload:
```
sudo nginx -t
```
```
sudo systemctl reload nginx
```

The important lines in the config, and why each one is there:

```nginx
upstream jobs_backend {
    zone jobs_backend 64k;
    server 13.40.149.114:8000 max_fails=3 fail_timeout=30s;
    server 18.175.45.200:8000 max_fails=3 fail_timeout=30s;
}
server {
    listen 80;
    server_name 13.40.99.74 _;
    client_max_body_size 6m;
    add_header X-Upstream $upstream_addr always;
    location / {
        proxy_pass http://jobs_backend;
        proxy_read_timeout 90s;
        proxy_next_upstream error timeout http_502 http_503;
        ...
    }
}
```

| Setting | Reason |
|---|---|
| `upstream` with two servers | Round robin by default: request 1 to web01, request 2 to web02, and so on. |
| `zone jobs_backend 64k` | Makes all nginx worker processes share one round robin counter, so the split is a true 50/50. |
| `max_fails`, `fail_timeout` | After 3 failures a server is taken out of rotation for 30 seconds. |
| `proxy_next_upstream` | If one server is down, the request is retried on the other, so users never see the failure. |
| `client_max_body_size 6m` | nginx rejects bodies over 1MB by default, which would break most CV uploads. |
| `proxy_read_timeout 90s` | AI grading can take a while; this stops nginx from cutting it off early. |
| `X-Upstream` header | Shows which backend IP answered, alongside the app's own `X-Served-By`. |

For now the app is reached directly on lb01's IP (`13.40.99.74`). Once a domain is ready, point its DNS `A` record at that IP and add the domain to `server_name`. Port 8000 on web01 and web02 must accept connections from lb01; if `curl http://13.40.149.114:8000/api/health` from lb01 hangs, that port is blocked by the firewall. A ready to use HAProxy alternative is in `deploy/haproxy-lb01.cfg` if you prefer it, but only run one of the two.

### Step 3: verify the load balancer

From any machine:
```
bash deploy/test_lb.sh http://13.40.99.74 10
```
The script sends ten requests and counts which server answered each one. Expected output:
```
Request 1 -> web01
Request 2 -> web02
Request 3 -> web01
...
Summary:
      5 web01
      5 web02
```
You can also check a single request by hand:
```
curl -I http://13.40.99.74/api/health
```
and in the app itself, the footer shows **Served by web01** or **Served by web02** for every request.

**Failover test:** stop the app on one server (`sudo systemctl stop jobs` on web02) and run the script again. All requests go to web01 and the site stays up. Start it again with `sudo systemctl start jobs` and the split returns to even.

I ran all three of these checks (even split, failover, and a 4MB upload passing through nginx) against this exact config before submitting.

### Automated testing and deployment (CI/CD)

`.github/workflows/ci.yml` runs the full test suite on every push and pull request. It also has an optional deploy job that SSHes into web01, then web02, pulls the latest code and restarts the service, one server at a time so the site never goes down. It only runs if you set the repository variable `DEPLOY_ENABLED=true` and add the secrets `WEB01_HOST`, `WEB02_HOST`, `SSH_USER` and `SSH_PRIVATE_KEY`.

## Reliability, security and performance

**Error handling.** Every external call is wrapped so one failing API cannot take down the others. If Adzuna is down, you still get your score with a clear note that listings could not load. If Groq is rate limited, times out, or replies with broken JSON, you get a plain message telling you what happened and to try again, never a blank screen or a stack trace. Raw upstream errors are logged on the server but never shown to users.

**Input validation.** Job titles and locations are length capped. The country must come from an allow list of Adzuna's supported countries, because it is placed inside the Adzuna URL path and must never be raw user input. Request bodies are capped at 1MB (5MB for CV uploads), must be JSON objects, and AI replies are checked and reshaped before they reach the browser.

**XSS protection.** All text from APIs is inserted with `textContent` or escaped first. Job links are only rendered if they start with `http://` or `https://`, so a `javascript:` link can never run.

**Static file safety.** Requests like `/../server.py` or `/%2e%2e/.env` return 404. The server resolves every path and refuses anything outside the `frontend` folder.

**Security headers.** Every response sets `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` and a strict referrer policy.

**Caching.** Adzuna results are cached in memory for 10 minutes per role, country and city. Repeat searches return instantly and save the free tier quota. `/api/jobs` reports `"cached": true` when it served from the cache.

**Concurrency.** The server uses `ThreadingHTTPServer`, so one user waiting 10 seconds on an AI call does not block everyone else.

## Challenges and how I solved them

**Cloudflare blocking Python.** The first Groq integration returned 403 with "error code 1010." Groq sits behind Cloudflare, which flags Python's default `urllib` User Agent as a bot. Setting an explicit browser User Agent fixed it. This was hard to spot because the real reason was only in the response body, so I changed the app to log the full upstream error.

**Gemini rate limits.** The first plan used Gemini, but the free tier allowed roughly ten requests a minute and returned random 400s. I switched to Groq, which is faster and far more forgiving on the free tier. Later Groq retired Llama 3.3 from the free tier, so I moved to `openai/gpt-oss-20b`.

**APILayer returning fields as lists.** Fields like `email` came back as a string sometimes and a list other times. A `_safe_str` helper now handles both shapes.

**Committing `.env` by accident.** Early on the `.env` file was pushed before `.gitignore` existed. I rotated every key immediately. Lesson: write `.gitignore` before the first commit.

**Match scoring.** At first almost every job scored low because "PostgreSQL" and "postgres" did not match. A proper tokenizer and double weighting for title matches fixed the ranking.

**The load balancer split looked uneven.** Testing showed 4 requests to web01 and 2 to web02. nginx runs several worker processes and each one kept its own round robin counter. Adding a shared `zone` to the upstream block made the split exactly even.

**CV uploads failed through the load balancer but worked locally.** nginx's default 1MB body limit rejected most PDFs with a 413 before they reached the app. Setting `client_max_body_size 6m` fixed it.

**One slow request froze the whole site.** The original `HTTPServer` handled one request at a time, so while one user waited on Groq everyone else waited too. Switching to `ThreadingHTTPServer` fixed it.

## Credits

Job listings by [Adzuna](https://www.adzuna.com), used under their developer API terms.
Skill assessment by [Groq](https://groq.com) running the `openai/gpt-oss-20b` open weights model.
CV parsing by [APILayer Resume Parser](https://apilayer.com/marketplace/resume_parser-api).
Load balancing by [nginx](https://nginx.org). Font is Inter by Rasmus Andersson, via Google Fonts.

Built by **Oteniya Oluwatobi Jeremiah** ([github.com/africancontrarian](https://github.com/africancontrarian)), Software Engineering student at African Leadership University.

## License

Educational use only, as this is coursework.
