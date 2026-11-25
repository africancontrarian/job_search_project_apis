const state = {
  view: 'start',
  jobTitle: '',
  country: 'gb',
  location: '',
  questions: [],
  currentIndex: 0,
  answers: [],
  results: null,
};
const el = {
  views: {
    start: document.getElementById('start-view'),
    quiz: document.getElementById('questionnaire-view'),
    loading: document.getElementById('loading-view'),
    results: document.getElementById('results-view'),
  },
  startForm: document.getElementById('start-form'),
  startBtn: document.querySelector('#start-form button[type="submit"]'),
  jobTitleInput: document.getElementById('job-title'),
  countryInput: document.getElementById('country'),
  locationInput: document.getElementById('location'),
  cvTextInput: document.getElementById('cv-text'),
  cvFileInput: document.getElementById('cv-file'),
  uploadStatus: document.getElementById('upload-status'),

  qCurrent: document.getElementById('q-current'),
  qTotal: document.getElementById('q-total'),
  progressFill: document.getElementById('progress-fill'),
  questionText: document.getElementById('question-text'),
  optionsEl: document.getElementById('options'),
  restartBtn: document.getElementById('restart-btn'),
  restartBtn2: document.getElementById('restart-btn-2'),

  scoreNumber: document.getElementById('score-number'),
  qualLevel: document.getElementById('qual-level'),
  strengthsList: document.getElementById('strengths-list'),
  gapsList: document.getElementById('gaps-list'),
  qualFeedback: document.getElementById('qual-feedback'),

  cvCard: document.getElementById('cv-card'),
  cvList: document.getElementById('cv-list'),

  jobsCount: document.getElementById('jobs-count'),
  resultsList: document.getElementById('results-list'),
  metricsGrid: document.getElementById('metrics-grid'),

  errorStrip: document.getElementById('error-strip'),
  cardTemplate: document.getElementById('job-card-template'),

  loadingText: document.getElementById('loading-text'),
  servedBy: document.getElementById('served-by'),
  toolbar: document.getElementById('jobs-toolbar'),
  filterText: document.getElementById('filter-text'),
  sortBy: document.getElementById('sort-by'),
  minMatch: document.getElementById('min-match'),
  salaryOnly: document.getElementById('salary-only'),
  jobsEmpty: document.getElementById('jobs-empty'),
  clearFilters: document.getElementById('clear-filters'),
};

// Locks the quiz while an answer is being recorded so a fast double click
// cannot submit two answers for the same question.
let answerLocked = false;

// Shows which backend server answered, using the X-Served-By header.
// Handy for proving the load balancer is switching between web01 and web02.
function noteServer(res) {
  const name = res.headers.get('X-Served-By');
  if (name) {
    el.servedBy.textContent = `Served by ${name}`;
    el.servedBy.hidden = false;
  }
}

async function postJson(url, payload) {
  let res;
  try {
    res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  } catch (networkErr) {
    throw new Error('Could not reach the server. Check your connection and try again.');
  }
  noteServer(res);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.error || `Server error ${res.status}`);
  }
  return data;
}

fetch('/api/health').then(noteServer).catch(() => {});

const logoColors = ['#B84420', '#4A7A3B', '#C79A2A', '#6B4A9C', '#2F7E7A', '#8A3A5C'];

function showView(name) {
  state.view = name;
  Object.entries(el.views).forEach(([key, section]) => {
    section.hidden = (key !== name);
  });
  window.scrollTo({ top: 0, behavior: 'smooth' });
  clearError();
}

function showError(message) {
  el.errorStrip.textContent = message;
  el.errorStrip.hidden = false;
}
function clearError() {
  el.errorStrip.textContent = '';
  el.errorStrip.hidden = true;
}

el.cvFileInput.addEventListener('change', async (e) => {
  const file = e.target.files[0];
  if (!file) return;

  if (file.size > 5 * 1024 * 1024) {
    el.uploadStatus.textContent = 'That file is over 5MB. Paste your CV text instead.';
    el.uploadStatus.className = 'upload-status error';
    e.target.value = '';
    return;
  }

  el.uploadStatus.textContent = `Parsing ${file.name}...`;
  el.uploadStatus.className = 'upload-status';
  el.startBtn.disabled = true;
  el.startBtn.textContent = 'Parsing your CV...';

  try {
    const res = await fetch('/api/parse-cv', {
      method: 'POST',
      headers: { 'Content-Type': 'application/octet-stream' },
      body: file,
    });
    noteServer(res);
    if (!res.ok) {
      const errBody = await res.json().catch(() => ({}));
      throw new Error(errBody.error || `Server error ${res.status}`);
    }
    const data = await res.json();
    el.cvTextInput.value = data.text || '';
    el.uploadStatus.textContent = `Parsed ${file.name}. You can edit below if needed.`;
    el.uploadStatus.className = 'upload-status success';
  } catch (err) {
    el.uploadStatus.textContent = `Could not parse: ${err.message}`;
    el.uploadStatus.className = 'upload-status error';
  } finally {
    el.startBtn.disabled = false;
    el.startBtn.textContent = 'Start the test';
  }
});

el.startForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const jobTitle = el.jobTitleInput.value.trim();
  if (!jobTitle) return;

  state.jobTitle = jobTitle;
  state.country = el.countryInput.value;
  state.location = el.locationInput.value.trim();
  state.cvText = el.cvTextInput.value.trim();
  state.answers = [];
  state.currentIndex = 0;

  el.loadingText.textContent = `Writing a short skill test for "${jobTitle}". This takes a few seconds.`;
  showView('loading');

  try {
    const data = await postJson('/api/generate-test', {
      job_title: jobTitle,
      cv_text: state.cvText,
    });
    state.questions = (data.questions || []).filter(
      q => q && typeof q.text === 'string' && Array.isArray(q.options) && q.options.length >= 2
    );
    if (state.questions.length === 0) {
      throw new Error('No questions returned.');
    }
    renderCurrentQuestion();
    showView('quiz');
  } catch (err) {
    showView('start');
    showError(`Could not generate questions: ${err.message}`);
  }
});

function renderCurrentQuestion() {
  const q = state.questions[state.currentIndex];
  const total = state.questions.length;
  const number = state.currentIndex + 1;

  el.qCurrent.textContent = number;
  el.qTotal.textContent = total;
  el.progressFill.style.width = `${(number / total) * 100}%`;
  el.questionText.textContent = q.text;

  answerLocked = false;
  el.optionsEl.innerHTML = '';
  const letters = ['A', 'B', 'C', 'D', 'E', 'F'];
  q.options.forEach((opt, i) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'option';
    btn.innerHTML = `<span class="option-letter">${letters[i]}</span><span>${escapeHtml(opt)}</span>`;
    btn.addEventListener('click', () => handleOptionPicked(opt, btn));
    el.optionsEl.appendChild(btn);
  });
}

async function handleOptionPicked(chosenOption, btn) {
  if (answerLocked) return;
  answerLocked = true;
  el.optionsEl.querySelectorAll('.option').forEach(b => b.classList.remove('selected'));
  btn.classList.add('selected');

  const q = state.questions[state.currentIndex];
  state.answers.push({
    question: q.text,
    chosen_option: chosenOption,
  });
  setTimeout(async () => {
    if (state.currentIndex + 1 < state.questions.length) {
      state.currentIndex += 1;
      renderCurrentQuestion();
    } else {
      await submitAnswers();
    }
  }, 200);
}

el.restartBtn.addEventListener('click', resetToStart);
el.restartBtn2.addEventListener('click', resetToStart);

function resetToStart() {
  state.questions = [];
  state.answers = [];
  state.currentIndex = 0;
  state.results = null;
  el.jobTitleInput.value = '';
  el.locationInput.value = '';
  el.cvTextInput.value = '';
  showView('start');
}

async function submitAnswers() {
  el.loadingText.textContent = 'Grading your answers and pulling matched jobs from Adzuna. Give it a few seconds.';
  showView('loading');

  try {
    const data = await postJson('/api/score', {
      job_title: state.jobTitle,
      country: state.country,
      location: state.location,
      answers: state.answers,
      cv_text: state.cvText,
    });
    state.results = data;
    resetFilters();
    renderResults();
    showView('results');
    if (data.warning) showError(data.warning);
  } catch (err) {
    showView('start');
    showError(`Could not score answers: ${err.message}`);
  }
}

function renderResults() {
  const q = state.results.qualification || {};
  const jobs = state.results.jobs || [];
  const metrics = state.results.metrics || {};
  el.scoreNumber.textContent = q.overall_score ?? '—';
  el.qualLevel.textContent = q.level || '';
  fillList(el.strengthsList, q.strengths || []);
  fillList(el.gapsList, q.gaps || []);
  el.qualFeedback.textContent = q.feedback || '';
  const cvAdvice = q.cv_advice || [];
  if (cvAdvice.length > 0) {
    fillList(el.cvList, cvAdvice);
    el.cvCard.hidden = false;
  } else {
    el.cvCard.hidden = true;
  }
  el.toolbar.hidden = jobs.length === 0;
  renderJobList();
  renderMetrics(metrics, jobs.length);
}

//  Search, sort and filter on the matched jobs (all client side, no extra API calls)

function salaryOf(job) {
  const lo = Number(job.salary_min) || 0;
  const hi = Number(job.salary_max) || 0;
  if (lo && hi) return (lo + hi) / 2;
  return lo || hi;
}

function searchableText(job) {
  return [
    job.title, job.company, job.location, job.category,
    job.description, ...(job.matched_skills || []),
  ].join(' ').toLowerCase();
}

function getVisibleJobs() {
  const all = (state.results && state.results.jobs) || [];
  const terms = el.filterText.value.toLowerCase().split(/\s+/).filter(Boolean);
  const minMatch = Number(el.minMatch.value) || 0;
  const salaryOnly = el.salaryOnly.checked;

  const filtered = all.filter(job => {
    if ((job.match_score ?? 0) < minMatch) return false;
    if (salaryOnly && !salaryOf(job)) return false;
    if (terms.length) {
      const hay = searchableText(job);
      if (!terms.every(t => hay.includes(t))) return false;
    }
    return true;
  });

  const sorters = {
    match: (a, b) => (b.match_score ?? 0) - (a.match_score ?? 0),
    salary: (a, b) => salaryOf(b) - salaryOf(a),
    date: (a, b) => (Date.parse(b.created) || 0) - (Date.parse(a.created) || 0),
    company: (a, b) => (a.company || '').localeCompare(b.company || ''),
  };
  return filtered.sort(sorters[el.sortBy.value] || sorters.match);
}

function renderJobList() {
  const total = ((state.results && state.results.jobs) || []).length;
  const visible = getVisibleJobs();

  el.resultsList.innerHTML = '';
  visible.forEach(j => el.resultsList.appendChild(buildJobCard(j)));

  if (total === 0) {
    el.jobsCount.textContent = 'No listings found for this role right now.';
    el.jobsEmpty.hidden = true;
  } else if (visible.length === total) {
    el.jobsCount.textContent = `${total} listing${total === 1 ? '' : 's'}`;
    el.jobsEmpty.hidden = true;
  } else {
    el.jobsCount.textContent = `Showing ${visible.length} of ${total}`;
    el.jobsEmpty.hidden = visible.length !== 0;
  }
}

function resetFilters() {
  el.filterText.value = '';
  el.sortBy.value = 'match';
  el.minMatch.value = '0';
  el.salaryOnly.checked = false;
}

let filterTimer = null;
el.filterText.addEventListener('input', () => {
  clearTimeout(filterTimer);
  filterTimer = setTimeout(renderJobList, 150);
});
[el.sortBy, el.minMatch, el.salaryOnly].forEach(ctrl =>
  ctrl.addEventListener('change', renderJobList)
);
el.clearFilters.addEventListener('click', () => {
  resetFilters();
  renderJobList();
});

function fillList(listEl, items) {
  listEl.innerHTML = '';
  items.forEach(item => {
    const li = document.createElement('li');
    li.textContent = item;
    listEl.appendChild(li);
  });
}

function buildJobCard(job) {
  const card = el.cardTemplate.content.cloneNode(true);
  const company = job.company || 'Unknown';

  card.querySelector('.job-title').textContent = job.title || 'Untitled role';
  card.querySelector('.job-company').textContent = company;
  card.querySelector('.job-location').textContent = job.location || 'Location not listed';
  card.querySelector('.job-desc').textContent = job.description || '';
  card.querySelector('.job-date').textContent = job.created ? formatDate(job.created) : '';
  const initial = company.trim().charAt(0).toUpperCase() || '·';
  const colorIdx = Math.abs(hashCode(company)) % logoColors.length;
  const logoEl = card.querySelector('.job-logo');
  logoEl.textContent = initial;
  logoEl.style.background = logoColors[colorIdx];
  const salaryEl = card.querySelector('.job-salary');
  if (job.salary_min || job.salary_max) {
    const min = job.salary_min ? Math.round(job.salary_min).toLocaleString() : null;
    const max = job.salary_max ? Math.round(job.salary_max).toLocaleString() : null;
    salaryEl.textContent = min && max ? `${min} to ${max}` : (min || max);
  }
  const sepEl = card.querySelector('.job-meta-sep');
  sepEl.hidden = !salaryEl.textContent;
  const badge = card.querySelector('.match-badge');
  const score = job.match_score ?? 0;
  if (score >= 70) {
    badge.classList.add('match-strong');
    badge.textContent = `${score}% match`;
  } else if (score >= 40) {
    badge.classList.add('match-medium');
    badge.textContent = `${score}% match`;
  } else {
    badge.classList.add('match-weak');
    badge.textContent = `${score}% match`;
  }
  const skillsEl = card.querySelector('.job-skills');
  (job.matched_skills || []).forEach(skill => {
    const tag = document.createElement('span');
    tag.className = 'skill-tag';
    tag.textContent = skill;
    skillsEl.appendChild(tag);
  });
  const linkEl = card.querySelector('.job-link');
  // Only allow real web links, never javascript: or other schemes.
  if (/^https?:\/\//i.test(job.url || '')) {
    linkEl.href = job.url;
  } else {
    linkEl.removeAttribute('href');
    linkEl.textContent = 'Link unavailable';
  }

  return card;
}

function renderMetrics(metrics, jobsCount) {
  el.metricsGrid.innerHTML = '';

  const cards = [];

  cards.push({
    label: 'Total listings',
    value: metrics.total_jobs ?? jobsCount,
  });

  cards.push({
    label: 'Strong matches',
    value: metrics.strong_matches ?? 0,
    hint: '70% match or higher',
  });

  if (metrics.avg_salary_top_matches) {
    cards.push({
      label: 'Avg. pay (top matches)',
      value: Number(metrics.avg_salary_top_matches).toLocaleString(),
    });
  }

  if (metrics.top_gap_impact && metrics.top_gap_impact.gap) {
    cards.push({
      label: 'Biggest unlock',
      value: metrics.top_gap_impact.gap,
      hint: `Closing this gap would strongly match ${metrics.top_gap_impact.unlocks} more listing${metrics.top_gap_impact.unlocks === 1 ? '' : 's'}.`,
      highlight: true,
    });
  }

  cards.forEach(c => {
    const div = document.createElement('div');
    div.className = 'metric-card' + (c.highlight ? ' metric-card-highlight' : '');
    div.innerHTML = `
      <p class="metric-label">${escapeHtml(c.label)}</p>
      <p class="metric-value">${escapeHtml(String(c.value))}</p>
      ${c.hint ? `<p class="metric-hint">${escapeHtml(c.hint)}</p>` : ''}
    `;
    el.metricsGrid.appendChild(div);
  });
}

function hashCode(str) {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    hash = ((hash << 5) - hash) + str.charCodeAt(i);
    hash |= 0;
  }
  return hash;
}

function formatDate(dateStr) {
  const d = new Date(dateStr);
  if (isNaN(d)) return '';
  const now = new Date();
  const diffDays = Math.floor((now - d) / (1000 * 60 * 60 * 24));
  if (diffDays === 0) return 'Posted today';
  if (diffDays === 1) return 'Posted yesterday';
  if (diffDays < 30) return `Posted ${diffDays} days ago`;
  return `Posted ${d.toLocaleDateString()}`;
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}