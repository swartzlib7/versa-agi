# Skill: Computer Use — the Primary User's desktop (COA only)

> **Scope:** COA only (`coa_only`). **Tool:** `agictl_desktop`. It exists only when the Primary User runs the Versa desktop (HeteroSphere) on this machine.
>
> - **Look:** `desktop look pu` reads the desktop, every screen.
> - **Picture:** `desktop see pu [--screen N]` takes a PNG picture of screens (§4a).
> - **Steps:** `desktop open pu [user]`, `desktop load pu <element>`, `desktop unload pu <element>`, `desktop activate pu`, `desktop close pu` operate it the way the Primary User does. `desktop expand pu`, `desktop fullscreen pu`, `desktop restore pu` resize an open interface. `desktop minimize pu` minimizes an open app; it keeps running. `desktop back pu` goes back one step, as Escape does. `desktop press pu <key>` presses one calculator key.

## 1. What a look returns

The Primary User's desktop describes itself as a tree. Each element has:

- `id`: stable; the same element keeps the same id between looks.
- `role`: what it is (`desktop`, `surface`, `perceptron`, `subject`, `do`, `have`, `guide`, …).
- `name`: what the Primary User sees it called.
- `state`: its current values, for example `{"available": true, "loaded": false}` on a `do` item.
- `bounds`: `[x, y, width, height]` across their screens, as the screens sit side by side.
- `children`

Read the state from `state`. Do not guess it from names or positions.

### Screens

The tree's root `desktop` has one `screen-<n>` child per screen (role `screen`), left to right. `name` is the screen's connector (for example `HDMI1`); `state.index` is `n`, `state.primary` is true for `screen-0`, `state.size` its pixels. Each screen's elements are under it.

- `screen-0` is the primary screen: the desktop, the Computer and User, and everything in §2. **Steps act on the primary only**, and an element id in a step means the element under `screen-0`.
- Every other screen has its own Perceptron (`phase: "screen"`), its own box, and the windows of apps the Primary User moved there. The same ids appear there; read them, but you cannot operate them.

### The Perceptron

The center of the Versa desktop is the **Perceptron**: whatever the Primary User has their attention on.

- `perceptron`: its `state` says:
  - `observed`: what is observed;
  - `context`: in which context (for example `authenticated`);
  - `open`: whether its Do and Have items are shown;
  - `load`: what is loaded into the center;
  - `armed`: a confirmation waiting for a second activation;
  - `running`: an action under way;
  - `prompt`: what the center says;
  - `message`: the last result;
  - `opened` and `size`: the interface open in the center, if any, and its size (`compact`, `expanded`, `fullscreen`);
  - `background`: the apps minimized and still running (for example `["terminal"]`);
  - `tone`: the color the Primary User sees, which says the state: `violet` before signing in, `cyan` signed in, `gold` while User is in the sphere, `silver` for Apps, `amber` while a confirmation or the administrator password waits, `red` while a missing app is loaded ("Tap to install") and for a moment after something failed.
- `interface`: the open interface; `state.sizes` lists the sizes it allows.
- `computer` (role `subject`): `inside` says whether the computer is in the sphere. Outside, it waits at the bottom of the screen and nothing is observed.
- `user` (role `subject`): User, the person acting on the system. It is not part of the computer. With the sphere empty it waits at the top of the screen; `inside` says whether it is in the sphere (`observed` is then `user`). Only one of the two is in the sphere at a time: while one is in, the other is absent from the tree. User has no Do or Have items yet.
- `do` items sit above it: what the observed element can do. `have` items sit below: what it is made of (for the computer: `cpu`, `storage`, `ram`, `gpu`, `screen`, `keyboard`, `mouse`, `apps`).
- `do-row` and `have-row` (role `row`): `state.items` lists every item in the row, `state.shown` the ones on screen now, left to right. A row shows at most 9 and scrolls; only shown items appear as their own `do` or `have` element. You can still load an item that is not shown: it scrolls into view first. While an interface is expanded, the rows hold that interface's own items instead, usually none.
- `corner-context` (role `corner`): `state.context` is what the Primary User is in (for example Computer), `state.person` who is signed in. `corner-activity`: `state.time` and `state.activity`, what is open, loaded, or running. Both are absent in full screen.
- An item with `available: false` is shown but cannot run yet. An item with `background: true` is a minimized app that is still running, or the collection that holds one (Apps). A hosted app (Terminal, VS Code, Cursor, AntiGravity) may have `installed: true` or `false` after the first load; it is listed either way. The first load says "Analysing"; if the program is missing the sphere turns red and the prompt is "Tap to install". Activate then is refused. Installing is not a step yet.
- `parent-computer` (role `parent`): the computer, on the bottom line, while anything else is in the sphere (an item loaded, an interface open, or one of its parts opened). Only one element is in the sphere at a time. It is not a control.
- `path` (role `path`): `state.crumbs` lists where the Primary User is, oldest first, for example `["computer", "apps"]` while Calculator is loaded from Apps. Empty when the computer itself is in the sphere with nothing loaded.
- `facts` (role `facts`): an opened part's facts, `state.facts` as `[{label, value}]`, the same values the Primary User reads on the card. They refresh every 2 seconds; a fact the machine does not have is left out.
- `calculator-display` (role `calculator`): while the calculator is open, `state.display` is the line the Primary User sees, including the operation (`2 + 3 = 5`). `desktop press pu <key>` presses one key: `0` to `9`, `dot`, `plus`, `minus`, `times`, `divide`, `equals`, `clear`, or `back`. It refuses when the calculator is not open.
- `window` (role `window`): while a hosted program (Terminal, VS Code) is open, its window. `state.app_id` is the program; `state.fullscreen` whether its box is full screen. `state.interior` is `atspi` when the app's own controls are listed under this node, `partial` when that list was cut short, and `none` when the app published nothing. An empty window is not an empty app. You open and close it with `load`, `activate`, and `back`. Typing into it is not a step. A minimized program's window is not in the tree.
- Under a `window`, when `interior` is `atspi` or `partial`: `push button`, `text`, `menu item`, `check box`, `list item`, and `label`. Each has `name`, `state.focused`, `state.checked`, `state.enabled`, and bounds on the screen. A password is `text` with `state.password` and the name `Password`. Its contents are never in the tree. Some apps publish a thin tree, so a control can be missing.
- `window-dialog` (role `window`): the program's own second window over it, such as Terminal's Preferences, while the Primary User has it open. `state.title` names it. You cannot open or close it.
- `popups` (role `button`): shown when a program opened a window nobody in the desktop asked for; `state.count` how many wait. `popup-lightbox` (role `window`): the one the Primary User is looking at over the dimmed desktop; `name` is the app, `state.title` the window's title, and its controls are under it like any `window`. Only the Primary User opens and closes these.
- When a dialog opens over another dialog, `window-dialog` is the top one, which has the keys. The one under it is `window-dialog-2`, then `window-dialog-3`.
- Before Versa Desktop is installed, the Perceptron is the introduction (`phase: "zero"`). You can look then, but not take steps.

## 2. Operating the desktop

Nothing runs from one step. Every action is two deliberate steps, exactly as for the Primary User:

1. `desktop open pu`: the computer moves into the sphere; its Do and Have items appear. (`desktop open pu user` moves User in instead; it is refused while the computer is in, so `desktop close pu` first.)
2. `desktop load pu <element>`: the item goes into the center. Nothing runs yet.
3. `desktop activate pu`: it runs. An item that needs confirmation (for example `reboot`) **arms** instead: `armed` is set and the center waits for confirmation. Activate again, after a moment, to confirm.

- **Interfaces:** activating an item with an interface (for example `login`) opens it in the center. `desktop expand pu` gives it most of the screen, `desktop fullscreen pu` the whole screen, `desktop restore pu` steps back down. An interface that does not allow a size refuses it.
- **Minimize:** `desktop minimize pu` closes an open app's box and leaves the app running (Terminal keeps its output, Calculator its entry); other interfaces refuse it. To bring it back, load it and activate it (the prompt says "Tap to return"). `back` on an open app ends it.
- **Parts of the computer:** load a part (for example `cpu`; the prompt says "Tap to open") and activate it. It becomes the observed element, the computer moves to the path as `parent-computer`, and its facts appear in the `facts` element. To answer a question about the hardware, read `facts`; then `desktop back pu`.
- **Back:** `desktop back pu` does what Escape does: full screen steps down a size; otherwise it closes the open interface, returns from an opened part to the computer, or clears the load. With nothing to go back from it is refused.
- **Admin password:** some actions ask the Primary User's administrator password in the center (`password-form`). Only they can type it; wait for the result, never try to answer it.
- **Changed course:** `desktop unload pu <element>` takes it back out; `desktop close pu` moves the computer (or User) back out and clears everything.
- **Each step returns the tree after the step,** so you see what changed. Read it before the next step.
- **Refusals** (`error: "refused"`) are the same ones the Primary User would get: the item is not shown, not available, cannot be loaded together with what is loaded, or the confirmation is not ready yet. `message` says why. Do not retry the same step blindly.

**When to take steps:** only when the Primary User asked for that outcome. Never reboot, shut down, log out, or switch their next start on your own judgment. Your steps appear on their screen as you take them.

## 3. Consent

- Looking, operating, and pictures are allowed separately. Looking and operating ask the Primary User on their primary screen; a picture asks on each screen it is of. Each holds for a while once allowed (15, 30, or 60 minutes, or always).
- The reply's `grant` says how long you may still look or control (`expires_at`, or `always: true`); for a picture, one entry per screen.
- They can revoke any of them at any time, a picture grant per screen. The next request then asks again.

## 4. When to look

- Only when a task needs what is on their desktop right now, or they asked you to look.
- One look per question. Do not poll.
- Tell them what you saw in plain words, not as a tree.

## 4a. Pictures

- **Prefer the tree.** Take a picture only when the tree cannot answer: what a document, an image, or a web page shows, a layout, or the Primary User asked you to see it. A picture shows everything on that screen, so ask for the screens you need (`--screen N`).
- The reply's `pictures` lists each screen: `path`, `size`, or `error` (`rejected`, `timeout`, `capture_failed`) for a screen not taken. Read a picture with `agictl_view_image <path>`; that needs a model that reads images.
- A new picture request deletes the earlier pictures. Do not copy them elsewhere.
- While the desktop is locked, pictures are refused (`locked`).

## 5. Errors

| `error` | What to do |
|---|---|
| `rejected` | They said no. Do not ask again for this request. Ask in chat if you still need it. |
| `timeout` | They did not answer in 30 seconds. Do not retry in a loop. Ask in chat whether they want you to go ahead. |
| `busy` | Another request is waiting for their answer. Do not send another. |
| `refused` | The step is not possible right now; `message` says why and `tree` shows the desktop. Adjust, or tell them. |
| `not_running` | Their Versa desktop is not running. Say so if it matters to the task. |
| `unsafe` | The desktop socket looks wrong. Do not work around it. Tell the Primary User. |
| `locked` | Their desktop is locked. Wait until they are back; do not retry in a loop. |
| `no_screen` | No such screen. `desktop look pu` lists them. |
| `capture_failed` | The picture could not be taken. Use the tree, or tell them. |
