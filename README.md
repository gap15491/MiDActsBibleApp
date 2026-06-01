# MAD Study Library

Mid-Acts Dispensational Teachers Study Library — search, compare, notes, and history across all your MAD teachers' transcripts.

## Features
- Upload transcript files per teacher
- Search across all teachers or a single teacher
- Compare mode — side-by-side answers from multiple teachers
- Persistent notes organized by teacher or topic
- Search history saved to database

## Teachers included
- David Reid (Columbus Bible Church)
- Les Feldick (Through the Bible)
- Steve Atwood (Grace Bible Church Chattanooga)
- Mark Gabert (Gracepoint Bible Church)
- Justin Johnson (Grace Ambassadors)
- Bryan Ross (Grace Life Bible Church)

---

## Deployment (GitHub + Railway)

### Step 1 — Push to GitHub

```bash
cd mad-study-library
git init
git add .
git commit -m "Initial commit — MAD Study Library"
git remote add origin https://github.com/YOUR_USERNAME/mad-study-library.git
git push -u origin main
```

### Step 2 — Create Railway project

1. Go to railway.app and log in
2. Click **New Project**
3. Select **Deploy from GitHub repo**
4. Choose your `mad-study-library` repo
5. Railway will detect Node.js and deploy automatically

### Step 3 — Add environment variables in Railway

In your Railway project dashboard:
1. Click your service
2. Go to **Variables** tab
3. Add:
   - `ANTHROPIC_API_KEY` = your Anthropic API key

### Step 4 — Add a persistent volume for the database

1. In Railway dashboard click your service
2. Go to **Volumes** tab
3. Click **Add Volume**
4. Mount path: `/app/data`
5. This keeps your notes and history safe across deploys

### Step 5 — Get your URL

Railway gives you a URL like `mad-study-library.up.railway.app`
Open it in any browser — your app is live!

---

## Local development

```bash
npm install
ANTHROPIC_API_KEY=your_key_here node server.js
```

Open http://localhost:3000

---

## How to load transcripts

Since transcripts are large files, they are uploaded per session in the browser:

1. Download transcripts from your Dropbox folder to your computer
2. Open the app in your browser
3. Select a teacher in the left panel
4. Hover over the teacher and click the upload icon
5. Navigate to your Dropbox transcript folder and select the .txt files
6. Transcripts load instantly into memory for that session

Notes and search history are saved permanently to the database.
