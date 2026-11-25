# Cloud storage — connect a person's Google Drive, OneDrive, Dropbox or other cloud files to this machine

> **Trigger:** The Primary User or a client wants their cloud files (Google Drive, OneDrive, Dropbox, S3 and the like) reached from this machine: mapped to a folder, read, reorganised, backed up, or kept in step. Load this skill before you propose anything.
> **Scope:** All agents (`all`).
> **Rule of order:** an existing tool first. `rclone` covers most cloud storage services and is the default. Writing your own integration against a provider's API is the last option and needs the Primary User's word.

> **Harness tools:** Examples use shell form. Run `rclone` through `agictl_execute` (`bash "…"`). For `agictl` commands, pass only the part after `agictl` to the matching tool. Full map: **cli_reference_agent.md**.

## 0. You do the work

You install, configure, mount and organise. The person does only what only they can do:

| Only the person can | How they do it |
|---|---|
| Change settings inside their own cloud account (for Google Drive, the one-time setup in §5) | In their own browser. You give one step per message |
| Sign in to that account | In their own browser, once |
| Approve a system package you asked for | A tap in the VersaVoice app: **Settings → Personal → Versa AGi & API → Packages & Requests** |
| Give the Chief Orchestrator Agent sudo on this machine, when root is truly needed | **Grant sudo Access** on the COA card in the VersaVoice app, or **COA Autonomous Mode** in agitop System Settings |

Never hand the person commands to run on this machine, and never ask them to install or set up something here. Your means, in this order:

1. **Your own Linux user.** rclone and FUSE are already installed (§4), and the rest needs no root: a mount in your own folder is yours, your config file is yours.
2. **A package request** for any other system software (`system_packages.md`): `agictl pkg request <name> --reason "…"`, they approve in the app, you run `agictl pkg install <name>`.
3. **COA with sudo**, when your spawn prompt says **AUTONOMOUS MODE**. If root is needed and it is off, COA asks the Primary User to switch on **Grant sudo Access**, says what for, and disarms when done. A sub-agent that needs root asks COA.

## 1. Find out what they want first

Do not start with accounts and keys. Ask, in plain words, only what you do not already know:

1. **Which service**, and whose account (personal, or a company's Google Workspace or Microsoft 365).
2. **What should happen to the files.** Reorganise them (rename files and folders, make folders, move things, add files)? Read or process their contents? Back them up? Keep a full copy here in step with the cloud?
3. **How much is there**, roughly, and whether other people keep changing it.
4. **Whether you may change and delete**, or only read.

Then check this machine: free disk (`df -h`), `host_class` from your spawn context, and what is already here (`which rclone fusermount3`, `rclone version`).

## 2. Choose how rclone is used

| What they want | Use | Disk used here |
|---|---|---|
| The cloud files as a folder you work in: read the whole structure, rename, move, make folders, add files | **`rclone mount`** in your own folder (§6) | None for files that are only read. A file being written is held on disk until it is uploaded |
| The same work with no folder, or where FUSE is not available | **rclone commands**: `lsf`, `lsjson`, `mkdir`, `moveto`, `move`, `copyto` | None. A rename or move inside one account is done on the provider's side |
| A full copy here that stays the same as the cloud, both ways | **`rclone bisync`** on a schedule | The whole of what is synced. rclone calls it an advanced command: read its page first |
| A one-way copy (backup) | **`rclone copy`** | What is copied |

- **Little free disk, and the work is reorganising:** the mount. Never bisync.
- **To read the whole tree at once,** `rclone lsf -R remote:` (or `lsjson -R`) into a file is quicker than walking a mount. One command may run about 2 minutes (10 as COA in Autonomous Mode): list one top-level folder at a time, or send the listing to a file in the background and read it when it is done.
- **To process a file's contents,** read it through the mount (it is streamed, not stored), or copy that one file to a temporary folder, work on it and delete the copy. Check its size against free disk first.
- **Never run `rclone sync`, `delete` or `purge`** unless the Primary User asked for exactly that: `sync` deletes on the destination whatever is not on the source.

## 3. When rclone is not the answer

Say so before you build anything, and name the alternative:

- The provider is not on rclone's list (`rclone.org/overview/`).
- They want the *contents* of Google Docs, Sheets or Slides edited, or comments, sharing rules or approvals handled. rclone renames and moves such files and exports copies of them (`docx`, `xlsx`, `pptx`); it does not edit them. That is the provider's own app or API.
- They want something to happen the instant a file changes. rclone looks at intervals.

Offer in this order: another existing tool or the provider's own app; then a script task around rclone (`script_tasks.md`); and only then an integration you write, as a project the Primary User approves.

## 4. rclone and FUSE are already on this machine

Versa AGi installs both with every install and update: rclone at a version it ships (`rclone version` shows it), and FUSE (`fuse3`) from the operating system. Nobody has to install anything.

1. **Check:** `which rclone fusermount3`.
2. **If one is missing,** the machine had no network when it was last updated, or it is older than 3.10.2. Request it yourself: `agictl pkg request rclone --reason "…"` (or `fuse3`), and after the approval `agictl pkg install <name>`. The operating system's rclone is an older version; it mounts and moves files. Tell COA, so the Primary User knows an update will bring the shipped version.
3. **Do not swap in another rclone version** on your own. If something on rclone's pages is missing in the installed one, say which, and let COA raise it with the Primary User.
4. **The Snap package of rclone cannot mount.** Do not use it.

## 5. Google Drive: one setup in the person's Google account, then you take over

rclone's shared Google sign-in is being retired: rclone's Drive page says it "will stop working during 2026" and that making your own is now required. So the person creates a **client ID** in their own Google account, once. This and the sign-in are the only parts you cannot do for them.

1. **Read the current steps first:** `agictl search read "https://rclone.org/drive/"`, section *Making your own client_id*. Google's console changes; that page is the authority, not your memory.
2. **Walk them through it one step per message,** in the Google Cloud console in their browser: make a project, switch on the **Google Drive API**, fill in the consent screen, add themselves as a user, create an OAuth client of type **Desktop app**, and **publish** the app. Left in testing, the sign-in expires after a week. rclone's page mentions no charge; if Google asks for billing details, stop and tell them before they go on.
3. **Access** (`scope`): `drive` for the work in §2, since you must see and change files that already exist. `drive.readonly` if you may only read. `drive.file` reaches only files rclone itself created, so it cannot reorganise what is already there.
4. **You create the remote here.** Your tool has no terminal for rclone's questions, so do not run the interactive `rclone config`. Use `rclone config create` with the client ID, the client secret and the scope; `rclone config create --help` shows the form for the installed version.
5. **The sign-in.** It needs a browser, and this machine has none for them. rclone's way (`rclone.org/remote_setup/`): on a computer that has a browser and rclone, they run the `rclone authorize …` command you give them, sign in, and send you the text it prints. You store it with `rclone config update <remote> token '<that text>'`. `rclone authorize --help` shows the exact form. Then prove it: `rclone lsd <remote>:`.
6. **The client secret and the token are credentials.** They belong only in your rclone config file (`rclone config file` shows where; keep it readable by you alone). Once stored, ask the person to delete the message they sent them in, and do not copy them into a task, memory, a log or a message of your own.

A company's Google Workspace also has a service-account route on rclone's Drive page.

## 5a. Other services: usually a sign-in is all they do

Google Drive is the exception. For most other services rclone's built-in sign-in still works, so **do not ask the person to create an app or a developer registration** unless that service's page says it is needed. Read `rclone.org/<service>/` before you start, then guide them the same way: one step per message.

| Service | What the person does | Worth knowing (from rclone's page) |
|---|---|---|
| Microsoft OneDrive | Signs in. The client ID is left blank | Names are not case-sensitive. A path is at most 400 characters. Deleted files go to the recycle bin. A sign-in unused for 90 days expires. Business and SharePoint accounts need rclone 1.76 or later from 1 April 2027 |
| Dropbox | Signs in. The app key is left blank | Names are not case-sensitive. More than 10,000 files in one folder limits some commands |
| Box | Signs in. The client ID is left blank | Names are not case-sensitive. A sign-in unused for 60 days expires |
| Amazon S3 and services like it | No sign-in. They give you an access key and a secret key from their provider's console | The keys are credentials (§5 step 6). There are no real folders, only names with slashes |

- The sign-in itself works as in §5 step 5: it needs a browser, so it happens on their computer.
- Their own app is worth it only when rclone's shared one is being slowed down by the provider (the service's page says how). Offer it then, not before.
- If a service's page carries a notice like Google's, that its shared sign-in is ending, treat that service as §5.

## 6. The mount

You mount as yourself, into a folder of your own. No root, and nobody else has to do anything.

- **Where:** an empty folder that already exists, outside any project's git folder (a git command would walk the whole Drive), for example `~/cloud/<remote>`. Record the folder and the remote in your memory so a later cycle and the housekeeping scan know what it is.
- **How:** `rclone mount <remote>: ~/cloud/<remote> --daemon --vfs-cache-mode writes --vfs-cache-max-size <a size that fits the free disk> --cache-dir <a folder of yours> --log-file <a log file of yours>`.
  - `--vfs-cache-mode writes`: files that are only read come straight from the provider; a file being written is held on disk first. rclone says this mode "should support all normal file system operations", which is what renaming, moving, making folders and adding files need.
  - `--daemon` puts the mount in the background, so the command returns.
- **Before every piece of work:** `mountpoint -q ~/cloud/<remote>`. If it is not mounted (a restart of the machine, or the mount ended), mount it again yourself. A script task can do that check on a schedule (`script_tasks.md`). Do not ask the Primary User to keep it mounted.
- **Stop it** with `fusermount3 -u ~/cloud/<remote>`.
- **Another Linux user has to see the folder** (a sub-agent working in COA's mount, or the Primary User's own login): that needs `--allow-other` and a system setting, which is root. Prefer giving the agent that does the work its own mount. Otherwise it is COA's job in Autonomous Mode.
- **`fusermount3: mount failed: Permission denied` on a newer Ubuntu** is an AppArmor rule; rclone's mount page has the fix, which is root: COA in Autonomous Mode, and tell the Primary User what you changed.
- **`host_class`:** on `wsl1` or `other`, FUSE may not be there. Use the commands in §2.
- **What to expect:** a change someone makes in the cloud shows in the folder within minutes, not at once. A Google Doc, Sheet or Slide shows as a file of size 0 and may not open through the mount; you can still rename and move it.

## 7. Reorganising safely

1. **Read first, change nothing.** List the structure (§2) and keep the listing in the project folder: it is the record of how things were.
2. **Write the plan as a list** of old path → new path, and show the person the pattern and a sample. Before you change more than a handful of files, get their yes. This is a question about their files, not a job you are handing them.
3. **Try it small.** With rclone commands, every `moveto` and `move` takes `--dry-run`; rclone's own advice is to test first because these can lose data. Through the mount, do one folder and check it in the listing before the rest.
4. **Work in batches**, and keep the list of what you moved so it can be put back.
5. **Google Drive allows two files with the same name in one folder.** If a listing shows doubles, tell the person; `rclone dedupe` has modes for it. Do not pick one for them.
6. **Go gently.** Drive limits requests (about 10 a second on a person's own client ID). Do not run many rclone commands at once.
7. **Delete only what they named.**

## 8. What you tell the person at the start

In a few sentences: what you understood they want; which way in §2 fits and why; the two things you need from them (for Google Drive: a one-time setup in their Google account that you will walk them through, and a sign-in); that you do the rest on this machine yourself; and what you will not do without asking (delete, or change many files at once).
