# Skill: Computer Use — the Primary User's desktop (COA only)

> **Scope:** COA only (`coa_only`). **Tool:** `agictl_desktop`. It exists only when the Primary User runs the Versa desktop (HeteroSphere) on this machine.
>
> - **Look:** `desktop look pu` reads the desktop, every screen, with each app window summed up. `desktop look pu --window <id> [--screen N]` reads one window's controls (§1b).
> - **Picture:** `desktop see pu [--screen N]` takes a PNG picture of screens (§4a).
> - **In an app:** `desktop click pu <id>`, `desktop right-click pu <id>` and `desktop double-click pu <id>` press one of an app's controls; `desktop type pu <id> "<text>"` types into a field; `desktop key pu <keys>` presses keys (§2b).
> - **Steps:** `desktop open pu [user]`, `desktop load pu <element>`, `desktop unload pu <element>`, `desktop activate pu`, `desktop close pu` operate it the way the Primary User does. `desktop expand pu`, `desktop fullscreen pu`, `desktop restore pu` resize an open interface. `desktop minimize pu` minimizes an open app; it keeps running. `desktop back pu` goes back one step, as Escape does. `desktop press pu <key>` presses one calculator key. `desktop clip-use pu <n>`, `desktop clip-remove pu <n>`, `desktop clip-clear pu` pick, remove, or clear clipboard copies.

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
- `corner-context` (role `corner`): `state.context` is what the Primary User is in (for example Computer), `state.person` who is signed in. `corner-activity`: `state.time` and `state.activity`, what is open, loaded, or running. `state.notice` is what the Primary User was just told there, such as `Saved to Downloads: report.md`; it is empty again after ten seconds. Both are absent in full screen.
- An item with `available: false` is shown but cannot run yet. An item with `background: true` is an app that is running but not in this screen's element box (minimized, in a layout's slot, or on another screen), or the collection that holds one (Apps). A hosted app (Terminal, VS Code, Cursor, AntiGravity) may have `installed: true` or `false` after the first load; it is listed either way. The first load says "Analysing"; if the program is missing the sphere turns red and the prompt is "Tap to install". Activate then is refused. Installing is not a step yet.
- `parent-computer` (role `parent`): the computer, on the bottom line, while anything else is in the sphere (an item loaded, an interface open, or one of its parts opened). Only one element is in the sphere at a time. It is not a control.
- `path` (role `path`): `state.crumbs` lists where the Primary User is, oldest first, for example `["computer", "apps"]` while Calculator is loaded from Apps. Empty when the computer itself is in the sphere with nothing loaded.
- `facts` (role `facts`): an opened part's facts, `state.facts` as `[{label, value}]`, the same values the Primary User reads on the card. They refresh every 2 seconds; a fact the machine does not have is left out.
- `calculator-display` (role `calculator`): while the calculator is open, `state.display` is the line the Primary User sees, including the operation (`2 + 3 = 5`). `desktop press pu <key>` presses one key: `0` to `9`, `dot`, `plus`, `minus`, `times`, `divide`, `equals`, `clear`, or `back`. It refuses when the calculator is not open.
- `clipboard` (role `note`): the clipboard at the top center of the primary screen. `state.count` is how many copies it keeps (up to 25, newest first; they are still there after the next sign-in, except password copies); `state.entries` lists them as `{number, text, image, size, current, password, kept, ticked}`, where `current` is the one Ctrl+V pastes in every app. It is number 1 unless a ticked entry is the paste (a ticked entry stays in its place), and none is current right after sign-in or when a run of ticked entries has ended with nothing left. `state.text` is the current copy's text. A password manager's copy is listed with `password: true`, `kept: false` and no text; you never see what it holds. `ticked` entries are the ones the Primary User has chosen for the tab's "Auto Clear" and "Next After Paste" switches (`clipboard-auto-clear`, `clipboard-next-after-paste`, role `switch`): after such an entry is pasted it is removed, or the next ticked one below becomes the paste, or both. Do not tick entries or change those switches unless the Primary User asks. `desktop clip-use pu <n>` makes copy `n` current (it moves to the top as 1); `desktop clip-remove pu <n>` removes it (removing the current one makes the next current); `desktop clip-clear pu` empties the clipboard. To see an image copy, take a picture while the Primary User has the clipboard's Images tab open. While the Primary User has the clipboard open, `clip-<n>` (role `list item`), `clip-<n>-tick` (role `check box`, `state.checked`), `clip-<n>-link` (`state.kept`) and `clip-<n>-remove` are its rows.
- `window` (role `window`): while a hosted program (Terminal, VS Code) is open, its window. `state.app_id` is the program; `state.fullscreen` whether its box is full screen. `state.interior` is `atspi` when the app's own controls are listed under this node, `partial` when that list was cut short, and `none` when the app published nothing. An empty window is not an empty app. You open and close it with `load`, `activate`, and `back`; you click in it with `click`, `right-click` and `double-click` (§2b), and type with `type` and `key` (§2c). A minimized program's window is not in the tree.
- Under a `window`, when `interior` is `atspi` or `partial`: `push button`, `text`, `menu item`, `check box`, `radio button`, `toggle button`, `list item`, `label`, `link`, `heading`, `combo box`, `tab`, `tree item`, `cell`, `slider`, `spin button`, and `image`. Each has `name`, `state.focused`, `state.checked`, `state.enabled`, and bounds on the screen. Only names are in the tree, never what a field or a document says; take a picture (`see`) to read that. A password is `text` with `state.password` and the name `Password`. Some apps publish a thin tree, so a control can be missing. Some apps publish none (`interior: none`, for example Blender); a picture is the only way to see them.
- `cpu-graph`, `ram-graph` (role `meter`): beside the date, CPU and memory use. `state.percent` is now, `state.history` the last minute, one value a second, oldest first. `storage-graph`: how full the system disk is, `state.percent`. In an open CPU or RAM element box, `facts-history` is the same minute (`state.lines`: one per core for the CPU).
- `net-wifi`, `net-bluetooth`, `net-ethernet`, `net-airplane` (role `status`): after the meters, what each connection is doing now. `state.light` is `green` (connected and working), `yellow` (on, something missing: connecting, no internet, a weak signal, no Bluetooth device connected), `red` (on, not connected) or `off`; `state.words` says it in words (for example `Connected to Home`, `No cable`). For airplane mode `yellow` means it is on. `state.shown` is false when the Primary User took the symbol off the bar. A click on any of them opens Networking; they switch nothing.
- `networking` (role `form`): the open Networking box. `state.status` has `wifi`, `bluetooth` and `ethernet`, each with `light`, `words` and `readout` (the signal as `76 %`, the connected devices, the cable's speed); `state.airplane` says whether airplane mode is on. In it: `net-wifi-enabled`, `net-bluetooth-enabled`, `net-ethernet-<n>-enabled` (one for each cable device) and `net-airplane-enabled` (role `switch`, `state.on`; `state.enabled` false when it cannot be switched now); `net-wifi-status`, `net-bluetooth-status`, `net-ethernet-<n>-status` (role `label`, the same `light`, `words`, `readout`); `net-<name>-bar` switches whether that symbol shows on the top bar. Switching Wi-Fi or Ethernet off may cut the connection you are working over: do it only when the Primary User asks.
- In Networking, `net-wifi-networks` and `net-bluetooth-devices` (role `button`, `state.open`, `state.count`) open and close the two lists. A network in range is `net-network-<n>` (role `list item`; `name` is the network, `state.signal`, `state.secured`, `state.known`, `state.joined`, `state.note` such as `Wrong password`); a click joins it. A new network with a password shows `net-wifi-password`: only the Primary User types it (`state.password`), so ask them to. `net-network-<n>-forget` forgets a known network. A device is `net-device-<n>` (`state.kind`, `state.paired`, `state.connected`, `state.battery`, `state.note`); a click connects or disconnects a paired one and pairs a found one. `net-bluetooth-add` searches for new devices. While a device is being paired, `net-pair-question` (role `label`) holds what it asks: `name` is the question, `state.kind` is `confirm` or `allow` (answer with `net-pair-yes` or `net-pair-no`), `show` (the code in `state.code` is typed on the device itself) or `code` (the device's code goes in `net-pair-code`). Join, forget and pair only what the Primary User asked for.
- `super-button` (role `button`): on the top bar, the Super key as a button. `state.open` says whether the running apps overlay is open. In that overlay (`running-box`): `running-<id>` for each running app (`state.own` for Versa's own, such as Calculator), `running-<id>-quit`, its ×, and in the Windows view `running-<id>-pick`, the pickup claw (`state.enabled`). The overlay shows above the apps; they stay on screen. With nothing on screen, Apps' Have items sit in a straight line below it. While the Primary User carries a picked-up window, `picked` names it. These are the Primary User's controls; your own ways in stay `load` and `activate`.
- `box-quit` (role `button`): on a hosted app's element box, beside ⏻ (`box-close`). It asks the app to close as its own close button would; ⏻ ends it.
- Layouts on a screen: `layout-<n>` (role `layout`, `state.layout`, `state.slots`: the app in each slot). While the Primary User carries an app: `layout-bar`, `layout-<name>` (`state.active`, `state.allowed`), and `preview-slot-<i>`. An app placed in a slot is in an element box of its own: `slot-<i>` (role `slot`, `state.app`), with `slot-<i>-title`, `slot-<i>-minimize` (takes it out of the slot; it keeps running), `slot-<i>-quit`, `slot-<i>-end`. Its `window` node is still the app's window. You do not place apps yet.
- `menu` (role `menu`): a right-click menu or another app menu that is open. `state.pid` is its app. Its `menu item`s are under it, at their place on screen.
- `window-dialog` (role `window`): the program's own second window over it, such as Terminal's Preferences, while the Primary User has it open. `state.title` names it. You cannot open or close it.
- `popups` (role `button`): shown when a program opened a window nobody in the desktop asked for; `state.count` how many wait. `popup-lightbox` (role `window`): the one the Primary User is looking at over the dimmed desktop; `name` is the app, `state.title` the window's title, and its controls are under it like any `window`. Only the Primary User opens and closes these.
- When a dialog opens over another dialog, `window-dialog` is the top one, which has the keys. The one under it is `window-dialog-2`, then `window-dialog-3`.
- Before Versa Desktop is installed, the Perceptron is the introduction (`phase: "zero"`). You can look then, but not take steps.

## 1b. App windows: summary and one window

- A look sums up every app window. Each `window` keeps its `state`, plus `state.controls`, the number of controls it publishes. Only the window that has the keys (`state.activated: true`) lists its controls; an open `menu` always lists its items.
- To read another window's controls, `desktop look pu --window <id> [--screen N]`, with the window's `id` (`window`, `window-dialog`, `popup-lightbox`) and its screen (default 0). The reply has `window` (that node with its controls) and `menus` (its app's open menus), not `tree`. Use this instead of reading the whole desktop again while you work in one app.
- A window with `interior: none` carries `state.advice`: the app publishes no controls. Ask the Primary User whether you may take a picture, then `desktop see pu --screen N`; the picture asks them on screen too.

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

## 2b. Clicking in an app

- `desktop click pu <id>` clicks the centre of an app control, an `atspi-…` id from your last look or step reply, on any screen. `desktop right-click pu <id>` right-clicks it: an app opens its menu, which is the `menu` node in the reply.
- To pick from a menu, `desktop click pu <menu item id>` from that reply. Escape-like closing is not a step yet; clicking elsewhere in the app closes a menu.
- `desktop double-click pu <id>` double-clicks it: opens a file or a folder in Files, for example.
- The reply comes 0.6 s after the click, with the summed-up tree.
- Ids of app controls are numbered on each read. A click is refused when that id now names a different control, or one you have not seen: look again (`--window`) and use the new id.
- Clicks are operating: they use the same permission as steps and appear on their screen as you make them.

## 2c. Typing and keys in an app

- `desktop type pu <id> "<text>"` types into a field (role `text`, `combo box` or `spin button`), at most 2000 characters. If the field does not have the keys, it is clicked first. The text goes in at the field's cursor; you cannot read what the field already holds. To replace it: `desktop key pu ctrl+a`, then `type` (a field that has the keys is not clicked again, so the selection stays).
- **Never a password field** (`state.password`): it is refused. Only the Primary User types passwords; ask them in chat.
- A character the keyboard layout cannot type is refused, and the message names it.
- `desktop key pu <keys>` presses keys in the app window that has the keys: `enter`, `escape`, `tab`, `backspace`, `delete`, `up`, `down`, `left`, `right`, `home`, `end`, `pageup`, `pagedown`, `space`, `f1`–`f12`, with `ctrl+`, `alt+`, `shift+` (for example `ctrl+c`, `ctrl+l`, `ctrl+shift+n`). A letter or digit needs `ctrl` or `alt`; plain text is `type`.
- Refused: keys while the desktop itself has the keys (use the Perceptron's steps), anything with Super, Alt+Tab (it picks an app's page; click its tab in `pages` instead), and Ctrl+Alt with Backspace, Delete or an F-key.
- Everything you type appears on their screen as you type it. Type only what the task needs.

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
| `no_window` | No window with that id on that screen. Look again without `--window`. |
| `capture_failed` | The picture could not be taken. Use the tree, or tell them. |
