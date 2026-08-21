// PRIVIA desktop shell.
//
// The shell is deliberately thin. It owns the window and one privileged
// capability the browser cannot provide safely: a native folder picker, so the
// user can grant PRIVIA a directory without typing a path. Every other action
// goes through the local HTTP API, where the permission engine lives.
//
// Nothing here executes commands, reads files, or talks to the network.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::Serialize;
use tauri::api::dialog::blocking::FileDialogBuilder;

#[derive(Serialize)]
struct AppInfo {
    version: String,
    api_base: String,
    platform: String,
}

/// Where the backend lives. Loopback only, never configurable to a remote host.
fn api_base() -> String {
    let port = std::env::var("PRIVIA_PORT").unwrap_or_else(|_| "8756".to_string());
    format!("http://127.0.0.1:{port}")
}

#[tauri::command]
fn app_info() -> AppInfo {
    AppInfo {
        version: env!("CARGO_PKG_VERSION").to_string(),
        api_base: api_base(),
        platform: std::env::consts::OS.to_string(),
    }
}

/// Open the OS folder picker and return the chosen path.
///
/// Choosing a folder here does not grant anything on its own: the path is sent
/// to the backend, which validates it and adds it to the allowlist. The user
/// then still has to grant `files:read` before anything is read.
#[tauri::command]
async fn pick_folder() -> Result<Option<String>, String> {
    let selection = FileDialogBuilder::new()
        .set_title("Choose a folder PRIVIA may read")
        .pick_folder();
    Ok(selection.map(|path| path.to_string_lossy().to_string()))
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![app_info, pick_folder])
        .run(tauri::generate_context!())
        .expect("PRIVIA failed to start");
}
