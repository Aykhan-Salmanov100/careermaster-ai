/* ============================================================
   CareerMaster AI — client-side application
   Works fully in the browser; transparently uses the optional
   FastAPI backend when CONFIG.BACKEND_URL is set and reachable.
   ============================================================ */
(() => {
  "use strict";

  const CFG = window.CONFIG || { BACKEND_URL: "", ENDPOINTS: {} };
  const $ = (id) => document.getElementById(id);

  // Shared application state, persisted so all three tools share the CV.
  const state = {
    cv: JSON.parse(localStorage.getItem("cm_cv") || "null"),
    backendOnline: false,
  };

  const api = (key) => (CFG.BACKEND_URL || "") + (CFG.ENDPOINTS[key] || "");

  // ---------------------------------------------------------------
  // Boot: reveal-on-scroll, nav, hero CTA, backend health probe
  // ---------------------------------------------------------------
  function boot() {
    const io = new IntersectionObserver(
      (entries) => entries.forEach((e) => e.isIntersecting && e.target.classList.add("in")),
      { threshold: 0.12 }
    );
    document.querySelectorAll(".reveal").forEach((el) => io.observe(el));

    const navToggle = $("navToggle"), nav = $("nav");
    if (navToggle) navToggle.addEventListener("click", () => nav.classList.toggle("open"));
    document.querySelectorAll(".nav-links a").forEach((a) =>
      a.addEventListener("click", () => nav.classList.remove("open"))
    );

    const cta = $("ctaStart");
    if (cta) cta.addEventListener("click", () => $("upload").scrollIntoView({ behavior: "smooth" }));

    probeBackend();
    CVAnalysis.init();
    MockInterview.init();
    Live.init();
    if (state.cv) { CVAnalysis.render(state.cv); MockInterview.onCvReady(); Live.onCvReady(); }
  }

  async function probeBackend() {
    const badge = $("aiBadge"), label = $("aiBadgeLabel");
    if (!CFG.BACKEND_URL) { if (badge) badge.textContent = "Browser"; if (label) label.textContent = "AI mode"; return; }
    try {
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), 3500);
      const r = await fetch(api("HEALTH"), { signal: ctrl.signal });
      clearTimeout(t);
      state.backendOnline = r.ok;
    } catch { state.backendOnline = false; }
    if (badge) badge.textContent = state.backendOnline ? "Online" : "Browser";
    if (label) label.textContent = state.backendOnline ? "AI backend" : "AI mode";
  }

  // ---------------------------------------------------------------
  // Skills dictionary + small NLP helpers (shared)
  // ---------------------------------------------------------------
  const SKILLS = [
    "python","java","javascript","typescript","c++","c#","go","rust","ruby","php","swift","kotlin","scala","r","sql",
    "react","angular","vue","svelte","node","express","django","flask","fastapi","spring","rails",".net","laravel",
    "html","css","tailwind","bootstrap","sass",
    "postgresql","mysql","mongodb","redis","sqlite","elasticsearch","dynamodb",
    "aws","azure","gcp","docker","kubernetes","terraform","ansible","jenkins","github actions","ci/cd",
    "git","linux","bash","graphql","rest","grpc","websocket","kafka","rabbitmq",
    "pandas","numpy","tensorflow","pytorch","scikit-learn","spacy","nlp","machine learning","deep learning",
    "agile","scrum","jira","figma","matlab","simulink","pytest","junit","jest","cypress",
  ];

  function extractSkills(text) {
    const lower = text.toLowerCase();
    const found = new Set();
    SKILLS.forEach((s) => {
      const re = new RegExp("(^|[^a-z0-9+#.])" + s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "([^a-z0-9+#.]|$)", "i");
      if (re.test(lower)) found.add(s);
    });
    return [...found];
  }

  function extractContacts(text) {
    const email = (text.match(/[\w.+-]+@[\w-]+\.[\w.-]+/) || [])[0] || null;
    const phone = (text.match(/(\+?\d[\d\s().-]{7,}\d)/) || [])[0] || null;
    const linkedin = /linkedin\.com\/[^\s)]+/i.test(text) ? (text.match(/linkedin\.com\/[^\s)]+/i) || [])[0] : null;
    const github = /github\.com\/[^\s)]+/i.test(text) ? (text.match(/github\.com\/[^\s)]+/i) || [])[0] : null;
    return { email, phone, linkedin, github };
  }

  const ACTION_VERBS = ["built","designed","implemented","led","created","developed","optimized","optimised","migrated","automated","refactored","analyzed","analysed","coordinated","debugged","launched","delivered","improved","reduced","increased","architected","deployed","maintained","tested","integrated","scaled","mentored"];

  // ---------------------------------------------------------------
  // 1) CV ANALYSIS
  // ---------------------------------------------------------------
  const CVAnalysis = {
    init() {
      const dz = $("dropzone"), input = $("cvFile");
      ["dragover", "dragenter"].forEach((ev) =>
        dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("dragover"); })
      );
      ["dragleave", "drop"].forEach((ev) =>
        dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("dragover"); })
      );
      dz.addEventListener("drop", (e) => { if (e.dataTransfer.files[0]) this.handle(e.dataTransfer.files[0]); });
      input.addEventListener("change", (e) => { if (e.target.files[0]) this.handle(e.target.files[0]); });

      $("downloadJson").addEventListener("click", () => this.download());
      $("clearCv").addEventListener("click", () => this.clear());

      if (window.pdfjsLib) {
        pdfjsLib.GlobalWorkerOptions.workerSrc = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";
      }
    },

    async handle(file) {
      $("cvFilename").textContent = file.name;
      $("parseStatus").textContent = "Reading file…";
      let text = "";
      try {
        if (/\.pdf$/i.test(file.name)) text = await this.readPdf(file);
        else if (/\.docx?$/i.test(file.name)) text = await this.readDocx(file);
        else { $("parseStatus").textContent = "Unsupported file type. Use PDF or DOCX."; return; }
      } catch (err) {
        $("parseStatus").textContent = "Could not read that file: " + err.message;
        return;
      }
      if (!text.trim()) { $("parseStatus").textContent = "No text found (is it a scanned image?)."; return; }

      const cv = this.analyze(text, file.name);
      state.cv = cv;
      localStorage.setItem("cm_cv", JSON.stringify(cv));
      $("parseStatus").textContent = `Parsed ${cv.wordCount} words · ${cv.skills.length} skills found.`;
      this.render(cv);
      MockInterview.onCvReady();
      Live.onCvReady();
    },

    async readPdf(file) {
      const buf = await file.arrayBuffer();
      const pdf = await pdfjsLib.getDocument({ data: buf }).promise;
      let text = "";
      for (let i = 1; i <= pdf.numPages; i++) {
        const page = await pdf.getPage(i);
        const content = await page.getTextContent();
        text += content.items.map((it) => it.str).join(" ") + "\n";
      }
      return text;
    },

    async readDocx(file) {
      const buf = await file.arrayBuffer();
      const res = await window.mammoth.extractRawText({ arrayBuffer: buf });
      return res.value;
    },

    analyze(text, filename) {
      const skills = extractSkills(text);
      const contacts = extractContacts(text);
      const wordCount = text.trim().split(/\s+/).length;
      const numbers = (text.match(/\b\d+(\.\d+)?%?\b/g) || []).length;
      const verbs = ACTION_VERBS.filter((v) => new RegExp("\\b" + v + "\\b", "i").test(text));
      const yearsMatch = text.match(/(\d+)\+?\s*years?/i);
      const years = yearsMatch ? parseInt(yearsMatch[1], 10) : null;

      // ATS-readiness heuristic (0–100)
      let score = 0; const notes = [];
      if (contacts.email) score += 15; else notes.push(["warn", "Add a professional email address."]);
      if (contacts.linkedin || contacts.github) score += 10; else notes.push(["warn", "Add a LinkedIn or GitHub link."]);
      if (skills.length >= 6) score += 25; else { score += skills.length * 4; notes.push(["warn", "List more concrete, relevant skills/technologies."]); }
      if (numbers >= 4) score += 25; else { score += numbers * 6; notes.push(["warn", "Quantify achievements with numbers (%, time saved, scale)."]); }
      if (verbs.length >= 4) score += 15; else notes.push(["warn", "Open bullet points with strong action verbs."]);
      if (wordCount >= 250 && wordCount <= 900) score += 10;
      else if (wordCount < 250) notes.push(["warn", "CV looks short — add more detail on impact and scope."]);
      else notes.push(["warn", "CV looks long — tighten to the most relevant points."]);
      score = Math.max(0, Math.min(100, Math.round(score)));
      if (notes.length === 0) notes.push(["ok", "Strong, well-rounded CV. Nice work."]);

      return { filename, text, skills, contacts, wordCount, numbers, verbs, years, score, notes };
    },

    render(cv) {
      $("cvResult").style.display = "";
      $("clearCv").disabled = false; $("downloadJson").disabled = false;
      const ring = $("scoreRing"); ring.style.setProperty("--val", cv.score); $("scoreVal").textContent = cv.score;
      $("scoreSummary").textContent =
        cv.score >= 75 ? "Interview-ready" : cv.score >= 50 ? "Solid — a few gaps" : "Needs work before applying";

      $("cvDetails").innerHTML = [
        ["Email", cv.contacts.email || "—"],
        ["Links", [cv.contacts.linkedin, cv.contacts.github].filter(Boolean).join(" · ") || "—"],
        ["Experience", cv.years ? cv.years + "+ years" : "—"],
        ["Word count", cv.wordCount],
        ["Quantified points", cv.numbers],
      ].map(([k, v]) => `<dt>${k}</dt><dd>${escapeHtml(String(v))}</dd>`).join("");

      $("cvSkills").innerHTML = cv.skills.length
        ? cv.skills.map((s) => `<span class="chip on">${escapeHtml(s)}</span>`).join("")
        : '<span class="muted">No known skills detected.</span>';

      $("cvNotes").innerHTML = cv.notes.map(([kind, msg]) => `<li class="${kind === "warn" ? "warn" : ""}">${escapeHtml(msg)}</li>`).join("");

      const json = { filename: cv.filename, skills: cv.skills, contacts: cv.contacts, experience_years: cv.years, ats_score: cv.score, word_count: cv.wordCount };
      $("cvJson").textContent = JSON.stringify(json, null, 2);

      // Feed the live-assistant CV context card
      const ctx = $("liveCvContext");
      if (ctx) ctx.innerHTML = cv.skills.length
        ? `<b style="color:var(--text);">Top skills:</b> ${cv.skills.slice(0, 10).map(escapeHtml).join(", ")}`
        : '<span class="muted">No skills detected from your CV.</span>';
    },

    download() {
      if (!state.cv) return;
      const json = { filename: state.cv.filename, skills: state.cv.skills, contacts: state.cv.contacts, experience_years: state.cv.years, ats_score: state.cv.score };
      const blob = new Blob([JSON.stringify(json, null, 2)], { type: "application/json" });
      triggerDownload(blob, "cv-analysis.json");
    },

    clear() {
      state.cv = null; localStorage.removeItem("cm_cv");
      $("cvResult").style.display = "none"; $("cvFilename").textContent = "";
      $("cvFile").value = ""; $("parseStatus").textContent = "No CV loaded yet.";
      $("clearCv").disabled = true; $("downloadJson").disabled = true;
      $("liveCvContext").innerHTML = '<span class="muted">Upload a CV to ground the suggestions in your own experience.</span>';
      MockInterview.onCvReady(); Live.onCvReady();
    },
  };

  // ---------------------------------------------------------------
  // 2) MOCK INTERVIEW
  // ---------------------------------------------------------------
  const QUESTION_BANK = {
    behavioural: [
      "Tell me about a time you {verb} a challenging {skill} problem. What was the impact?",
      "Describe a situation where a project using {skill} went off track. How did you respond?",
      "Give an example of when you had to learn {skill} quickly under pressure.",
      "Tell me about a time you disagreed with a teammate on a technical decision.",
      "Describe your proudest achievement involving {skill}.",
    ],
    technical: [
      "How would you design a system that relies heavily on {skill}? Walk me through the trade-offs.",
      "What are common pitfalls when working with {skill}, and how do you avoid them?",
      "How do you test and debug code that uses {skill}?",
      "Explain how you would optimise performance in a {skill}-based component.",
    ],
    role: [
      "Why are you a strong fit for a {role} position?",
      "What does a great {role} do differently from an average one?",
      "Where do you want to grow as a {role} over the next two years?",
    ],
  };

  const MockInterview = {
    questions: [], idx: 0, answers: {},
    init() {
      $("generateBtn").addEventListener("click", () => this.generate());
      $("prevBtn").addEventListener("click", () => this.go(-1));
      $("nextBtn").addEventListener("click", () => this.go(1));
      $("submitBtn").addEventListener("click", () => this.score());
    },
    onCvReady() {
      $("genStatus").textContent = state.cv
        ? `Ready — questions will use ${state.cv.skills.length} skills from your CV.`
        : "Upload a CV first for tailored questions (or generate role-based ones now).";
    },

    async generate() {
      const role = $("roleSelect").value;
      const difficulty = $("difficultySelect").value;
      $("genStatus").textContent = "Generating…";

      if (state.backendOnline) {
        try {
          const r = await fetch(api("GENERATE"), {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ skills: state.cv ? state.cv.skills : [], role, difficulty, count: 10 }),
          });
          if (r.ok) {
            const data = await r.json();
            const qs = (data.questions || data || []).map((q) => (typeof q === "string" ? q : q.text || q.question));
            if (qs.length) { this.questions = qs.map((t, i) => ({ text: t, tag: "AI · " + role })); return this.start("AI-generated"); }
          }
        } catch { /* fall back to client-side */ }
      }
      this.questions = this.buildLocal(role, difficulty);
      this.start("Generated in-browser");
    },

    buildLocal(role, difficulty) {
      const skills = (state.cv && state.cv.skills.length ? state.cv.skills : ["software engineering", "problem solving", "teamwork"]);
      const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];
      const fill = (tpl) => tpl
        .replace("{skill}", pick(skills))
        .replace("{role}", role)
        .replace("{verb}", pick(ACTION_VERBS));
      const out = [];
      const counts = difficulty === "senior" ? [4, 5, 3] : difficulty === "junior" ? [5, 2, 2] : [4, 4, 2];
      const seen = new Set();
      const add = (tpl, tag) => { let q; let guard = 0; do { q = fill(tpl); guard++; } while (seen.has(q) && guard < 6); seen.add(q); out.push({ text: q, tag }); };
      for (let i = 0; i < counts[0]; i++) add(pick(QUESTION_BANK.behavioural), "Behavioural");
      for (let i = 0; i < counts[1]; i++) add(pick(QUESTION_BANK.technical), "Technical · " + difficulty);
      for (let i = 0; i < counts[2]; i++) add(pick(QUESTION_BANK.role), role);
      return out;
    },

    start(sourceLabel) {
      this.idx = 0; this.answers = {};
      $("interviewPanel").style.display = "";
      $("genStatus").textContent = `${this.questions.length} questions · ${sourceLabel}.`;
      this.show();
    },

    show() {
      const q = this.questions[this.idx];
      $("qProgress").textContent = `Question ${this.idx + 1} / ${this.questions.length}`;
      $("qText").textContent = "“" + q.text + "”";
      $("qTag").textContent = q.tag || "";
      $("answerInput").value = this.answers[this.idx] || "";
      $("prevBtn").disabled = this.idx === 0;
      $("nextBtn").disabled = this.idx === this.questions.length - 1;
      $("starFeedback").style.display = "none";
    },

    go(delta) {
      this.answers[this.idx] = $("answerInput").value;
      const next = this.idx + delta;
      if (next < 0 || next >= this.questions.length) return;
      this.idx = next; this.show();
    },

    async score() {
      const answer = $("answerInput").value.trim();
      this.answers[this.idx] = answer;
      if (!answer) { $("genStatus").textContent = "Type an answer first."; return; }

      if (state.backendOnline) {
        try {
          const r = await fetch(api("EVALUATE"), {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ question_id: this.idx, question_text: this.questions[this.idx].text, response_text: answer }),
          });
          if (r.ok) { const d = await r.json(); return this.renderFeedback(this.mapBackend(d, answer)); }
        } catch { /* fall back */ }
      }
      this.renderFeedback(starHeuristic(answer));
    },

    mapBackend(d, answer) {
      // Backend returns rich data; fall back to heuristic flags if absent.
      const h = starHeuristic(answer);
      return {
        text: d.feedback || d.summary || h.text,
        S: d.situation ?? h.S, T: d.task ?? h.T, A: d.action ?? h.A, R: d.result ?? h.R,
      };
    },

    renderFeedback(fb) {
      $("starFeedback").style.display = "";
      $("starFeedbackText").textContent = fb.text;
      const cell = (label, ok) =>
        `<span class="star-pill"><span class="ic ${ok ? "ok" : "no"}">${ok ? "✓" : "!"}</span>${label}</span>`;
      $("starRow").innerHTML = cell("Situation", fb.S) + cell("Task", fb.T) + cell("Action", fb.A) + cell("Result", fb.R);
    },
  };

  // Client-side STAR scorer for a typed answer.
  function starHeuristic(answer) {
    const t = answer.toLowerCase();
    const wc = answer.split(/\s+/).filter(Boolean).length;
    const S = /\b(when|while|during|at the time|in my role|at \w+|project|team|company|situation|context)\b/.test(t);
    const T = /\b(needed to|had to|responsible|my job|goal|objective|tasked|challenge|required|asked to)\b/.test(t);
    const A = ACTION_VERBS.filter((v) => new RegExp("\\b" + v + "\\b").test(t)).length >= 2;
    const R = /\b(\d+%?|reduced|increased|improved|saved|grew|cut|boosted|resulted|outcome|impact|delivered)\b/.test(t);
    const present = [S, T, A, R].filter(Boolean).length;
    let text;
    if (wc < 25) text = "Your answer is quite short — aim for 4–6 sentences that walk through the full story.";
    else if (present === 4) text = "Well structured — all four STAR elements are present. Keep quantifying the result.";
    else {
      const missing = [!S && "Situation", !T && "Task", !A && "Action", !R && "Result"].filter(Boolean);
      text = `Good start. Strengthen the ${missing.join(" & ")} ${missing.length > 1 ? "elements" : "element"}` +
             (!R ? " — add concrete numbers to your result." : ".");
    }
    return { text, S, T, A, R };
  }

  // ---------------------------------------------------------------
  // 3) LIVE INTERVIEW ASSISTANT (Web Speech API)
  // ---------------------------------------------------------------
  const Live = {
    rec: null, listening: false, supported: false,
    full: "", buffer: "", questions: 0, started: 0, meterCtx: null, micStream: null,

    init() {
      const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
      this.supported = !!SR;
      $("startBtn").addEventListener("click", () => this.start());
      $("stopBtn").addEventListener("click", () => this.stop());
      $("micTestBtn").addEventListener("click", () => this.testMic());
      $("downloadSummaryBtn").addEventListener("click", () => this.downloadSummary());
      $("audioLang").addEventListener("change", (e) => localStorage.setItem("cm_lang", e.target.value));
      const saved = localStorage.getItem("cm_lang"); if (saved) $("audioLang").value = saved;
      if (!this.supported) {
        this.setStatus("Unsupported", "err");
        $("startBtn").disabled = true;
        $("transcript").innerHTML = '<span class="placeholder">Your browser does not support speech recognition. Try Chrome or Edge.</span>';
      }
    },
    onCvReady() {},

    setStatus(text, kind) {
      const b = $("liveStatus"); b.textContent = text;
      b.className = "status-badge" + (kind ? " " + kind : "");
    },

    async start() {
      if (!this.supported) return;
      const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
      this.rec = new SR();
      this.rec.continuous = true; this.rec.interimResults = true;
      this.rec.lang = $("audioLang").value || "en-US";
      this.rec.maxAlternatives = 1;
      this.rec.onstart = () => this.setStatus("Listening", "live");
      this.rec.onerror = (e) => {
        if (e.error === "not-allowed") { this.setStatus("Mic denied", "err"); }
        else if (e.error !== "no-speech") { this.setStatus("Error: " + e.error, "err"); }
      };
      this.rec.onend = () => { if (this.listening) { try { this.rec.start(); } catch {} } };
      this.rec.onresult = (ev) => this.onResult(ev);

      try { this.rec.start(); } catch {}
      this.listening = true; this.full = ""; this.buffer = ""; this.questions = 0; this.started = Date.now();
      $("startBtn").disabled = true; $("stopBtn").disabled = false;
      $("transcript").innerHTML = '<span class="placeholder">Listening… speak or play the interviewer\'s question.</span>';
      $("liveSuggestion").style.display = "none"; $("sessionSummary").style.display = "none";
      try { this.micStream = await navigator.mediaDevices.getUserMedia({ audio: true }); this.startMeter(this.micStream); } catch {}
    },

    stop() {
      this.listening = false;
      if (this.rec) { try { this.rec.stop(); } catch {} }
      if (this.micStream) { this.micStream.getTracks().forEach((t) => t.stop()); this.micStream = null; }
      if (this.meterCtx) { this.meterCtx.close().catch(() => {}); this.meterCtx = null; }
      $("levelMeter").style.width = "0%";
      $("startBtn").disabled = false; $("stopBtn").disabled = true;
      this.setStatus("Stopped");
      this.showSummary();
    },

    onResult(ev) {
      let interim = "", final = "";
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const tr = ev.results[i][0].transcript;
        if (ev.results[i].isFinal) final += tr; else interim += tr;
      }
      if (final) { this.full += " " + final; this.buffer += " " + final; }
      const el = $("transcript");
      el.innerHTML = escapeHtml(this.full.trim()) + (interim ? ' <span class="interim">' + escapeHtml(interim) + "</span>" : "");
      el.scrollTop = el.scrollHeight;

      const combined = (final + " " + interim).toLowerCase();
      if (final && this.isQuestion(this.buffer)) { this.questions++; this.suggest(this.buffer.trim()); this.buffer = ""; }
      else if (this.isQuestion(combined)) { /* show hint card emphasis only */ }
    },

    isQuestion(text) {
      const clean = text.trim().toLowerCase();
      const wc = clean.split(/\s+/).filter(Boolean).length;
      if (wc < 4) return false;
      const triggers = /\b(tell me|describe|explain|how|why|what|when|where|walk me|give me|can you|could you|share|a time|challenge|example|your experience|why should)\b/;
      return triggers.test(clean) || clean.endsWith("?");
    },

    async suggest(question) {
      const box = $("liveSuggestion"); box.style.display = "";
      if (state.backendOnline) {
        try {
          const r = await fetch(api("SUGGEST"), {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ transcript: question, cv_data: state.cv || {}, role: $("roleSelect") ? $("roleSelect").value : "" }),
          });
          if (r.ok) { const d = await r.json(); box.innerHTML = `<b>Suggested angle</b><br>${escapeHtml(d.suggestion || d.text || "")}`; return; }
        } catch { /* fall back */ }
      }
      const skill = state.cv && state.cv.skills.length ? state.cv.skills[Math.floor(Math.random() * Math.min(5, state.cv.skills.length))] : "a recent project";
      box.innerHTML = `<b>Answer with STAR</b><br>Set the <b>situation</b>, state the <b>task</b>, detail your <b>actions</b>, and quantify the <b>result</b>. Anchor it on <b>${escapeHtml(skill)}</b> and end with a measurable outcome.`;
    },

    startMeter(stream) {
      try {
        const AC = window.AudioContext || window.webkitAudioContext;
        const ctx = new AC(); this.meterCtx = ctx;
        const src = ctx.createMediaStreamSource(stream);
        const an = ctx.createAnalyser(); an.fftSize = 512; src.connect(an);
        const data = new Uint8Array(an.frequencyBinCount); const meter = $("levelMeter");
        const tick = () => {
          if (!this.meterCtx) return;
          an.getByteTimeDomainData(data);
          let sum = 0; for (let i = 0; i < data.length; i++) { const v = (data[i] - 128) / 128; sum += v * v; }
          meter.style.width = Math.min(100, Math.round(Math.sqrt(sum / data.length) * 400)) + "%";
          requestAnimationFrame(tick);
        };
        tick();
      } catch {}
    },

    async testMic() {
      const result = $("micTestBtn");
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        result.textContent = "Microphone OK ✓";
        this.startMeter(stream);
        setTimeout(() => { stream.getTracks().forEach((t) => t.stop()); if (this.meterCtx && !this.listening) { this.meterCtx.close().catch(()=>{}); this.meterCtx = null; $("levelMeter").style.width = "0%"; } result.textContent = "Test microphone"; }, 3000);
      } catch (e) { result.textContent = "Mic blocked — check permissions"; }
    },

    showSummary() {
      const secs = Math.round((Date.now() - this.started) / 1000);
      const words = this.full.trim().split(/\s+/).filter(Boolean).length;
      const starCues = ["situation","task","action","result"].filter((k) => this.full.toLowerCase().includes(k)).length;
      $("sumDuration").textContent = secs >= 60 ? Math.floor(secs / 60) + "m " + (secs % 60) + "s" : secs + "s";
      $("sumQuestions").textContent = this.questions;
      $("sumWords").textContent = words;
      $("sumStar").textContent = starCues + "/4";
      $("sessionSummary").style.display = words > 0 ? "" : "none";
    },

    downloadSummary() {
      const blob = new Blob([
        "CareerMaster AI — Live session summary\n\n" +
        "Questions detected: " + this.questions + "\n" +
        "Words transcribed: " + this.full.trim().split(/\s+/).filter(Boolean).length + "\n\n" +
        "Transcript:\n" + this.full.trim(),
      ], { type: "text/plain" });
      triggerDownload(blob, "live-session.txt");
    },
  };

  // ---------------------------------------------------------------
  // Utilities
  // ---------------------------------------------------------------
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function triggerDownload(blob, name) {
    const url = URL.createObjectURL(blob); const a = document.createElement("a");
    a.href = url; a.download = name; a.click(); URL.revokeObjectURL(url);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
