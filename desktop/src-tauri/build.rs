fn main() {
    tauri_build::try_build(
        tauri_build::Attributes::new().app_manifest(
            // Without an app manifest command list, Tauri allows custom commands
            // from every window/webview by default. Keep the sidecar token IPC
            // behind explicit capabilities.
            tauri_build::AppManifest::new().commands(&[
                "handshake",
                "spawn_error",
                "prepare_close",
            ]),
        ),
    )
    .expect("failed to build tauri app manifest")
}
