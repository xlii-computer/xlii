// The xlii desktop face — a logic-free Tauri host over the face server.
//
// Responsibilities (and NOTHING else — the brain stays kernel-side; the UI is
// xlii/face_assets, the same page the browser gets):
//   1. spawn the DEV-MODE sidecar: `xlii serve --face --handshake` resolved
//      from PATH, cwd = wherever this app was launched (`xlii code --tauri`
//      positions that at the project root);
//   2. read its one-line JSON handshake ({port, token, …}) from stdout;
//   3. navigate the webview to the live face URL so HTML/JS always match the
//      Python package (frozen frontendDist assets go stale across builds);
//   4. kill the sidecar when the app exits (stdin-close in the sidecar is the
//      backstop for a SIGKILL'd host).
//
// A frozen-binary sidecar (externalBin) is the DISTRIBUTION story, deferred —
// see proposals/tauri-shell.md T0 for the proven freeze.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::Mutex;

use tauri::Manager;
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

#[derive(Clone, Default, serde::Serialize, serde::Deserialize)]
struct Handshake {
    port: u16,
    token: String,
    version: String,
    #[serde(default)]
    protocol: String,
}

#[derive(Default)]
struct SidecarState {
    handshake: Mutex<Option<Handshake>>,
    error: Mutex<Option<String>>,
    child: Mutex<Option<CommandChild>>,
    closing: Mutex<bool>,
}

/// Webview polls this until the sidecar's handshake line has arrived
/// (fallback if navigate to the live URL failed).
#[tauri::command]
fn handshake(state: tauri::State<'_, SidecarState>) -> Option<Handshake> {
    state.handshake.lock().unwrap().clone()
}

/// Set when the sidecar could not be spawned (e.g. xlii not on PATH) — the
/// page shows the dry message instead of retrying forever.
#[tauri::command]
fn spawn_error(state: tauri::State<'_, SidecarState>) -> Option<String> {
    state.error.lock().unwrap().clone()
}

/// Face calls this after a successful graceful exit (or when giving up) so the
/// next `window.close()` is not intercepted by CloseRequested → /exit again.
#[tauri::command]
fn prepare_close(state: tauri::State<'_, SidecarState>) {
    *state.closing.lock().unwrap() = true;
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(SidecarState::default())
        .setup(|app| {
            let cmd = app
                .shell()
                .command("xlii")
                .args(["serve", "--face", "--handshake"]);
            match cmd.spawn() {
                Ok((mut rx, child)) => {
                    *app.state::<SidecarState>().child.lock().unwrap() = Some(child);
                    let handle = app.handle().clone();
                    tauri::async_runtime::spawn(async move {
                        let mut last_err = String::new();
                        while let Some(event) = rx.recv().await {
                            match event {
                                CommandEvent::Stderr(line) => {
                                    let text = String::from_utf8_lossy(&line);
                                    eprintln!("sidecar: {text}");
                                    let t = text.trim();
                                    if !t.is_empty() {
                                        last_err = t.to_string();
                                    }
                                }
                                CommandEvent::Stdout(line) => {
                                    let text = String::from_utf8_lossy(&line);
                                    if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text)
                                    {
                                        if let Some(err) =
                                            v.get("error").and_then(|e| e.as_str())
                                        {
                                            *handle
                                                .state::<SidecarState>()
                                                .error
                                                .lock()
                                                .unwrap() = Some(err.to_string());
                                            break;
                                        }
                                    }
                                    match serde_json::from_str::<Handshake>(&text) {
                                        Ok(hs) => {
                                            let face_url = format!(
                                                "http://127.0.0.1:{}/?token={}",
                                                hs.port, hs.token
                                            );
                                            *handle
                                                .state::<SidecarState>()
                                                .handshake
                                                .lock()
                                                .unwrap() = Some(hs);
                                            // Live face assets from the Python
                                            // package — not the copy frozen into
                                            // this binary at cargo build time.
                                            if let Some(win) = handle.get_webview_window("main") {
                                                match face_url.parse::<url::Url>() {
                                                    Ok(u) => {
                                                        if let Err(e) = win.navigate(u) {
                                                            eprintln!(
                                                                "sidecar: navigate to live face failed: {e}"
                                                            );
                                                        }
                                                    }
                                                    Err(e) => eprintln!(
                                                        "sidecar: bad face url {face_url}: {e}"
                                                    ),
                                                }
                                            }
                                            break; // the handshake is the only stdout line
                                        }
                                        Err(err) => eprintln!(
                                            "sidecar: non-handshake stdout line ({err}): {text}"
                                        ),
                                    }
                                }
                                CommandEvent::Terminated(_) => {
                                    let hs = handle
                                        .state::<SidecarState>()
                                        .handshake
                                        .lock()
                                        .unwrap()
                                        .is_some();
                                    let already = handle
                                        .state::<SidecarState>()
                                        .error
                                        .lock()
                                        .unwrap()
                                        .is_some();
                                    if !hs && !already {
                                        let msg = if last_err.is_empty() {
                                            "xlii serve --face exited before handshake".to_string()
                                        } else {
                                            last_err.clone()
                                        };
                                        *handle
                                            .state::<SidecarState>()
                                            .error
                                            .lock()
                                            .unwrap() = Some(msg);
                                    }
                                    break;
                                }
                                CommandEvent::Error(err) => {
                                    eprintln!("sidecar: {err}");
                                    last_err = err;
                                }
                                _ => {}
                            }
                        }
                    });
                }
                Err(err) => {
                    *app.state::<SidecarState>().error.lock().unwrap() =
                        Some(format!("could not start xlii (is it on PATH?): {err}"));
                }
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![handshake, spawn_error, prepare_close])
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app, event| match event {
            tauri::RunEvent::WindowEvent {
                event: tauri::WindowEvent::CloseRequested { api, .. },
                ..
            } => {
                let state = app.state::<SidecarState>();
                let mut closing = state.closing.lock().unwrap();
                if !*closing {
                    // First OS / chrome close: hold the window and ask the face
                    // to run the same /exit path as the menu. A later close after
                    // prepare_close (session_end) or a second OS close proceeds.
                    *closing = true;
                    api.prevent_close();
                    if let Some(win) = app.get_webview_window("main") {
                        let _ = win.eval(
                            "window.__xliiRequestExit && window.__xliiRequestExit()",
                        );
                    }
                }
            }
            tauri::RunEvent::Exit => {
                if let Some(child) = app
                    .state::<SidecarState>()
                    .child
                    .lock()
                    .unwrap()
                    .take()
                {
                    let _ = child.kill();
                }
            }
            _ => {}
        });
}
