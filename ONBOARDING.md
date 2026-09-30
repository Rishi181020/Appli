# Getting started with Appli

Appli fills job applications for you and **stops before Submit**: you review every form and submit it yourself.

## 1. Get these from the project owner
- The **Supabase URL** and **publishable key** (safe to share).
- **Your login** (email and password). They create it for you in Supabase.

You also need:
- Python 3.11+ and Node 18+.
- Your own [OpenRouter](https://openrouter.ai/keys) API key with a little credit. A typical day costs cents.
- Optional: MiKTeX (Windows) or TeX Live (Mac/Linux), only if you want tailored resumes built from LaTeX.

## 2. Install and start
```bash
git clone <repo-url> appli
```
Then double-click **`start.bat`** (Windows) or run **`./start.sh`** (Mac/Linux).

1. **The first time**, it creates `.env` and opens it. Fill in the three lines at the top and save:
   ```
   SUPABASE_URL=...
   SUPABASE_PUBLISHABLE_KEY=...
   OPENROUTER_API_KEY=...
   ```
2. **Run `start.bat` again.** It installs everything (a few minutes, once) and opens http://localhost:8765.

## 3. Sign in and follow the screens (once)
1. **Your name.** Your files go in `users/<Your Name>/` on your computer.
2. **Resumes.** Add each version you use (SWE, AI, full-stack…) as a PDF.
   - If you also add its LaTeX `main.tex`, missing skills can be added to a tailored copy for you to approve.
   - A one-line description of each resume is drafted for you; edit it if it's off.
3. **Cover letter** (optional). One you've written before, used as the voice for new ones.
4. **Profile.**
   - It's read from your resume. Check each step: basics, education, experience, skills, work authorization, common answers.
   - This is the only source of facts the models use.
   - Your sponsorship and citizenship answers also decide which postings are skipped for you.
5. **Jobs.** Paste job links (one per line) or upload a CSV/Excel file. Its columns are recognized; you confirm them.

After that, every sign-in goes straight to the dashboard. Press **Run** (or tick jobs and press **Run selected**):
- A browser opens and fills forms one by one.
- Each finished form stays open in a tab for you to review and submit.

Change files later on **My files**, and your facts on **Profile**. Add jobs any time with **+ Add jobs**.

## How your data is kept separate
- Every row (jobs, answers, runs, profile) has an owner. The database only lets you read or change your own.
- The runner on your computer signs in as you, with its own session. Your password is never stored.
- Your files live in `users/<Your Name>/` and are git-ignored, along with `.env`. Never commit or share them.
- The project owner can see all data in the Supabase console (they administer it), so share accordingly.

## Troubleshooting
- **"Appli isn't running on this computer":** open the page that `start.bat` opens (http://localhost:8765), not a saved copy.
- **"Connect the runner" banner:** enter your password once. This happens the first time on a new computer.
- **System check** (on My files): tests your API key, the models and LaTeX, and tells you what to fix.
- **"The database isn't set up for several people yet":** the project owner needs to run migration 004.
