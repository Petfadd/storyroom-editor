# Push Storyroom Editor to GitHub

Target: **new public repository** named `storyroom-editor`.

Arena does not offer a GitHub connector in this workspace, and the sandbox has no GitHub token or SSH key. This package is therefore prepared locally; the final push is done from your own GitHub-authenticated machine.

## Files delivered

- `storyroom-editor-repo.zip` — the complete Git working tree, including `.git/`
- `storyroom-editor.bundle` — a portable Git bundle containing the `main` branch and commit history
- this `PUSH_TO_GITHUB.md`

No token, password, SSH key, uploaded media, voiceover, SRT, project JSON or rendered MP4 is included. One-off case-specific scripts containing private project IDs, local absolute paths, or human frame-review notes are also excluded; the reusable engine and validator are included.

---

## Option A — GitHub CLI, easiest

From a machine with `gh` installed:

```sh
cd /path/to/storyroom-editor-repo
gh auth login
gh repo create storyroom-editor --public --source=. --remote=origin --push
```

That creates the public repo and pushes `main`.

---

## Option B — GitHub web UI + HTTPS

1. Go to <https://github.com/new>.
2. Repository name: `storyroom-editor`
3. Visibility: **Public**
4. Do **not** initialize with README, `.gitignore` or a license.
5. Create repository.
6. Then run:

```sh
cd /path/to/storyroom-editor-repo
git remote add origin https://github.com/YOUR_GITHUB_USERNAME/storyroom-editor.git
git branch -M main
git push -u origin main
```

Replace `YOUR_GITHUB_USERNAME` with your GitHub username.

---

## Option C — Start from the Git bundle

If you only have `storyroom-editor.bundle`:

```sh
git clone /path/to/storyroom-editor.bundle storyroom-editor
cd storyroom-editor
git branch -M main
git remote set-url origin https://github.com/YOUR_GITHUB_USERNAME/storyroom-editor.git
git push -u origin main
```

A clone from a bundle already has an `origin` pointing at the local bundle, so `set-url` replaces that URL instead of trying to add a second `origin`.

---

## Option D — Start from the ZIP

```sh
unzip storyroom-editor-repo.zip -d storyroom-editor
cd storyroom-editor
git remote add origin https://github.com/YOUR_GITHUB_USERNAME/storyroom-editor.git
git branch -M main
git push -u origin main
```

---

## Before publishing

- Add a `LICENSE` file if you want to grant reuse rights.
- Confirm the repo is **Public**.
- Confirm `git ls-files` contains only engine/UI/docs files and no user media.
- Do not paste a GitHub token into chat. Use `gh auth login`, Git Credential Manager, or an SSH key on your own machine.

## After cloning

```sh
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open `http://localhost:8003`.
