# Instagram Auto-Poster — "AI News Daily"

This collects AI news, uses free Gemini to pick and summarize the top stories, renders branded
1080×1350 slides and publishes an Instagram carousel every day, plus a weekly recap.
It runs on GitHub Actions, so it costs **$0** and your PC can be off.

```
RSS + Hacker News → dedupe → Gemini (free) → HTML slides → GitHub-hosted JPEGs → Instagram API → history.json
```

## Setup (one time)

### 1. Instagram account
1. Create the channel's Instagram account.
2. Go to **Settings → Account type and tools → Switch to professional account**. Choose **Creator** or **Business**.

### 2. Gemini API key (free)
1. Go to https://aistudio.google.com → **Get API key** → **Create API key**.
2. Copy it somewhere safe. You'll paste it into GitHub in step 5.

### 2b. Cloudflare Workers AI (free) — slide background images
1. Sign up at https://dash.cloudflare.com/sign-up. It's free and no card is needed.
2. **Account ID:** in the dashboard, open **Workers AI** (under AI in the left menu). On the "Use REST API" panel, copy the **Account ID**. You can also find it via the account menu → **Account home** → the ⋯ next to your account → **Copy account ID**.
3. **API token:** on the same "Use REST API" panel, click **Create a Workers AI API Token** → **Create API Token**, and copy the token. It's shown only once.
4. Put both in `.env` (`CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`) and later in GitHub Secrets.

### 3. Meta developer app (gets your Instagram token)
1. Go to https://developers.facebook.com → **My Apps → Create App** → use case **"Manage messaging & content on Instagram"** (or "Other" → Business).
2. In the app: **Instagram → API setup with Instagram login**.
   - **Generate access tokens → Add account**, then log in to your channel account and approve.
   - Make sure the permissions include `instagram_business_basic` and `instagram_business_content_publish`.
3. Copy the generated token. Tokens made here are long-lived (60 days), and the refresh workflow keeps renewing them.
4. The app can stay in **Development mode**. Publishing to your own account works without App Review.

### 4. GitHub repository
1. Create a free GitHub account and a **new public repository** (e.g. `insta-automation`).
   - It must be public so Instagram can download the images and Actions minutes are unlimited.
   - Your keys stay private as Secrets.
2. Upload this folder's contents, either:
   - with Git: `git init`, `git add .`, `git commit`, `git push`, or
   - with **GitHub Desktop** (easiest on Windows).

### 5. Secrets
In the repository, go to **Settings → Secrets and variables → Actions → New repository secret** and add:

| Name | Value |
|---|---|
| `GEMINI_API_KEY` | from step 2 |
| `CLOUDFLARE_ACCOUNT_ID` | from step 2b |
| `CLOUDFLARE_API_TOKEN` | from step 2b |
| `IG_ACCESS_TOKEN` | from step 3 |
| `IG_USER_ID` | your Instagram professional account ID (see "Find your IG_USER_ID" below) |
| `GH_PAT` | GitHub → Settings → Developer settings → Fine-grained tokens → this repo only → permission **Secrets: Read and write** |

**Find your IG_USER_ID:** run the workflow "Post to Instagram" in dry-run mode once. If the ID is wrong, publishing will fail with a clear error. You can also run locally: `python -m engine.instagram whoami`.

### 6. Personalize
- Edit `channels/ai-news/config.yaml`:
  - `name`, `handle`, `timezone`, brand colors, hashtags, news sources.
- To change posting times, edit `publish_time` in config.yaml *and* the two `cron:` lines in `.github/workflows/post.yml`.
  - Cron lines are in **UTC** (IST − 5:30) and should start about 40 minutes before the publish time.
  - If you change the weekly cron, also change the matching line further down in that file.

## Test, then go live
1. On GitHub, open **Actions → Post to Instagram → Run workflow**, leave **dry run** ticked and click Run.
2. When it finishes, open the run and download the **slides-…** artifact to see the images and `caption.txt`.
3. Happy with them? Run it again with dry run **unticked**. That makes a real post.
4. After that, it posts automatically on schedule. Do the same test with mode = `weekly` after a few days of daily posts.

## Running locally (optional)
Install Python 3.12+, then:
```
pip install -r requirements.txt
python -m playwright install chromium
copy .env.example .env      # then fill in GEMINI_API_KEY
python run.py --channel ai-news --mode daily --dry-run
```
Slides appear in `output/ai-news/<date>/`.

## Adding another channel later
1. Copy `channels/ai-news` to `channels/<new-name>`.
2. Change its `config.yaml` (sources, prompt tone, colors) and templates.
3. Add cron lines for it in `post.yml`.

A different Instagram account needs its own token and user ID secrets. Ask Claude to wire that up.

## Good to know
- Instagram allows 50 API posts per account per 24h and 10 images per carousel. This uses 1 post/day and at most 10 images (cover + stories + outro).
- If every Gemini model fails, that day's post is **skipped**, never padded with text copied from news sites. The GitHub run is marked failed and GitHub emails you about it.
- If image generation fails, those slides use a plain branded background and the post still goes out.
- Schedule: daily at 18:30 IST and the weekly recap on Sundays at 12:00 IST. Jobs start early and wait for the exact time (`publish_time` in config.yaml).
- Each run checks `history.json`, so it never posts twice in a day and never repeats a story.
- GitHub may run scheduled jobs 5–15 minutes late.

## Video slides, stock footage and press clips
- **Video slides** (`video:` in config.yaml): the cover and every story slide become 6-second clips (background slowly zooms, text slides in); the closing slide stays a photo.
- **Stock footage**: for each story Gemini writes a search phrase; the system tries **NASA** (space stories, no key), then **Pexels** and **Pixabay** (free keys `PEXELS_API_KEY`, `PIXABAY_API_KEY`). A found clip becomes that slide's moving background; otherwise the AI image is animated. Credits are added to the caption.
- **Two daily stages**:
  - **14:05 IST draft**: today's stories are picked and saved to `channels/ai-news/drafts/<date>/`. A GitHub Issue (emailed to you) lists them, with links to official company newsrooms.
  - **Optional press clip**: upload `press-N.mp4` (N = story number; under 25 MB; first ~6 s used) into that drafts folder before ~17:45 IST. Use only clips offered for media/editorial use.
  - **17:50 IST post**: built from the draft, published at 18:30, and the Issue is closed.
