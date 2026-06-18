const express = require('express');
const path = require('path');
const { Pool } = require('pg');

const app = express();
const PORT = process.env.PORT || 3000;
const ANTHROPIC_API_KEY = process.env.ANTHROPIC_API_KEY;

// ── Postgres ─────────────────────────────────────────────────────
let pool = null;
if (process.env.DATABASE_URL) {
  pool = new Pool({
    connectionString: process.env.DATABASE_URL,
    ssl: { rejectUnauthorized: false }
  });
} else {
  console.warn('WARNING: DATABASE_URL not set — using in-memory fallback.');
}

// In-memory fallback when no DB
const mem = { notes: [], history: [], transcripts: [] };

async function initDB() {
  if (!pool) return;
  await pool.query(`
    CREATE TABLE IF NOT EXISTS notes (
      id TEXT PRIMARY KEY,
      data JSONB NOT NULL,
      updated_at TIMESTAMP DEFAULT NOW()
    );
    CREATE TABLE IF NOT EXISTS history (
      id TEXT PRIMARY KEY,
      data JSONB NOT NULL,
      created_at TIMESTAMP DEFAULT NOW()
    );
    CREATE TABLE IF NOT EXISTS transcripts (
      id TEXT PRIMARY KEY,
      teacher_id TEXT NOT NULL,
      filename TEXT NOT NULL,
      content TEXT NOT NULL,
      size INTEGER,
      uploaded_at TIMESTAMP DEFAULT NOW()
    );
  `);
  console.log('DB tables ready.');
}

initDB().catch(e => console.error('DB init error:', e));

app.use(express.json({ limit: '100mb' }));

// Override CSP to allow fetch calls to Anthropic API
app.use((req, res, next) => {
  res.setHeader('Content-Security-Policy',
    "default-src * 'unsafe-inline' 'unsafe-eval' data: blob:;");
  next();
});

app.use(express.static(path.join(__dirname, 'public')));

// ── Health check ─────────────────────────────────────────────────
app.get('/api/health', async (req, res) => {
  const hasKey = !!ANTHROPIC_API_KEY;
  const hasDB = !!pool;
  let dbOk = false;
  if (pool) {
    try { await pool.query('SELECT 1'); dbOk = true; } catch(e) {}
  }
  res.json({ ok: true, hasApiKey: hasKey, hasDB, dbOk });
});

// ── Claude proxy ─────────────────────────────────────────────────
app.post('/api/claude', async (req, res) => {
  if (!ANTHROPIC_API_KEY) {
    return res.status(500).json({ 
      error: 'ANTHROPIC_API_KEY is not set in Railway environment variables. Go to your Railway service → Variables tab and add it.' 
    });
  }
  try {
    const body = { ...req.body, model: 'claude-sonnet-4-6' }; // always force correct model
    const response = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': ANTHROPIC_API_KEY,
        'anthropic-version': '2023-06-01'
      },
      body: JSON.stringify(body)
    });
    const data = await response.json();
    if (!response.ok) {
      console.error('Anthropic API error:', data);
      return res.status(response.status).json({ error: data.error?.message || JSON.stringify(data) });
    }
    res.json(data);
  } catch (err) {
    console.error('Claude proxy error:', err);
    res.status(500).json({ error: err.message });
  }
});

// ── Transcripts ───────────────────────────────────────────────────
app.get('/api/transcripts', async (req, res) => {
  try {
    if (pool) {
      const r = await pool.query('SELECT teacher_id, filename, content, size, uploaded_at FROM transcripts ORDER BY uploaded_at');
      res.json(r.rows);
    } else {
      res.json(mem.transcripts);
    }
  } catch(e) { res.status(500).json({ error: e.message }); }
});

app.post('/api/transcripts', async (req, res) => {
  const { teacherId, filename, content } = req.body;
  if (!teacherId || !filename || !content) return res.status(400).json({ error: 'Missing fields' });
  const id = teacherId + ':' + filename;
  try {
    if (pool) {
      await pool.query(`
        INSERT INTO transcripts (id, teacher_id, filename, content, size)
        VALUES ($1,$2,$3,$4,$5)
        ON CONFLICT (id) DO UPDATE SET content=$4, size=$5, uploaded_at=NOW()
      `, [id, teacherId, filename, content, content.length]);
    } else {
      const idx = mem.transcripts.findIndex(t => t.id === id);
      const entry = { id, teacher_id: teacherId, filename, content, size: content.length };
      if (idx >= 0) mem.transcripts[idx] = entry; else mem.transcripts.push(entry);
    }
    res.json({ ok: true });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

app.delete('/api/transcripts/:teacherId', async (req, res) => {
  try {
    if (pool) {
      await pool.query('DELETE FROM transcripts WHERE teacher_id=$1', [req.params.teacherId]);
    } else {
      mem.transcripts = mem.transcripts.filter(t => t.teacher_id !== req.params.teacherId);
    }
    res.json({ ok: true });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// ── Notes ────────────────────────────────────────────────────────
app.get('/api/notes', async (req, res) => {
  try {
    if (pool) {
      const r = await pool.query('SELECT data FROM notes ORDER BY updated_at DESC');
      res.json(r.rows.map(x => x.data));
    } else { res.json(mem.notes); }
  } catch(e) { res.status(500).json({ error: e.message }); }
});

app.post('/api/notes', async (req, res) => {
  const note = req.body;
  try {
    if (pool) {
      await pool.query(`
        INSERT INTO notes (id, data) VALUES ($1,$2)
        ON CONFLICT (id) DO UPDATE SET data=$2, updated_at=NOW()
      `, [note.id, JSON.stringify(note)]);
    } else {
      const idx = mem.notes.findIndex(n => n.id == note.id);
      if (idx >= 0) mem.notes[idx] = note; else mem.notes.unshift(note);
    }
    res.json({ ok: true });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

app.delete('/api/notes/:id', async (req, res) => {
  try {
    if (pool) { await pool.query('DELETE FROM notes WHERE id=$1', [req.params.id]); }
    else { mem.notes = mem.notes.filter(n => n.id != req.params.id); }
    res.json({ ok: true });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// ── History ───────────────────────────────────────────────────────
app.get('/api/history', async (req, res) => {
  try {
    if (pool) {
      const r = await pool.query('SELECT data FROM history ORDER BY created_at DESC LIMIT 100');
      res.json(r.rows.map(x => x.data));
    } else { res.json(mem.history.slice(0,100)); }
  } catch(e) { res.status(500).json({ error: e.message }); }
});

app.post('/api/history', async (req, res) => {
  const entry = req.body;
  try {
    if (pool) {
      await pool.query(`
        INSERT INTO history (id, data) VALUES ($1,$2)
        ON CONFLICT (id) DO UPDATE SET data=$2, created_at=NOW()
      `, [entry.id, JSON.stringify(entry)]);
    } else {
      mem.history = mem.history.filter(h => h.id != entry.id);
      mem.history.unshift(entry);
      if (mem.history.length > 100) mem.history = mem.history.slice(0,100);
    }
    res.json({ ok: true });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

app.delete('/api/history', async (req, res) => {
  try {
    if (pool) { await pool.query('DELETE FROM history'); }
    else { mem.history = []; }
    res.json({ ok: true });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

app.delete('/api/history/:id', async (req, res) => {
  try {
    if (pool) { await pool.query('DELETE FROM history WHERE id=$1', [req.params.id]); }
    else { mem.history = mem.history.filter(h => h.id != req.params.id); }
    res.json({ ok: true });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

app.listen(PORT, () => {
  console.log(`MAD Study Library running on port ${PORT}`);
  if (!ANTHROPIC_API_KEY) console.warn('WARNING: ANTHROPIC_API_KEY not set.');
  if (!process.env.DATABASE_URL) console.warn('WARNING: DATABASE_URL not set.');
});
