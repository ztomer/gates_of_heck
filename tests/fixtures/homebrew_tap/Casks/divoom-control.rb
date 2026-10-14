cask "divoom-control" do
  version "0.41.1"
  sha256 "af0a3d12eda729df1bb88976f91c893608bccc87e8eb4b4acfb86d001a84696b"

  url "https://github.com/ztomer/divoom_control/releases/download/v#{version}/Divoom-v#{version}.dmg"
  name "Divoom Control"
  desc "Control center for Divoom pixel displays (Pixoo/Tivoo/Ditoo/Timoo) over BLE+LAN"
  homepage "https://github.com/ztomer/divoom_control"

  depends_on arch: :arm64
  depends_on macos: :big_sur

  app "Divoom.app"

  postflight_steps do
    run "/usr/bin/xattr", args: ["-rd", "com.apple.quarantine", "{{appdir}}/Divoom.app"]
  end

  # A clean upgrade/uninstall must stop everything the app spawns. Homebrew runs
  # these in a fixed order (quit before script), so:
  #   1. quit the GUI by bundle id — this triggers the app's own graceful daemon
  #      shutdown (divoomd exits, releasing /tmp/divoom.sock).
  #   2. pkill the helper binaries — the menu-bar agent is spawned detached
  #      (start_new_session) and orphans to launchd when the GUI quits, so it must
  #      be killed explicitly; this also reaps a stubborn divoomd. The pattern
  #      matches only Contents/Frameworks/bin/divoom{d,-menubar}, never the main
  #      Contents/MacOS/Divoom app. `|| true` so a no-match (nothing running) is
  #      not treated as a failure.
  uninstall quit:   "com.divoom.control",
            script: {
              executable: "/bin/sh",
              args:       ["-c", "pkill -f 'Divoom.app/Contents/Frameworks/bin/divoom' || true"],
              sudo:       false,
            }

  zap trash: [
    "/tmp/divoom.sock",
    "/tmp/divoom_daemon.log",
    "~/.config/divoom-control",
  ]
end