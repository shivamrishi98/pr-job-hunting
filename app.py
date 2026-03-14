import json
import os
import threading
from functools import wraps
from flask import Flask, jsonify, request, send_file, abort, Response
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB upload limit

RANKED_FILE = "data/ranked_jobs.json"

# ── Auth (username: localhost, password: 0706) ─────────────────────────────────

AUTH_USERNAME = os.getenv("AUTH_USERNAME", "localhost")
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD", "0706")


def _check_auth(username: str, password: str) -> bool:
    return username == AUTH_USERNAME and password == AUTH_PASSWORD


def _auth_required():
    return Response(
        "Login required",
        401,
        {"WWW-Authenticate": 'Basic realm="PreetiWorld"'},
    )


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not _check_auth(auth.username, auth.password):
            return _auth_required()
        return f(*args, **kwargs)
    return decorated
PROFILE_FILE = "data/profile.json"
RESUME_FILE  = "data/resume.pdf"
JOBS_FILE    = "data/jobs.json"
TOP_N = 5

# ── Pipeline state ────────────────────────────────────────────────────────────

_state = {
    "running": False,
    "done": False,
    "error": None,
    "current_step": "",
    "completed_steps": [],
}
_lock = threading.Lock()


def _set(running=None, done=None, error=None, current_step=None, add_step=None):
    with _lock:
        if running is not None:
            _state["running"] = running
        if done is not None:
            _state["done"] = done
        if error is not None:
            _state["error"] = error
        if current_step is not None:
            _state["current_step"] = current_step
        if add_step is not None:
            _state["completed_steps"].append(add_step)


def _reset_state():
    with _lock:
        _state["running"] = True
        _state["done"] = False
        _state["error"] = None
        _state["current_step"] = "Starting..."
        _state["completed_steps"] = []


# ── Pipeline runner ───────────────────────────────────────────────────────────

def _run_pipeline():
    try:
        from scraper.remoteok import fetch_remoteok_jobs
        from ai.resume_parser import parse_resume, save_profile
        from ai.filter import rank_jobs
        from ai.coverletter import generate_cover_letter
        from ai.pdf import generate_pdf

        _set(current_step="Loading your profile...")
        if not os.path.exists(PROFILE_FILE):
            if os.path.exists(RESUME_FILE):
                _set(current_step="Parsing resume PDF with AI...")
                profile = parse_resume(RESUME_FILE)
                save_profile(profile, PROFILE_FILE)
            else:
                raise FileNotFoundError(
                    "No resume found. Please upload your resume PDF first."
                )
        with open(PROFILE_FILE) as f:
            profile = json.load(f)
        _set(add_step=f"Profile loaded — {profile.get('name', 'Candidate')}")

        _set(current_step="Scraping jobs from RemoteOK...")
        jobs = fetch_remoteok_jobs()
        os.makedirs("data", exist_ok=True)
        with open(JOBS_FILE, "w") as f:
            json.dump(jobs, f, indent=2)
        _set(add_step=f"Found {len(jobs)} matching jobs")

        _set(current_step=f"Ranking jobs with AI (scoring {len(jobs)})...")
        top_jobs = rank_jobs(jobs, profile, top_n=TOP_N)
        _set(add_step=f"Selected top {len(top_jobs)} best matches")

        results = []
        for i, job in enumerate(top_jobs, 1):
            _set(current_step=f"Writing cover letter {i}/{len(top_jobs)}: {job['title']} @ {job['company']}")
            letter = generate_cover_letter(job, profile)
            pdf_path = generate_pdf(letter, job, profile)
            results.append({**job, "pdf_path": pdf_path})
            _set(add_step=f"Cover letter ready: {job['company']}")

        with open(RANKED_FILE, "w") as f:
            json.dump(results, f, indent=2)

        _set(running=False, done=True, current_step="All done!")

    except Exception as e:
        _set(running=False, done=False, error=str(e), current_step="")


# ── API routes ────────────────────────────────────────────────────────────────

@app.route("/api/generate", methods=["POST"])
@require_auth
def api_generate():
    with _lock:
        if _state["running"]:
            return jsonify({"ok": False, "message": "Pipeline already running"}), 409
    _reset_state()
    threading.Thread(target=_run_pipeline, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/status")
@require_auth
def api_status():
    with _lock:
        return jsonify(dict(_state))


@app.route("/api/jobs")
@require_auth
def api_jobs():
    if not os.path.exists(RANKED_FILE):
        return jsonify([])
    with open(RANKED_FILE) as f:
        jobs = json.load(f)
    for job in jobs:
        job.pop("pdf_path", None)
    return jsonify(jobs)


@app.route("/api/upload-resume", methods=["POST"])
@require_auth
def api_upload_resume():
    try:
        if "file" not in request.files:
            return jsonify({"ok": False, "message": "No file received."}), 400

        file = request.files["file"]
        if not file or file.filename == "":
            return jsonify({"ok": False, "message": "No file selected."}), 400

        if not file.filename.lower().endswith(".pdf"):
            return jsonify({"ok": False, "message": "Only PDF files are accepted."}), 400

        os.makedirs("data", exist_ok=True)
        file.save(RESUME_FILE)

        if os.path.exists(PROFILE_FILE):
            os.remove(PROFILE_FILE)

        return jsonify({"ok": True, "message": "Resume uploaded. Profile will be updated on next generate."})
    except Exception as e:
        return jsonify({"ok": False, "message": f"Upload error: {str(e)}"}), 500


@app.route("/api/profile")
@require_auth
def api_profile():
    if not os.path.exists(PROFILE_FILE):
        return jsonify(None)
    with open(PROFILE_FILE) as f:
        p = json.load(f)
    return jsonify({"name": p.get("name", ""), "email": p.get("email", "")})


@app.route("/download/<job_id>")
@require_auth
def download(job_id: str):
    if not os.path.exists(RANKED_FILE):
        abort(404)
    with open(RANKED_FILE) as f:
        jobs = json.load(f)
    job = next((j for j in jobs if str(j.get("id")) == job_id), None)
    if not job:
        abort(404)
    pdf_path = job.get("pdf_path", "")
    if not pdf_path or not os.path.exists(pdf_path):
        abort(404, description="PDF not found. Regenerate to fix.")
    filename = f"cover_letter_{job.get('company','job').replace(' ','_').lower()}.pdf"
    return send_file(pdf_path, as_attachment=True, download_name=filename)


# ── Frontend ──────────────────────────────────────────────────────────────────

@app.route("/")
@require_auth
def index():
    return HTML_PAGE


HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>PreetiWorld</title>
<style>
:root {
  --bg:        #fff0f6;
  --surface:   #ffffff;
  --border:    #fce7f3;
  --text:      #1a0a11;
  --muted:     #9d6e84;
  --pink:      #ec4899;
  --pink-h:    #db2777;
  --pink-lt:   #fdf2f8;
  --pink-ring: #fbcfe8;
  --green:     #16a34a;
  --orange:    #d97706;
  --red:       #dc2626;
  --radius:    14px;
  --shadow:    0 2px 16px rgba(236,72,153,.08);
  --shadow-h:  0 8px 32px rgba(236,72,153,.18);
}

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, sans-serif;
  background: var(--bg);
  color: var(--text);
  min-height: 100vh;
}

/* ── NAV ── */
nav {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 28px;
  height: 60px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  z-index: 200;
  gap: 12px;
}

.nav-brand {
  font-size: 1.1rem;
  font-weight: 800;
  color: var(--pink);
  letter-spacing: -0.3px;
  white-space: nowrap;
}

.nav-profile {
  font-size: 0.8rem;
  color: var(--muted);
  display: flex;
  align-items: center;
  gap: 6px;
}

.nav-profile strong { color: var(--text); }

.nav-actions { display: flex; gap: 8px; align-items: center; }

.btn-nav-ghost {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  background: var(--pink-lt);
  color: var(--pink);
  border: 1px solid var(--pink-ring);
  border-radius: 9px;
  font-size: 0.82rem;
  font-weight: 600;
  cursor: pointer;
  transition: background .15s;
  white-space: nowrap;
}
.btn-nav-ghost:hover { background: var(--pink-ring); }

.btn-nav-pink {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  background: var(--pink);
  color: #fff;
  border: none;
  border-radius: 9px;
  font-size: 0.82rem;
  font-weight: 600;
  cursor: pointer;
  transition: background .15s, opacity .15s;
  white-space: nowrap;
}
.btn-nav-pink:hover { background: var(--pink-h); }
.btn-nav-pink:disabled { opacity: .5; cursor: not-allowed; }

/* ── UPLOAD MODAL ── */
.modal-overlay {
  display: none;
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,.4);
  z-index: 300;
  align-items: center;
  justify-content: center;
}
.modal-overlay.open { display: flex; }

.modal {
  background: var(--surface);
  border-radius: 20px;
  padding: 32px;
  width: min(480px, 92vw);
  box-shadow: 0 24px 60px rgba(0,0,0,.2);
  animation: popIn .2s ease both;
}

@keyframes popIn {
  from { opacity:0; transform: scale(.94) translateY(8px); }
  to   { opacity:1; transform: scale(1)   translateY(0);   }
}

.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.modal-header h3 { font-size: 1.1rem; font-weight: 700; }

.modal-close {
  background: var(--bg);
  border: none;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  font-size: 1.1rem;
  cursor: pointer;
  color: var(--muted);
  display: flex;
  align-items: center;
  justify-content: center;
}
.modal-close:hover { background: var(--border); }

.drop-zone {
  border: 2px dashed var(--pink-ring);
  border-radius: 12px;
  padding: 40px 20px;
  text-align: center;
  cursor: pointer;
  transition: border-color .2s, background .2s;
}
.drop-zone:hover, .drop-zone.drag-over {
  border-color: var(--pink);
  background: var(--pink-lt);
}

.drop-zone input[type="file"] { display: none; }

.drop-icon {
  font-size: 2.4rem;
  margin-bottom: 12px;
}

.drop-zone p {
  font-size: 0.9rem;
  color: var(--muted);
  line-height: 1.5;
}

.drop-zone p strong { color: var(--pink); }

.upload-status {
  margin-top: 16px;
  padding: 12px 16px;
  border-radius: 10px;
  font-size: 0.85rem;
  display: none;
}
.upload-status.success {
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
  color: var(--green);
  display: block;
}
.upload-status.error {
  background: #fef2f2;
  border: 1px solid #fecaca;
  color: var(--red);
  display: block;
}

.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 24px;
}

.btn-modal-cancel {
  padding: 10px 18px;
  background: var(--bg);
  color: var(--muted);
  border: 1px solid var(--border);
  border-radius: 9px;
  font-size: 0.875rem;
  font-weight: 500;
  cursor: pointer;
}
.btn-modal-cancel:hover { background: var(--border); }

.btn-modal-upload {
  padding: 10px 20px;
  background: var(--pink);
  color: #fff;
  border: none;
  border-radius: 9px;
  font-size: 0.875rem;
  font-weight: 600;
  cursor: pointer;
  transition: background .15s, opacity .15s;
}
.btn-modal-upload:hover { background: var(--pink-h); }
.btn-modal-upload:disabled { opacity: .5; cursor: not-allowed; }

/* ── HERO ── */
#hero {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 90px 20px 60px;
  gap: 16px;
}

#hero h1 {
  font-size: clamp(1.8rem, 4vw, 2.8rem);
  font-weight: 800;
  letter-spacing: -1px;
  line-height: 1.15;
}

#hero h1 span { color: var(--pink); }

#hero p {
  max-width: 460px;
  color: var(--muted);
  font-size: 0.98rem;
  line-height: 1.6;
}

.hero-actions {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  justify-content: center;
  margin-top: 8px;
}

.btn-hero-primary {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  padding: 13px 26px;
  background: var(--pink);
  color: #fff;
  border: none;
  border-radius: 12px;
  font-size: 0.95rem;
  font-weight: 700;
  cursor: pointer;
  transition: background .15s, transform .1s;
}
.btn-hero-primary:hover { background: var(--pink-h); }
.btn-hero-primary:active { transform: scale(.97); }
.btn-hero-primary:disabled { opacity: .5; cursor: not-allowed; }

.btn-hero-ghost {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 13px 22px;
  background: var(--surface);
  color: var(--pink);
  border: 1.5px solid var(--pink-ring);
  border-radius: 12px;
  font-size: 0.95rem;
  font-weight: 600;
  cursor: pointer;
  transition: background .15s, border-color .15s;
}
.btn-hero-ghost:hover { background: var(--pink-lt); border-color: var(--pink); }

/* ── PROGRESS ── */
#progress-panel {
  max-width: 540px;
  margin: 60px auto;
  padding: 32px;
  background: var(--surface);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  border: 1px solid var(--border);
  display: none;
}

#progress-panel h2 {
  font-size: 1rem;
  font-weight: 700;
  margin-bottom: 20px;
  color: var(--pink);
}

.step-current {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  background: var(--pink-lt);
  border: 1px solid var(--pink-ring);
  border-radius: 10px;
  font-size: 0.875rem;
  color: var(--pink-h);
  font-weight: 500;
  margin-bottom: 16px;
}

.spinner {
  width: 18px;
  height: 18px;
  border: 2.5px solid var(--pink-ring);
  border-top-color: var(--pink);
  border-radius: 50%;
  animation: spin .7s linear infinite;
  flex-shrink: 0;
}

@keyframes spin { to { transform: rotate(360deg); } }

.steps-done { display: flex; flex-direction: column; gap: 8px; }

.step-done {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 0.82rem;
  color: var(--muted);
  animation: fadeUp .3s ease both;
}

.step-done svg { flex-shrink: 0; color: var(--green); }

@keyframes fadeUp {
  from { opacity: 0; transform: translateY(6px); }
  to   { opacity: 1; transform: translateY(0); }
}

.error-box {
  padding: 14px 16px;
  background: #fef2f2;
  border: 1px solid #fecaca;
  border-radius: 10px;
  color: var(--red);
  font-size: 0.875rem;
  line-height: 1.5;
}

/* ── RESULTS ── */
#results-section {
  max-width: 1120px;
  margin: 0 auto;
  padding: 36px 24px 60px;
  display: none;
}

.results-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  margin-bottom: 24px;
  flex-wrap: wrap;
  gap: 12px;
}

.results-header h2 { font-size: 1.25rem; font-weight: 800; }
.results-header p   { color: var(--muted); font-size: 0.85rem; margin-top: 3px; }

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 18px;
}

.card {
  background: var(--surface);
  border-radius: var(--radius);
  padding: 22px;
  box-shadow: var(--shadow);
  display: flex;
  flex-direction: column;
  gap: 13px;
  border: 1px solid var(--border);
  transition: box-shadow .2s, transform .2s;
  animation: fadeUp .4s ease both;
}

.card:hover { box-shadow: var(--shadow-h); transform: translateY(-2px); }

.card-top {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}

.job-title { font-size: 0.94rem; font-weight: 700; line-height: 1.35; }

.job-company { font-size: 0.8rem; color: var(--muted); margin-top: 3px; }

.score-ring {
  flex-shrink: 0;
  width: 50px;
  height: 50px;
  border-radius: 50%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  font-size: 0.95rem;
  font-weight: 800;
  color: #fff;
  line-height: 1;
}

.score-ring small { font-size: 0.52rem; font-weight: 500; opacity: .85; margin-top: 1px; }

.score-high   { background: #16a34a; }
.score-mid    { background: #d97706; }
.score-low    { background: #dc2626; }

.reason {
  font-size: 0.79rem;
  color: var(--muted);
  line-height: 1.55;
  border-left: 3px solid var(--pink-ring);
  padding-left: 10px;
}

.tags { display: flex; flex-wrap: wrap; gap: 5px; }

.tag {
  background: var(--pink-lt);
  border: 1px solid var(--pink-ring);
  border-radius: 6px;
  padding: 2px 8px;
  font-size: 0.7rem;
  color: var(--pink-h);
}

.card-meta { font-size: 0.74rem; color: #c084a4; }

.card-actions { display: flex; gap: 8px; margin-top: auto; }

.btn {
  flex: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 9px 12px;
  border-radius: 9px;
  font-size: 0.8rem;
  font-weight: 600;
  text-decoration: none;
  border: none;
  cursor: pointer;
  transition: opacity .15s, transform .1s;
}
.btn:active { transform: scale(.96); }
.btn-primary { background: var(--pink); color: #fff; }
.btn-primary:hover { background: var(--pink-h); }
.btn-ghost { background: var(--pink-lt); color: var(--pink-h); border: 1px solid var(--pink-ring); }
.btn-ghost:hover { background: var(--pink-ring); }
</style>
</head>
<body>

<!-- NAV -->
<nav>
  <span class="nav-brand">✦ PreetiWorld</span>
  <span class="nav-profile" id="nav-profile"></span>
  <div class="nav-actions">
    <button class="btn-nav-ghost" onclick="openUploadModal()">
      <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1M16 12l-4-4m0 0l-4 4m4-4v12"/>
      </svg>
      Upload Resume
    </button>
    <button class="btn-nav-pink" id="btn-regen" onclick="startGenerate()">
      <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
      </svg>
      Regenerate
    </button>
  </div>
</nav>

<!-- UPLOAD MODAL -->
<div class="modal-overlay" id="upload-modal" onclick="handleOverlayClick(event)">
  <div class="modal">
    <div class="modal-header">
      <h3>Update Your Resume</h3>
      <button class="modal-close" onclick="closeUploadModal()">✕</button>
    </div>

    <div class="drop-zone" id="drop-zone"
         onclick="document.getElementById('file-input').click()"
         ondragover="onDragOver(event)"
         ondragleave="onDragLeave(event)"
         ondrop="onDrop(event)">
      <input type="file" id="file-input" accept=".pdf" onchange="onFileSelected(event)"/>
      <div class="drop-icon">📄</div>
      <p><strong>Click to browse</strong> or drag & drop your PDF resume here</p>
      <p style="margin-top:6px;font-size:0.78rem;">PDF only · Max 16 MB</p>
    </div>

    <div id="selected-file" style="display:none;margin-top:12px;font-size:0.85rem;color:var(--pink);font-weight:600;"></div>
    <div class="upload-status" id="upload-status"></div>

    <div class="modal-footer">
      <button class="btn-modal-cancel" onclick="closeUploadModal()">Cancel</button>
      <button class="btn-modal-upload" id="btn-upload" onclick="uploadResume()" disabled>
        Upload Resume
      </button>
    </div>
  </div>
</div>

<!-- HERO -->
<div id="hero">
  <h1>Preeti's AI<br/><span>job application</span> assistant</h1>
  <p>Find the best fit software engineering roles in the USA, get them scored against your resume, and generate tailored cover letters in one click.</p>
  <div class="hero-actions">
    <button class="btn-hero-primary" id="btn-hero-gen" onclick="startGenerate()">
      <svg width="17" height="17" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z"/>
      </svg>
      Generate Cover Letters
    </button>
    <button class="btn-hero-ghost" onclick="openUploadModal()">
      <svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1M16 12l-4-4m0 0l-4 4m4-4v12"/>
      </svg>
      Upload Resume
    </button>
  </div>
</div>

<!-- PROGRESS PANEL -->
<div id="progress-panel">
  <h2>Generating your cover letters...</h2>
  <div id="step-current" class="step-current">
    <div class="spinner"></div>
    <span id="step-text">Starting...</span>
  </div>
  <div id="steps-done" class="steps-done"></div>
</div>

<!-- RESULTS -->
<div id="results-section">
  <div class="results-header">
    <div>
      <h2 id="results-title">Your top matches</h2>
      <p id="results-sub"></p>
    </div>
  </div>
  <div id="grid" class="grid"></div>
</div>

<script>
const CHECK_SVG = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/></svg>`;

let pollTimer = null;
let selectedFile = null;

// ── Score ──
function scoreClass(s) {
  return s >= 70 ? 'score-high' : s >= 45 ? 'score-mid' : 'score-low';
}

// ── Generate ──
function startGenerate() {
  setGenerateBtns(true);
  fetch('/api/generate', { method: 'POST' })
    .then(r => r.json())
    .then(d => {
      if (!d.ok) { alert(d.message); setGenerateBtns(false); return; }
      showProgress();
      pollTimer = setInterval(pollStatus, 1200);
    })
    .catch(e => { alert('Failed to start: ' + e); setGenerateBtns(false); });
}

function setGenerateBtns(disabled) {
  document.getElementById('btn-regen').disabled = disabled;
  const hb = document.getElementById('btn-hero-gen');
  if (hb) hb.disabled = disabled;
}

function showProgress() {
  document.getElementById('hero').style.display = 'none';
  document.getElementById('results-section').style.display = 'none';
  document.getElementById('progress-panel').style.display = 'block';
  document.getElementById('steps-done').innerHTML = '';
}

function pollStatus() {
  fetch('/api/status')
    .then(r => r.json())
    .then(s => {
      document.getElementById('step-text').textContent = s.current_step || '';

      const doneEl = document.getElementById('steps-done');
      const incoming = s.completed_steps || [];
      for (let i = doneEl.children.length; i < incoming.length; i++) {
        const div = document.createElement('div');
        div.className = 'step-done';
        div.innerHTML = CHECK_SVG + `<span>${incoming[i]}</span>`;
        doneEl.appendChild(div);
      }

      if (s.error) {
        clearInterval(pollTimer);
        document.getElementById('step-current').innerHTML =
          `<div class="error-box">⚠ ${s.error}</div>`;
        setGenerateBtns(false);
      }

      if (s.done) {
        clearInterval(pollTimer);
        setTimeout(loadResults, 400);
      }
    });
}

// ── Results ──
function loadResults() {
  fetch('/api/jobs')
    .then(r => r.json())
    .then(jobs => {
      document.getElementById('progress-panel').style.display = 'none';
      document.getElementById('hero').style.display = 'none';

      const section = document.getElementById('results-section');
      section.style.display = 'block';
      document.getElementById('results-title').textContent =
        `Your top ${jobs.length} match${jobs.length !== 1 ? 'es' : ''}`;
      document.getElementById('results-sub').textContent =
        'Ranked by AI fit score · Download a cover letter or view the posting';

      const grid = document.getElementById('grid');
      grid.innerHTML = '';
      jobs.forEach((job, i) => {
        const score = job.fit_score || 0;
        const tags = (job.tags || []).slice(0, 5)
          .map(t => `<span class="tag">${t}</span>`).join('');
        const date = job.date ? job.date.slice(0, 10) : '';

        const card = document.createElement('div');
        card.className = 'card';
        card.style.animationDelay = `${i * 70}ms`;
        card.innerHTML = `
          <div class="card-top">
            <div>
              <div class="job-title">${job.title}</div>
              <div class="job-company">${job.company} &middot; ${job.location}</div>
            </div>
            <div class="score-ring ${scoreClass(score)}">
              ${score}<small>/100</small>
            </div>
          </div>
          ${job.fit_reason ? `<div class="reason">${job.fit_reason}</div>` : ''}
          ${tags ? `<div class="tags">${tags}</div>` : ''}
          <div class="card-meta">Posted ${date}</div>
          <div class="card-actions">
            <a class="btn btn-primary" href="/download/${job.id}">
              <svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/>
              </svg>
              Download PDF
            </a>
            <a class="btn btn-ghost" href="${job.url}" target="_blank" rel="noopener">
              View Job
              <svg width="11" height="11" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/>
              </svg>
            </a>
          </div>`;
        grid.appendChild(card);
      });

      setGenerateBtns(false);
    });
}

// ── Upload Modal ──
function openUploadModal() {
  selectedFile = null;
  document.getElementById('file-input').value = '';
  document.getElementById('selected-file').style.display = 'none';
  document.getElementById('btn-upload').disabled = true;
  const st = document.getElementById('upload-status');
  st.className = 'upload-status';
  st.textContent = '';
  document.getElementById('upload-modal').classList.add('open');
}

function closeUploadModal() {
  document.getElementById('upload-modal').classList.remove('open');
}

function handleOverlayClick(e) {
  if (e.target === document.getElementById('upload-modal')) closeUploadModal();
}

function onDragOver(e) {
  e.preventDefault();
  document.getElementById('drop-zone').classList.add('drag-over');
}

function onDragLeave() {
  document.getElementById('drop-zone').classList.remove('drag-over');
}

function onDrop(e) {
  e.preventDefault();
  document.getElementById('drop-zone').classList.remove('drag-over');
  const f = e.dataTransfer.files[0];
  if (f) setFile(f);
}

function onFileSelected(e) {
  const f = e.target.files[0];
  if (f) setFile(f);
}

function setFile(f) {
  if (!f.name.toLowerCase().endsWith('.pdf')) {
    showUploadStatus('error', 'Only PDF files are accepted.');
    return;
  }
  selectedFile = f;
  const el = document.getElementById('selected-file');
  el.textContent = '📄 ' + f.name;
  el.style.display = 'block';
  document.getElementById('btn-upload').disabled = false;
  const st = document.getElementById('upload-status');
  st.className = 'upload-status';
  st.textContent = '';
}

function showUploadStatus(type, msg) {
  const el = document.getElementById('upload-status');
  el.className = 'upload-status ' + type;
  el.textContent = msg;
}

function uploadResume() {
  if (!selectedFile) return;
  const btn = document.getElementById('btn-upload');
  btn.disabled = true;
  btn.textContent = 'Uploading...';

  const fd = new FormData();
  fd.append('file', selectedFile);

  fetch('/api/upload-resume', { method: 'POST', body: fd })
    .then(r => {
      if (!r.ok && r.headers.get('content-type') && !r.headers.get('content-type').includes('application/json')) {
        throw new Error('Server error: ' + r.status);
      }
      return r.json();
    })
    .then(d => {
      if (d.ok) {
        showUploadStatus('success', '✓ ' + d.message);
        btn.textContent = 'Uploaded!';
        loadNavProfile();
        setTimeout(closeUploadModal, 1800);
      } else {
        showUploadStatus('error', d.message);
        btn.disabled = false;
        btn.textContent = 'Upload Resume';
      }
    })
    .catch(() => {
      showUploadStatus('error', 'Upload failed. Please try again.');
      btn.disabled = false;
      btn.textContent = 'Upload Resume';
    });
}

// ── Nav profile chip ──
function loadNavProfile() {
  fetch('/api/profile')
    .then(r => r.json())
    .then(p => {
      const el = document.getElementById('nav-profile');
      if (p && p.name) {
        el.innerHTML = `<strong>${p.name}</strong>`;
      } else {
        el.innerHTML = '';
      }
    });
}

// ── Init ──
window.addEventListener('DOMContentLoaded', () => {
  loadNavProfile();
  fetch('/api/jobs')
    .then(r => r.json())
    .then(jobs => { if (jobs.length) loadResults(); });
});
</script>
</body>
</html>"""


if __name__ == "__main__":
    app.run(debug=True, port=8080)
