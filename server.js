const express = require('express');
const path = require('path');
const fs = require('fs');

const app = express();
const PORT = process.env.PORT || 3000;
const ANTHROPIC_API_KEY = process.env.ANTHROPIC_API_KEY;

const DATA_DIR = process.env.DATA_DIR || path.join(__dirname, 'data');
if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });

const DB_PATH = path.join(DATA_DIR, 'db.json');

function readDB() {
  try {
    if (fs.existsSync(DB_PATH)) {
      return JSON.parse(fs.readFileSync(DB_PATH, 'utf8'));
    }
  } catch(e) {}
  return { notes: [], history: [] };
}

function writeDB(data) {
  fs.writeFileSync(DB_PATH, JSON.stringify(data, null, 2));
}

app.use(express.json({ limit: '50mb' }));
app.use(express.static(path.join(__dirname, 'public')));

app.post('/api/claude', async (req, res) => {
  if (!ANTHROPIC_API_KEY) {
    return res.status(500).json({ error: 'ANTHROPIC_API_KEY not set in Railway environment variables.' });
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

app.get('/api/notes', (req, res) => {
  const db = readDB();
  res.json(db.notes);
});

app.post('/api/notes', (req, res) => {
  const db = readDB();
  const idx = db.notes.findIndex(n => n.id == req.body.id);
  if (idx >= 0) db.notes[idx] = req.body;
  else db.notes.unshift(req.body);
  writeDB(db);
  res.json({ ok: true });
});

app.delete('/api/notes/:id', (req, res) => {
  const db = readDB();
  db.notes = db.notes.filter(n => n.id != req.params.id);
  writeDB(db);
  res.json({ ok: true });
});

app.get('/api/history', (req, res) => {
  const db = readDB();
  res.json(db.history.slice(0, 100));
});

app.post('/api/history', (req, res) => {
  const db = readDB();
  db.history = db.history.filter(h => h.id != req.body.id);
  db.history.unshift(req.body);
  if (db.history.length > 100) db.history = db.history.slice(0, 100);
  writeDB(db);
  res.json({ ok: true });
});

app.delete('/api/history', (req, res) => {
  const db = readDB();
  db.history = [];
  writeDB(db);
  res.json({ ok: true });
});

app.delete('/api/history/:id', (req, res) => {
  const db = readDB();
  db.history = db.history.filter(h => h.id != req.params.id);
  writeDB(db);
  res.json({ ok: true });
});

app.listen(PORT, () => {
  console.log(`MAD Study Library running on port ${PORT}`);
  if (!ANTHROPIC_API_KEY) console.warn('WARNING: ANTHROPIC_API_KEY not set.');
});
