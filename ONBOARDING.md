# Getting started with Appli

Appli fills job applications for you and **stops before Submit**: you review every form and submit it yourself.

## 1. What you need
- Access to the repo. The connection to the shared database is already in it (`shared.env`), and you create your own
  account in the app.

You also need:
- Python 3.11+ and Node 18+.
- Your own [OpenRouter](https://openrouter.ai/keys) API key with a little credit. A typical day costs cents.
- Optional: MiKTeX (Windows) or TeX Live (Mac/Linux), only if you want tailored resumes built from LaTeX.

## 2. Install and start
```bash
git clone <repo-url> appli
```
Then double-click **`start.bat`** (Windows) or run **`./start.sh`** (Mac/Linux).

The first time it installs everything (a few minutes) and opens http://localhost:8765. Nothing to fill in.

## 3. Create your account and follow the screens (once)
1. **Create account** on the sign-in page (email + password). Open the confirmation link Supabase emails you, then sign in.
2. **Your name.** Your files go in `users/<Your Name>/` on your computer.
3. **API key.** Paste your OpenRouter key. It's checked with OpenRouter and saved only in your folder on this computer
   (never uploaded); every run uses it. Change it later on **My files → API key**, where you can also pick other models.
4. **Resumes.** Add each version you use (SWE, AI, full-stack…) as a PDF.
   - If you also add its LaTeX `main.tex`, missing skills can be added to a tailored copy for you to approve.
   - A one-line description of each resume is drafted for you; edit it if it's off.
5. **Cover letter** (optional). One you've written before, used as the voice for new ones.
6. **Profile.**
   - It's read from your resume. Check each step: basics, education, experience, skills, work authorization, common answers.
   - This is the only source of facts the models use.
   - Your sponsorship and citizenship answers also decide which postings are skipped for you.
7. **Jobs.** Paste job links (one per line) or upload a CSV/Excel file. Its columns are recognized; you confirm them.

After that, every sign-in goes straight to the dashboard. Press **Run** (or tick jobs and press **Run selected**):
- A browser opens and fills forms one by one.
- Each finished form stays open in a tab for you to review and submit.

Change files later on **My files**, and your facts on **Profile**. Add jobs any time with **+ Add jobs**.

**Workday jobs:** save your Workday email + password once on **My files**. Appli fills each Workday page and moves on by itself;
it only stops on a page with a question it can't answer (shown as **Needs your help**), and the final Submit is always yours.

## How your data is kept separate
- Every row (jobs, answers, runs, profile) has an owner. The database only lets you read or change your own.
- The runner on your computer signs in as you, with its own session. Your Appli password is never stored.
- Your Workday password, if you save it, stays in `users/<Your Name>/workday.json` on your computer only (plain text, git-ignored).
- Your files live in `users/<Your Name>/` and are git-ignored, along with `.env`. Never commit or share them.
- The project owner can see all data in the Supabase console (they administer it), so share accordingly.

## Troubleshooting
- **"Appli isn't running on this computer":** open the page that `start.bat` opens (http://localhost:8765), not a saved copy.
- **"Connect the runner" banner:** enter your password once. This happens the first time on a new computer.
- **System check** (on My files): tests your API key, the models and LaTeX, and tells you what to fix.
- **"The database isn't set up for several people yet":** the project owner needs to run migration 004.
