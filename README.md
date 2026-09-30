# appify

Turn a local web page into a real Mac app, in one command.

![Three icons appify generated: a node graph, a green C and an amber J](docs/icons.png)

```bash
appify 4142 --name Brain --style graph
```

That builds `/Applications/Brain.app`: its own window, its own Dock icon, and a launcher that starts the page's server when you open it.

## Why

I run a lot of small tools on localhost: dashboards, a notes graph, internal UIs. Browser tabs get lost among the other tabs. [Pake](https://github.com/tw93/Pake) already wraps any web page in a small native app (about 10 MB, built with Tauri). A bare Pake build of a *local* page still has three problems:

1. **It opens on a dead page** when the local server is not running.
2. **It gets a generic icon**, because local pages rarely have a favicon.
3. **The app menu says "Pake"** where the app's own name should be.

appify fixes all three and puts the app in `/Applications`.

## What it does

| Problem | Fix |
|---|---|
| Dead page on open | A launcher script in the app bundle checks the port before the window opens. If the port is down, it starts the server and waits for it. It finds the launchd service that serves the port by itself, or you give it a start command. |
| Generic icon | It draws a macOS style icon: a letter, or a small node graph. The colour is stable per app name, or you set it. You can also pass your own image. |
| "Pake" in the menu | appify builds from a pinned copy of Pake and changes the About item to use the app's name. |
| App in a temp folder | It installs into `/Applications`. It replaces an app there only when appify built that app. |

## Install

Needs macOS, [uv](https://docs.astral.sh/uv/), Node 20+ and Rust (`brew install rust`).

```bash
git clone https://github.com/nicolavalenti/appify ~/Projects/appify
ln -s ~/Projects/appify/appify.py ~/.local/bin/appify
```

The first build installs Pake into `~/.appify/pake` and compiles Rust, which takes 2 to 5 minutes. Later builds take about 20 seconds.

## Use

```bash
appify 4142 --name Brain --style graph            # node graph icon
appify 8080 --name Jaskier --color "#e0a458"      # letter icon in your colour
appify 3000 --name CRM --icon ~/icons/crm.png     # your own icon
appify 5173 --name Notes --start-cmd "cd ~/notes && npm run dev" --wait 60
appify 4142 --name Brain -- --hide-title-bar      # anything after -- goes to Pake
```

| Option | Does |
|---|---|
| `--style letter\|graph` | Mark on the generated icon. Default: the first letter of the name. |
| `--glyph TEXT` | Text for a letter icon instead of the first letter. |
| `--color #rrggbb` | Accent colour of the generated icon. |
| `--icon FILE` | Use your own `.icns` or image. |
| `--service LABEL` | launchd job to start when the port is down. Found automatically when it runs. |
| `--start-cmd CMD` | Shell command that starts the server, for servers launchd does not manage. |
| `--wait N` | Seconds the launcher waits for the server. Default 15. |
| `--no-install` | Build only. The app stays in `~/.appify/build`. |
| `--icon-only` | Write the icon and stop, to preview it. |

If the server still does not answer after the wait, the app asks you to quit or open anyway. It never shows you a blank window without a reason.

## How it works

1. Draws the icon with Pillow and converts it with `iconutil`.
2. Finds the launchd job behind the port: the listening PID from `lsof`, matched against `launchctl list`.
3. Builds with Pake (`--targets app`), pinned to a known version. The pinned copy has its menu patched. The patch is a plain string replacement, so if a new Pake version changes that code, appify stops with an error instead of building the old menu.
4. Renames Pake's binary, puts a small bash launcher in its place, and signs the bundle again (ad hoc, the same as Pake).
5. Copies the app into `/Applications`.

## Limits

- macOS only.
- The apps are signed ad hoc. They run on the Mac that built them. On another Mac, macOS will warn about them.
- The launcher starts a server. It does not stop the server when you quit the app.
- Pages that use Google sign in can refuse to load in an embedded web view. That is a Pake and WebKit limit.

## Credit

All the packaging is [Pake](https://github.com/tw93/Pake) by Tw93 (MIT). appify is a thin layer on top of it. The Help menu of every app still links to Pake.

## License

MIT
