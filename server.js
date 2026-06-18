const express = require('express');
const path = require('path');
const { Pool } = require('pg');

const app = express();
const PORT = process.env.PORT || 3000;
const ANTHROPIC_API_KEY = process.env.ANTHROPIC_API_KEY;

// ── Postgres connection ──────────────────────────────────────────
const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: process.env.DATABASE_URL?.includes('railway') || 
       process.env.DATABASE_URL?.includes('neon')
    ? { rejectUnauthorized: false }
    : false
});

// ── Create tables on startup ─────────────────────────────────────
async function initDB() {
  await pool.query(`
    CREATE TABLE IF NOT EXISTS notes (
      id TEXT PRIMARY KEY,
      teacher TEXT,
      topic TEXT,
      content TEXT,
      created_at TIMESTAMP DEFAULT NOW(),
      updated_at TIMESTAMP DEFAULT NOW(),
      data JSONB
    );

    CREATE TABLE IF NOT EXISTS history (
      id TEXT PRIMARY KEY,
      query TEXT,
      teacher TEXT,
      result TEXT,
      created_at TIMESTAMP DEFAULT NOW(),
      data JSONB
    );
  `);
  console.log('Database tables ready.');
}

initDB().catch(err => console.error('DB init error:', err));

// ── Middleware ───────────────────────────────────────────────────
app.use(express.json({ limit: '50mb' }));
app.use(express.static(path.join(__dirname, 'public')));

// ── Claude proxy (unchanged) ─────────────────────────────────────
app.post('/api/claude', async (req, res) => {
  if (!ANTHROPIC_API_KEY) {
    return res.status(500).json({ error: 'ANTHROPIC_API_KEY not set.' });
  }
  try {
    const response = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': ANTHROPIC_API_KEY,
        'anthropic-version': '2023-06-01'
      },
      body: JSON.stringify(req.body)
    });
    const data = await response.json();
    res.json(data);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ── Notes API ────────────────────────────────────────────────────
app.get('/api/notes', async (req, res) => {
  try {
    const result = await pool.query(
      'SELECT data FROM notes ORDER BY updated_at DESC'
    );
    res.json(result.rows.map(r => r.data));
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.post('/api/notes', async (req, res) => {
  try {
    const note = req.body;
    await pool.query(`
      INSERT INTO notes (id, teacher, topic, content, updated_at, data)
      VALUES ($1, $2, $3, $4, NOW(), $5)
      ON CONFLICT (id) DO UPDATE
        SET teacher = $2, topic = $3, content = $4,
            updated_at = NOW(), data = $5
    `, [
      note.id,
      note.teacher || null,
      note.topic || null,
      note.content || null,
      JSON.stringify(note)
    ]);
    res.json({ ok: true });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.delete('/api/notes/:id', async (req, res) => {
  try {
    await pool.query('DELETE FROM notes WHERE id = $1', [req.params.id]);
    res.json({ ok: true });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ── History API ──────────────────────────────────────────────────
app.get('/api/history', async (req, res) => {
  try {
    const result = await pool.query(
      'SELECT data FROM history ORDER BY created_at DESC LIMIT 100'
    );
    res.json(result.rows.map(r => r.data));
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.post('/api/history', async (req, res) => {
  try {
    const entry = req.body;
    await pool.query(`
      INSERT INTO history (id, query, teacher, result, data)
      VALUES ($1, $2, $3, $4, $5)
      ON CONFLICT (id) DO UPDATE
        SET query = $2, teacher = $3, result = $4, data = $5
    `, [
      entry.id,
      entry.query || null,
      entry.teacher || null,
      entry.result || null,
      JSON.stringify(entry)
    ]);
    res.json({ ok: true });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.delete('/api/history', async (req, res) => {
  try {
    await pool.query('DELETE FROM history');
    res.json({ ok: true });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.delete('/api/history/:id', async (req, res) => {
  try {
    await pool.query('DELETE FROM history WHERE id = $1', [req.params.id]);
    res.json({ ok: true });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ── Start ────────────────────────────────────────────────────────
app.listen(PORT, () => {
  console.log(`MAD Study Library running on port ${PORT}`);
  if (!ANTHROPIC_API_KEY) console.warn('WARNING: ANTHROPIC_API_KEY not set.');
});
